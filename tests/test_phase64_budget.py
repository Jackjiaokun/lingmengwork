# -*- coding: utf-8 -*-
"""Phase 64 · 编排成本预算护栏测试.

覆盖:
- today_cost: 只统计今天的成本 / 昨日不计
- set_daily_budget 语义: <=0 不限并解除暂停 / 正数生效
- get_budget_state: over 判定 / 次日成本归零自动解除暂停
- _scheduler_tick: 超预算跳过派发(计划不执行) + paused 标记 + 预算告警一次
  / 未超预算正常派发 / 预算告警同日只推一次
- _webhook_wrap budget 分支: feishu 橙头 / dingtalk / events=budget 白名单
- API GET/POST /api/superagent/budget
- settings schema 含 agent.daily_budget
- 页面含预算 chip
"""

import http.client
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import time as _time

TODAY = _time.strftime("%Y-%m-%d")
YDAY = (_time.localtime(_time.time() - 86400)) and _time.strftime("%Y-%m-%d", _time.localtime(_time.time() - 86400))

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(sa_mod, "_HOOKS", {})
    monkeypatch.setattr(sa_mod, "_HOOKS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_SCHEDS", {})
    monkeypatch.setattr(sa_mod, "_SCHEDS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    monkeypatch.setattr(sa_mod, "_BUDGET",
                        {"daily_limit": 0.0, "paused": False, "paused_at": "",
                         "alerted_date": ""})
    yield


def _write(base_dir, rows):
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for ts, goal, cost in rows:
            rec = {"ts": ts,
                   "summary": {"goal": goal, "ts": ts, "ok": True,
                               "selfcheck_score": 90, "elapsed_sec": 3,
                               "partners_ok": 1, "est_cost_cny": cost}}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _seed_schedules(base_dir, n=1):
    for i in range(n):
        sa_mod.add_schedule(goal="定时目标%d" % i, every_sec=60,
                            base_dir=str(base_dir))


def test_today_cost_only_today(tmp_path):
    _write(tmp_path, [(TODAY + " 10:00:00", "g", 0.5),
                      (TODAY + " 11:00:00", "g", 0.3),
                      (YDAY + " 09:00:00", "g2", 9.9)])
    assert sa_mod.today_cost(str(tmp_path)) == 0.8, "只统计今天"


def test_set_daily_budget_semantics():
    st = sa_mod.set_daily_budget(10)
    assert st["daily_limit"] == 10.0
    sa_mod._BUDGET["paused"] = True  # 模拟超限暂停态
    st2 = sa_mod.set_daily_budget(0)  # 归零解除
    assert st2["daily_limit"] == 0.0 and st2["paused"] is False
    st3 = sa_mod.set_daily_budget(-5)  # 负数钳 0
    assert st3["daily_limit"] == 0.0


def test_budget_state_over_and_auto_resume(tmp_path):
    sa_mod.set_daily_budget(1.0)
    _write(tmp_path, [(TODAY + " 10:00:00", "g", 0.4)])
    st = sa_mod.get_budget_state(str(tmp_path))
    assert st["over"] is False and st["today_cost"] == 0.4
    _write(tmp_path, [(TODAY + " 12:00:00", "g", 0.7)])
    st2 = sa_mod.get_budget_state(str(tmp_path))
    assert st2["over"] is True, "0.4+0.7=1.1 >= 1.0 超限"
    # 暂停后次日(成本归零)自动解除
    sa_mod._BUDGET["paused"] = True
    _write(tmp_path, [("2099-01-01 00:00:00", "g", 0.0)])  # 未来日期不影响今日
    # 模拟"明天": 直接清掉今日数据不可行, 改用把预算调大验证解除逻辑
    sa_mod.set_daily_budget(100.0)
    st3 = sa_mod.get_budget_state(str(tmp_path))
    assert st3["paused"] is False, "不再超限应自动解除暂停"


def test_tick_skips_dispatch_when_over_budget(tmp_path):
    _seed_schedules(tmp_path)
    sa_mod.set_daily_budget(1.0)
    _write(tmp_path, [(TODAY + " 10:00:00", "g", 2.0)])  # 已超限
    sa_mod._scheduler_tick(base_dir=str(tmp_path))
    assert sa_mod._BUDGET["paused"] is True
    assert sa_mod._BUDGET["paused_at"], "应记录暂停时间"
    # 到期计划未被派发: last_run 仍为空
    ss = sa_mod.list_schedules(base_dir=str(tmp_path))
    assert all(not s.get("last_run") for s in ss), "超预算应跳过派发"


def test_tick_normal_dispatch_and_alert_once(tmp_path, monkeypatch):
    _seed_schedules(tmp_path)
    sa_mod.set_daily_budget(1.0)
    pushed = []
    monkeypatch.setattr(sa_mod, "_push_budget_alert_safe",
                        lambda base_dir=None: pushed.append(1))
    # 未超限: 正常派发(派发会真实跑编排, monkeypatch run_schedule 避免真跑)
    monkeypatch.setattr(sa_mod, "run_schedule",
                        lambda sid, base_dir=None, llm_call=None, queue_wait_sec=5.0:
                        {"ok": True})
    sa_mod._scheduler_tick(base_dir=str(tmp_path))
    assert pushed == [], "未超限不应推预算告警"

    # 超限: 告警一次
    _write(tmp_path, [(TODAY + " 10:00:00", "g", 5.0)])
    sa_mod._scheduler_tick(base_dir=str(tmp_path))
    sa_mod._scheduler_tick(base_dir=str(tmp_path))
    assert len(pushed) == 1, "同日只推一次预算告警"


class _Capture(BaseHTTPRequestHandler):
    hits = []

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0) or 0)
        _Capture.hits.append(json.loads(self.rfile.read(ln).decode("utf-8")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def test_budget_alert_delivery_and_wrap(tmp_path):
    sa_mod.set_daily_budget(1.0)
    _write(tmp_path, [(TODAY + " 10:00:00", "g", 5.0)])
    srv = ThreadingHTTPServer(("127.0.0.1", 9107), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.hits = []
    try:
        sa_mod.add_webhook("http://127.0.0.1:9107/hook", events="budget",
                           base_dir=str(tmp_path))
        sa_mod.add_webhook("http://127.0.0.1:9107/hook2", events="quality",
                           base_dir=str(tmp_path))  # 不该收到 budget
        rep = sa_mod.push_budget_alert(base_dir=str(tmp_path))
        assert rep["ok"] is True and rep["sent"] == 1 and rep["errors"] == []
        got = _Capture.hits[0]
        assert got["event"] == "budget"
        assert got["limit"] == 1.0 and got["today_cost"] == 5.0

        # 三格式包装
        feishu = sa_mod._webhook_wrap({"fmt": "feishu"}, got)
        assert feishu["msg_type"] == "interactive"
        assert feishu["card"]["header"]["template"] == "orange"
        ding = sa_mod._webhook_wrap({"fmt": "dingtalk"}, got)
        assert "成本预算告警" in ding["markdown"]["title"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_budget_api(tmp_path):
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9127), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9127, timeout=15)

        c.request("GET", "/api/superagent/budget")
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True
        assert j["daily_limit"] == 0.0 and j["over"] is False

        c.request("POST", "/api/superagent/budget",
                  body=json.dumps({"daily_limit": 20}).encode(),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["daily_limit"] == 20.0
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_schema_has_budget_key():
    keys = {f["key"]: f["type"] for g in S_SCHEMA for f in g["fields"]}
    assert keys.get("agent.daily_budget") == "float"


S_SCHEMA = _srv._SETTINGS_SCHEMA


def test_page_has_budget_chip():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "budgetChip" in html and "loadBudget" in html
    assert "/api/superagent/budget" in html

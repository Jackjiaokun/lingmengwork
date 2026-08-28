"""Phase 43 · 定时编排测试.

覆盖:
- add/list/update/remove 计划 CRUD + 校验(goal 空 / every_sec<60 且无 daily / daily 格式)
- _sched_due 到期判定(never-run / 间隔 / daily / disabled)
- run_schedule 立即执行(计数/last_ok 持久化)
- _scheduler_tick 派发到期计划(后台线程执行)
- API e2e: GET 列表 / POST create(400 校验)/update/delete/run-now
- 页面含定时编排 UI
"""

import collections
import http.client
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


@pytest.fixture(autouse=True)
def clean_scheds(monkeypatch):
    """隔离进程级计划表与已加载标记。"""
    monkeypatch.setattr(sa_mod, "_SCHEDS", {})
    monkeypatch.setattr(sa_mod, "_SCHEDS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    yield


@pytest.fixture
def fast_executors(monkeypatch):
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [], "note": ""})
                         for d in ("code", "creation", "research", "ops")})


def test_schedule_crud_and_validation(tmp_path):
    with pytest.raises(ValueError):
        sa_mod.add_schedule("", base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        sa_mod.add_schedule("目标", every_sec=10, base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        sa_mod.add_schedule("目标", daily="25:00", base_dir=str(tmp_path))

    s = sa_mod.add_schedule("研究分析竞品趋势并部署监控", every_sec=3600, base_dir=str(tmp_path))
    assert s["id"] and s["enabled"] is True and s["every_sec"] == 3600
    assert [x["id"] for x in sa_mod.list_schedules(base_dir=str(tmp_path))] == [s["id"]]

    snap = sa_mod.update_schedule(s["id"], {"enabled": False, "every_sec": 120},
                                  base_dir=str(tmp_path))
    assert snap["enabled"] is False and snap["every_sec"] == 120
    assert sa_mod.update_schedule("s_nope", {"enabled": True}, base_dir=str(tmp_path)) is None

    assert sa_mod.remove_schedule(s["id"], base_dir=str(tmp_path)) is True
    assert sa_mod.list_schedules(base_dir=str(tmp_path)) == []

    # 持久化: 重新加载(清缓存后)仍能读到磁盘
    s2 = sa_mod.add_schedule("每日巡检", daily="08:30", base_dir=str(tmp_path))
    sa_mod._SCHEDS.clear()
    sa_mod._SCHEDS_LOADED.clear()
    ids = [x["id"] for x in sa_mod.list_schedules(base_dir=str(tmp_path))]
    assert s2["id"] in ids


def test_sched_due_logic():
    now = datetime(2026, 8, 28, 10, 0, 0)
    # 从未运行 → 立即到期
    assert sa_mod._sched_due({"goal": "x", "every_sec": 3600, "enabled": True}, now) is True
    # disabled → 不到期
    assert sa_mod._sched_due({"goal": "x", "every_sec": 3600, "enabled": False}, now) is False
    # 间隔未到 → 不到期; 已到 → 到期
    recent = {"goal": "x", "every_sec": 3600, "enabled": True,
              "last_run": (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")}
    assert sa_mod._sched_due(recent, now) is False
    stale = dict(recent, last_run=(now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"))
    assert sa_mod._sched_due(stale, now) is True
    # daily: 目标时间已过且今天没跑 → 到期; 已跑 → 不到期; 目标时间未到 → 不到期
    d1 = {"goal": "x", "daily": "09:00", "enabled": True,
          "last_run": "2026-08-27 09:00:05"}
    assert sa_mod._sched_due(d1, now) is True
    d2 = dict(d1, last_run="2026-08-28 09:00:05")
    assert sa_mod._sched_due(d2, now) is False
    d3 = {"goal": "x", "daily": "23:00", "enabled": True,
          "last_run": "2026-08-27 23:00:00"}
    assert sa_mod._sched_due(d3, now) is False


def test_run_schedule_now(tmp_path, fast_executors):
    s = sa_mod.add_schedule("研究分析竞品趋势并部署监控", every_sec=3600, base_dir=str(tmp_path))
    rep = sa_mod.run_schedule(s["id"], base_dir=str(tmp_path), queue_wait_sec=5)
    assert rep["ok"] is True
    entry = [x for x in sa_mod.list_schedules(base_dir=str(tmp_path)) if x["id"] == s["id"]][0]
    assert entry["run_count"] == 1
    assert entry["last_ok"] is True
    assert entry["last_run"]
    assert sa_mod.run_schedule("s_nope", base_dir=str(tmp_path))["ok"] is False


def test_scheduler_tick_dispatches_due(tmp_path, fast_executors):
    s = sa_mod.add_schedule("研究分析竞品趋势并部署监控", every_sec=60, base_dir=str(tmp_path))
    # 回拨 last_run 两小时 → 必然到期
    sa_mod.update_schedule(s["id"], {}, base_dir=str(tmp_path))
    with sa_mod._SCHEDS_LOCK:
        sa_mod._SCHEDS[s["id"]]["last_run"] = \
            (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    sa_mod._save_scheds(str(tmp_path))
    sa_mod._scheduler_tick(base_dir=str(tmp_path))
    deadline = time.time() + 10
    while time.time() < deadline:
        entry = [x for x in sa_mod.list_schedules(base_dir=str(tmp_path)) if x["id"] == s["id"]][0]
        if entry["run_count"] >= 1:
            break
        time.sleep(0.2)
    entry = [x for x in sa_mod.list_schedules(base_dir=str(tmp_path)) if x["id"] == s["id"]][0]
    assert entry["run_count"] == 1, "tick 应派发到期计划"
    assert entry["last_ok"] is True


def test_api_schedules_e2e(monkeypatch, fast_executors):
    """API e2e: create(含 400 校验)/list/update/delete/run-now。"""
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 8997), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def req(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", 8997, timeout=30)
            c.request(method, path,
                      body=json.dumps(body or {}).encode() if method == "POST" else None,
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, json.loads(r.read().decode())

        # 缺 every_sec/daily → 400
        st, resp = req("POST", "/api/superagent/schedules/create", {"goal": "x"})
        assert st == 400

        st, resp = req("POST", "/api/superagent/schedules/create",
                       {"goal": "研究分析竞品趋势并部署监控", "every_sec": 3600})
        assert st == 200 and resp["ok"] is True
        sid = resp["schedule"]["id"]

        st, resp = req("GET", "/api/superagent/schedules")
        assert st == 200 and len(resp["schedules"]) == 1

        st, resp = req("POST", "/api/superagent/schedules/update",
                       {"id": sid, "enabled": False})
        assert st == 200 and resp["schedule"]["enabled"] is False

        st, resp = req("POST", "/api/superagent/schedules/run", {"id": sid})
        assert st == 200 and resp["ok"] is True and resp["goal_ok"] is True

        st, resp = req("POST", "/api/superagent/schedules/delete", {"id": sid})
        assert st == 200 and resp["removed"] == sid
        st, resp = req("POST", "/api/superagent/schedules/delete", {"id": sid})
        assert st == 404
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_scheduler_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "定时编排" in html
    assert "createSchedule" in html and "loadSchedules" in html
    assert "schedGoal" in html

# -*- coding: utf-8 -*-
"""Phase 62 · 告警静默期 + 调度器自动推送测试.

覆盖:
- push_quality_alerts(silence_hours>0): 同 goal 首推送达并记账 / 静默期内再推被跳过
- silence_hours=0 手动强推可重复发送 / sent==0 不记账(不吞告警)
- per-goal 隔离: goal B 不受 goal A 静默影响
- 静默状态落盘重启后仍生效
- set_quality_auto 语义: interval<=0 强制关 / silence_h 记忆
- _maybe_auto_quality: disabled / 间隔未到 / 到达间隔触发(真实送达+状态更新)
- _scheduler_tick 集成自动推送钩子
- API: GET/POST /quality/auto + POST /quality/push 透传 silence_hours
- 页面含自动推送 UI
"""

import http.client
import json
import os
import threading
import time
TODAY = time.strftime("%Y-%m-%d")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(sa_mod, "_HOOKS", {})
    monkeypatch.setattr(sa_mod, "_HOOKS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    monkeypatch.setattr(sa_mod, "_QUALITY_AUTO",
                        {"enabled": False, "interval_h": 24.0, "silence_h": 24.0,
                         "last_push_epoch": 0.0, "silence": {}})
    yield


def _write(base_dir, rows):
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for ts, goal, score, elapsed, pok in rows:
            rec = {"ts": ts,
                   "summary": {"goal": goal, "ts": ts, "ok": score >= 60,
                               "selfcheck_score": score, "elapsed_sec": elapsed,
                               "partners_ok": pok}}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _seed(tmp_path, goal="同一目标"):
    rows = [(f"{TODAY} {h:02d}:00:00", goal, 90, 3.0, 2) for h in (8, 9, 10)]
    rows.append((TODAY + " 13:00:00", goal, 40, 3.0, 2))
    _write(tmp_path, rows)


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


def test_silence_dedup_and_force(tmp_path):
    _seed(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 9103), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.hits = []
    try:
        sa_mod.add_webhook("http://127.0.0.1:9103/hook", events="quality",
                           base_dir=str(tmp_path))
        # 首推: 送达并记账
        r1 = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30,
                                        silence_hours=24)
        assert r1["alerts"] == 1 and r1["sent"] == 1 and r1["silenced"] == 0
        assert len(_Capture.hits) == 1

        # 静默期内再推: 被跳过
        r2 = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30,
                                        silence_hours=24)
        assert r2["alerts"] == 0 and r2["silenced"] == 1 and r2["skipped"] is True
        assert len(_Capture.hits) == 1, "静默期内不应再发"

        # silence_hours=0 手动强推: 可重复发送
        r3 = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30,
                                        silence_hours=0)
        assert r3["alerts"] == 1 and r3["sent"] == 1
        assert len(_Capture.hits) == 2
    finally:
        srv.shutdown()
        srv.server_close()


def test_silence_not_recorded_when_no_receiver(tmp_path):
    """没送出去(sent==0)不记账 —— 否则告警会被静默"吞"掉。"""
    _seed(tmp_path)
    r1 = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30,
                                    silence_hours=24)
    assert r1["sent"] == 0
    r2 = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30,
                                    silence_hours=24)
    assert r2["alerts"] == 1 and r2["silenced"] == 0, "未送达不应进入静默"


def test_silence_per_goal_isolation(tmp_path):
    _seed(tmp_path, goal="目标A")
    rows = [(f"{TODAY} {h:02d}:00:00", "目标B", 90, 3.0, 2) for h in (8, 9, 10)]
    rows.append((TODAY + " 13:00:00", "目标B", 40, 3.0, 2))
    _write(tmp_path, rows)
    srv = ThreadingHTTPServer(("127.0.0.1", 9104), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.hits = []
    try:
        sa_mod.add_webhook("http://127.0.0.1:9104/hook", events="quality",
                           base_dir=str(tmp_path))
        r1 = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30,
                                        silence_hours=24)
        assert r1["alerts"] == 2 and r1["sent"] == 1, "两 goal 各一条, 一次推送带走"
        # 两个 goal 都进静默
        r2 = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30,
                                        silence_hours=24)
        assert r2["silenced"] == 2 and r2["alerts"] == 0
    finally:
        srv.shutdown()
        srv.server_close()


def test_silence_state_persists(tmp_path):
    _seed(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 9105), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.hits = []
    try:
        sa_mod.add_webhook("http://127.0.0.1:9105/hook", events="quality",
                           base_dir=str(tmp_path))
        sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30,
                                   silence_hours=24)
        # 模拟重启: 清空内存态
        monkey_state = {"enabled": False, "interval_h": 24.0, "silence_h": 24.0,
                        "last_push_epoch": 0.0, "silence": {}}
        orig = sa_mod._QUALITY_AUTO
        for k in list(orig.keys()):
            orig[k] = monkey_state.get(k)
        r = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30,
                                       silence_hours=24)
        assert r["silenced"] == 1 and r["alerts"] == 0, "重启后静默仍生效(落盘)"
    finally:
        srv.shutdown()
        srv.server_close()


def test_set_quality_auto_semantics():
    st = sa_mod.set_quality_auto(True, 24, 24)
    assert st["enabled"] is True and st["interval_h"] == 24.0
    st2 = sa_mod.set_quality_auto(True, 0)       # interval<=0 强制关
    assert st2["enabled"] is False
    st3 = sa_mod.set_quality_auto(True, -5)
    assert st3["enabled"] is False
    st4 = sa_mod.set_quality_auto(False, 24, 8)
    assert st4["enabled"] is False and st4["silence_h"] == 8.0


def test_maybe_auto_quality_paths(tmp_path):
    # disabled -> no-op
    sa_mod.set_quality_auto(False, 24)
    assert sa_mod._maybe_auto_quality(base_dir=str(tmp_path))["fired"] is False

    # enabled 但间隔未到
    sa_mod.set_quality_auto(True, 24)
    sa_mod._QUALITY_AUTO["last_push_epoch"] = time.time()
    assert sa_mod._maybe_auto_quality(base_dir=str(tmp_path))["fired"] is False

    # 到达间隔 -> 派发(无接收端, 静默执行)
    sa_mod._QUALITY_AUTO["last_push_epoch"] = 0.0
    rep = sa_mod._maybe_auto_quality(base_dir=str(tmp_path))
    assert rep["fired"] is True
    assert sa_mod._QUALITY_AUTO["last_push_epoch"] > 0, "派发前应更新时间戳防重复"
    st_path = sa_mod._quality_state_path(str(tmp_path))
    assert os.path.isfile(st_path), "自动推送状态应落盘"

    # 再次调用: 间隔未到
    assert sa_mod._maybe_auto_quality(base_dir=str(tmp_path))["fired"] is False


def test_auto_push_real_delivery(tmp_path):
    _seed(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 9106), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.hits = []
    try:
        sa_mod.add_webhook("http://127.0.0.1:9106/hook", events="quality",
                           base_dir=str(tmp_path))
        sa_mod.set_quality_auto(True, 24, 24)
        sa_mod._QUALITY_AUTO["last_push_epoch"] = 0.0
        rep = sa_mod._maybe_auto_quality(base_dir=str(tmp_path))
        assert rep["fired"] is True
        deadline = time.time() + 5
        while not _Capture.hits and time.time() < deadline:
            time.sleep(0.05)
        assert _Capture.hits, "自动推送应真实送达"
        assert _Capture.hits[0]["event"] == "quality"
    finally:
        srv.shutdown()
        srv.server_close()


def test_scheduler_tick_hooks_auto(tmp_path, monkeypatch):
    """_scheduler_tick 应调用 _maybe_auto_quality。"""
    called = []
    monkeypatch.setattr(sa_mod, "_maybe_auto_quality",
                        lambda base_dir=None: called.append(base_dir))
    monkeypatch.setattr(sa_mod, "archive_old_artifacts",
                        lambda base_dir=None, max_age_days=30: None)
    monkeypatch.setattr(sa_mod, "_maybe_push_daily_digest",
                        lambda base_dir=None: None)
    sa_mod._scheduler_tick(base_dir=str(tmp_path))
    assert called == [str(tmp_path)]


def test_quality_auto_api_and_push_silence_param(tmp_path):
    _seed(tmp_path)
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9125), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9125, timeout=15)

        def post(path, payload):
            c.request("POST", path, body=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r, json.loads(r.read().decode())

        # GET auto
        c.request("GET", "/api/superagent/quality/auto")
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True and "enabled" in j

        # POST auto 开启
        r, j = post("/api/superagent/quality/auto",
                    {"enabled": True, "interval_h": 12, "silence_h": 6})
        assert r.status == 200 and j["ok"] is True and j["enabled"] is True
        assert j["interval_h"] == 12.0 and j["silence_h"] == 6.0

        # push 带 silence_hours(无接收端, sent=0 不记账, 但参数生效不报错)
        r, j = post("/api/superagent/quality/push", {"days": 30, "silence_hours": 24})
        assert r.status == 200 and j["ok"] is True and j["alerts"] == 1
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_auto_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    for token in ("qualityAutoBtn", "loadQualityAuto", "toggleQualityAuto",
                  "/api/superagent/quality/auto"):
        assert token in html, "页面缺: " + token

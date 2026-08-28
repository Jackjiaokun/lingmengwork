"""Phase 52 · 编排失败自动重试(指数退避)测试.

覆盖:
- set_default_retry_max 默认注入
- run_with_retry: 失败→退避重试→成功(retries 记录) / 重试耗尽返回最后失败 / retry_max=0 不重试
- run_schedule 按计划 retry_max 自动重试
- add_schedule/update 白名单 retry_max + API 透传
- 页面含重试输入
"""

import collections
import http.client
import json
import os
import tempfile
import threading
import time

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(sa_mod, "_HOOKS", {})
    monkeypatch.setattr(sa_mod, "_HOOKS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_SCHEDS", {})
    monkeypatch.setattr(sa_mod, "_SCHEDS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    sa_mod._DEFAULT_RETRY.update({"max": 0, "backoff": 0.0})
    yield
    sa_mod._DEFAULT_RETRY.update({"max": 0, "backoff": 5.0})


@pytest.fixture
def fast_executors(monkeypatch):
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [], "note": ""})
                         for d in ("code", "creation", "research", "ops")})


def test_set_default_retry_max():
    cfg = sa_mod.set_default_retry_max(3, backoff_sec=1.0)
    assert cfg["max"] == 3 and cfg["backoff"] == 1.0
    assert sa_mod.set_default_retry_max(-5)["max"] == 0, "负数收敛 0"
    sa_mod._DEFAULT_RETRY.update({"max": 0, "backoff": 0.0})


def test_run_with_retry_succeeds_after_failures(tmp_path, fast_executors, monkeypatch):
    """前 2 次失败第 3 次成功: retries=2, 最终 ok。"""
    calls = {"n": 0}

    def fake_run(self, goal, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"ok": False, "goal": goal, "error": "临时故障", "trace": [], "elapsed_sec": 0}
        return {"ok": True, "goal": goal, "trace": [{"stage": s, "ts": "t", "ok": True, "detail": ""}
                                                   for s in sa_mod._STAGE_NAMES], "elapsed_sec": 1}

    monkeypatch.setattr(sa_mod.SuperAgent, "run", fake_run)
    rep = sa_mod.run_with_retry("研究分析竞品趋势并部署监控", base_dir=str(tmp_path),
                                retry_max=3, backoff_base=0)
    assert rep["ok"] is True and rep["retries"] == 2 and calls["n"] == 3


def test_run_with_retry_exhausted(tmp_path, fast_executors, monkeypatch):
    """重试耗尽: 返回最后一次失败, retries=max。"""
    calls = {"n": 0}

    def fake_run(self, goal, **kw):
        calls["n"] += 1
        return {"ok": False, "goal": goal, "error": "一直失败", "trace": [], "elapsed_sec": 0}

    monkeypatch.setattr(sa_mod.SuperAgent, "run", fake_run)
    rep = sa_mod.run_with_retry("g", base_dir=str(tmp_path), retry_max=2, backoff_base=0)
    assert rep["ok"] is False and rep["retries"] == 2 and calls["n"] == 3  # 首次+2 重试


def test_run_with_retry_disabled_by_default(tmp_path, fast_executors, monkeypatch):
    """默认 retry_max=0: 失败不重试。"""
    calls = {"n": 0}

    def fake_run(self, goal, **kw):
        calls["n"] += 1
        return {"ok": False, "goal": goal, "error": "e", "trace": [], "elapsed_sec": 0}

    monkeypatch.setattr(sa_mod.SuperAgent, "run", fake_run)
    rep = sa_mod.run_with_retry("g", base_dir=str(tmp_path))  # retry_max=None → 默认 0
    assert calls["n"] == 1 and rep["retries"] == 0


def test_run_schedule_with_retry_max(tmp_path, fast_executors, monkeypatch):
    """计划 retry_max=2: 前一次失败后重试成功, run_count=1 且记录 last_retries。"""
    s = sa_mod.add_schedule("研究分析竞品趋势并部署监控", every_sec=3600,
                            retry_max=2, base_dir=str(tmp_path))
    calls = {"n": 0}

    def fake_run(self, goal, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "goal": goal, "error": "瞬态失败", "trace": [], "elapsed_sec": 0}
        return {"ok": True, "goal": goal,
                "trace": [{"stage": st, "ts": "t", "ok": True, "detail": ""}
                          for st in sa_mod._STAGE_NAMES], "elapsed_sec": 1}

    monkeypatch.setattr(sa_mod.SuperAgent, "run", fake_run)
    rep = sa_mod.run_schedule(s["id"], base_dir=str(tmp_path))
    assert rep["ok"] is True
    entry = sa_mod.list_schedules(base_dir=str(tmp_path))[0]
    assert entry["run_count"] == 1 and entry["last_ok"] is True
    assert entry.get("last_retries") == 1


def test_schedule_retry_max_fields_and_api():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9113), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def req(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", 9113, timeout=15)
            c.request(method, path,
                      body=json.dumps(body or {}).encode() if method == "POST" else None,
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, json.loads(r.read().decode())

        st, resp = req("POST", "/api/superagent/schedules/create",
                       {"goal": "研究分析竞品趋势并部署监控", "every_sec": 3600, "retry_max": 2})
        assert st == 200 and resp["schedule"]["retry_max"] == 2
        sid = resp["schedule"]["id"]

        st, resp = req("POST", "/api/superagent/schedules/update",
                       {"id": sid, "retry_max": 1})
        assert st == 200 and resp["schedule"]["retry_max"] == 1

        # API e2e run(带重试): 全程成功
        st, resp = req("POST", "/api/superagent/schedules/run", {"id": sid})
        assert st == 200 and resp["ok"] is True
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_retry_input():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "schedRetry" in html and "失败重试" in html

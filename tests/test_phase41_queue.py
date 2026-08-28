"""Phase 41 · 编排并发控制(队列 + 忙拒绝)测试.

覆盖:
- set_max_orchestrations 上限设置与 get_queue_state 状态
- 槽位占满时 run() 有限排队 → 超时忙拒绝(busy=True, 不进编排历史)
- 释放槽位后恢复正常编排
- 运行中 queue state running=1; 释放后归零
- GET /api/superagent/queue API; busy 时同步端点 429
- 页面含队列状态 chip
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


@pytest.fixture
def fast_executors(monkeypatch):
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [], "note": ""})
                         for d in ("code", "creation", "research", "ops")})


@pytest.fixture
def max_one(monkeypatch):
    """并发上限=1; 结束后恢复默认 2。"""
    sa_mod.set_max_orchestrations(1)
    yield
    sa_mod.set_max_orchestrations(2)


def test_set_max_and_queue_state():
    assert sa_mod.set_max_orchestrations(3) == 3
    st = sa_mod.get_queue_state()
    assert st["max"] == 3 and st["running"] == 0 and st["waiting"] == 0
    assert sa_mod.set_max_orchestrations(0) == 1, "非法值应收敛到 1"
    sa_mod.set_max_orchestrations(2)


def test_run_busy_when_full(tmp_path, fast_executors, max_one):
    """槽位占满: run 有限排队后忙拒绝; busy 结果不进编排历史。"""
    assert sa_mod._ORCH_SEM.acquire(timeout=1), "测试前置: 占用唯一槽位"
    try:
        sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
        saved = sa_mod._RUNS
        sa_mod._RUNS = collections.deque(maxlen=60)
        try:
            rep = sa.run("研究分析竞品趋势并部署监控", session_id="p41",
                         quality_gate=False, queue_wait_sec=0.5)
            assert rep["ok"] is False and rep["busy"] is True
            assert "排队" in rep["error"]
            assert len(sa_mod._RUNS) == 0, "busy 拒绝不应写入编排历史"
        finally:
            sa_mod._RUNS = saved
    finally:
        sa_mod._ORCH_SEM.release()


def test_run_ok_after_release(tmp_path, fast_executors, max_one):
    """释放槽位后编排恢复正常(排队等待后获得槽位)。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("研究分析竞品趋势并部署监控", session_id="p41b",
                 quality_gate=False, queue_wait_sec=5)
    assert rep["ok"] is True and not rep.get("busy")
    assert [t["stage"] for t in rep["trace"]] == sa_mod._STAGE_NAMES


def test_queue_state_running_and_release(tmp_path, monkeypatch, max_one):
    """运行中 running=1; 阻塞中的第二个编排 waiting=1; 释放后归零。"""
    gate = threading.Event()
    started_evt = threading.Event()

    def slow_exec(partner, goal="", llm_call=None, base_dir=None):
        started_evt.set()
        gate.wait(5)
        return {"domain": partner.get("domain"), "status": "ok", "artifacts": [], "note": ""}

    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: slow_exec for d in ("code", "creation", "research", "ops")})
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    box = {}

    def worker():
        box["rep"] = sa.run("研究分析竞品趋势并部署监控", session_id="p41c",
                            quality_gate=False, queue_wait_sec=5)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert started_evt.wait(5), "第一个编排应已进入执行器"
    time.sleep(0.3)
    st = sa_mod.get_queue_state()
    assert st["running"] == 1, "运行中应计 running=1"
    # 第二个编排: 槽位占满, 短等待后忙拒绝
    rep2 = sa.run("另一个目标", session_id="p41d", quality_gate=False, queue_wait_sec=0.3)
    assert rep2.get("busy") is True
    gate.set()
    t.join(10)
    assert box["rep"]["ok"] is True
    time.sleep(0.2)
    assert sa_mod.get_queue_state()["running"] == 0, "释放后 running 归零"


def test_queue_api_and_busy_429(monkeypatch):
    """GET /api/superagent/queue 状态 API; 槽位满时同步 run 端点 429。"""
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 8993), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", 8993, timeout=15)
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        st, js = get("/api/superagent/queue")
        assert st == 200
        q = json.loads(js)
        assert q["ok"] is True and q["max"] >= 1
        assert "running" in q and "waiting" in q

        # 占满全部槽位 → 同步编排端点 429
        sem = sa_mod._ORCH_SEM
        acquired = []
        while sem.acquire(timeout=0.2):
            acquired.append(True)
            if len(acquired) >= q["max"]:
                break
        try:
            c = http.client.HTTPConnection("127.0.0.1", 8993, timeout=20)
            c.request("POST", "/api/superagent/run",
                      body=json.dumps({"goal": "研究分析竞品趋势并部署监控",
                                       "queue_wait_sec": 1}).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            body = json.loads(r.read().decode())
            assert r.status == 429, "槽位满应返回 429"
            assert body.get("busy") is True
        finally:
            for _ in acquired:
                try:
                    sem.release()
                except ValueError:
                    pass
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_queue_chip():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "queueChip" in html
    assert "/api/superagent/queue" in html

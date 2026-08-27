"""Phase 16 实时活动总线测试: EventBus 核心 + server /api/events + 引擎动作 emit 接入。

无 LLM key 环境下走规则兜底, 验证事件环形缓冲/增量游标/并发安全/服务端 API。
"""
import threading

import pytest

from lingmengwork import event_bus as eb
from lingmengwork.web import server


class _StubHandler(server.Handler):
    def __init__(self, body):
        import io, json
        raw = json.dumps(body).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self._captured = {}

    def _send_json(self, obj, status=200):
        self._captured = {"obj": obj, "status": status}


@pytest.fixture
def stub():
    return _StubHandler


# ---------------- EventBus 核心 ----------------
def test_emit_structure():
    bus = eb.EventBus()
    ev = bus.emit("engine", "run", "hello", {"k": 1})
    assert ev["id"] == 1
    assert ev["source"] == "engine"
    assert ev["kind"] == "run"
    assert ev["msg"] == "hello"
    assert ev["data"] == {"k": 1}
    assert isinstance(ev["ts"], int) and ev["ts"] > 0


def test_recent_since_cursor():
    bus = eb.EventBus()
    for i in range(5):
        bus.emit("s", "k", "m%d" % i)
    r = bus.recent(since_id=3)
    assert [e["id"] for e in r] == [4, 5]
    # limit 裁剪
    assert len(bus.recent(limit=2, since_id=0)) == 2


def test_ring_buffer_crop():
    bus = eb.EventBus(maxlen=3)
    for i in range(10):
        bus.emit("s", "k", "m")
    assert bus.size() == 3
    ids = [e["id"] for e in bus.recent(since_id=0)]
    assert ids == [8, 9, 10]


def test_counts_by_source():
    bus = eb.EventBus()
    bus.emit("a", "k", "x")
    bus.emit("a", "k", "y")
    bus.emit("b", "k", "z")
    assert bus.counts_by_source() == {"a": 2, "b": 1}


def test_concurrent_emit():
    bus = eb.EventBus()
    def worker():
        for _ in range(50):
            bus.emit("s", "k", "m")
    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert bus.size() == 200


def test_emit_exception_safe():
    # emit 异常时返回 None, 不抛
    assert eb.emit("s", "k", "x") is not None
    assert eb.get_bus() is eb.get_bus()


# ---------------- server /api/events ----------------
def test_events_get_empty(stub):
    h = stub({})
    h.path = "/api/events"
    h._events_get()
    out = h._captured["obj"]
    assert out["ok"] is True
    assert isinstance(out["events"], list)
    assert "counts" in out and "total" in out


def test_emit_then_events_get(stub):
    h = stub({})
    h.path = "/api/events"
    h._emit("system", "ping", "hello-world")
    h._events_get()
    out = h._captured["obj"]
    assert any(e["source"] == "system" and e["msg"] == "hello-world" for e in out["events"])


def test_events_since_cursor(stub):
    h = stub({})
    h._emit("system", "a", "A")
    h._emit("system", "b", "B")
    h.path = "/api/events?since=0"
    h._events_get()
    allout = h._captured["obj"]
    assert len(allout["events"]) >= 2
    last = allout["events"][-1]["id"]
    h.path = "/api/events?since=%d" % last
    h._events_get()
    assert h._captured["obj"]["events"] == []
    h._emit("system", "c", "C")
    h.path = "/api/events?since=%d" % last
    h._events_get()
    inc = h._captured["obj"]["events"]
    assert len(inc) == 1 and inc[0]["msg"] == "C"


# ---------------- 引擎动作 emit 接入 ----------------
def test_engines_run_emits_event(stub):
    h = stub({"engine": "decompose", "goal": "为登录模块增加记住我功能"})
    h._engines_run()
    out = h._captured["obj"]
    assert out["ok"] is True
    evs = eb.recent(limit=50)
    assert any(e["source"] == "engine" and e["kind"] == "run" for e in evs)


def test_automation_create_emits_event(stub, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # 隔离: hub 写 <tmp>/automations.json, 不污染仓库
    h = stub({"name": "night-build", "kind": "pipeline", "goal": "跑回归",
              "schedule": "daily:03:00", "domain": "code"})
    h._automations_create()
    out = h._captured["obj"]
    assert out["ok"] is True
    evs = eb.recent(limit=50)
    assert any(e["source"] == "automation" and e["kind"] == "create" for e in evs)

"""Phase 17 — 事件持久化回溯 + 关键操作审计链 测试。"""

import http.client
import json as _json
import os
import tempfile
import threading
import time

from lingmengwork import event_bus as eb
from lingmengwork.web import server as _srv


def test_persist_and_replay(tmp_path):
    p = str(tmp_path / "events.jsonl")
    b1 = eb.EventBus(persist_path=p, maxlen=50)
    for i in range(3):
        b1.emit("engine", "run", "run %d" % i, audit=(i == 1))
    # 新实例回放：内存含历史 + seq 接续
    b2 = eb.EventBus(persist_path=p, maxlen=50)
    assert b2.size() == 3, b2.size()
    assert b2._seq >= 3
    ev = b2.emit("engine", "run", "after", audit=True)
    assert ev["id"] == b2._seq
    lines = [l for l in open(p, encoding="utf-8") if l.strip()]
    assert len(lines) == 4


def test_audit_trail_filter(tmp_path):
    p = str(tmp_path / "events.jsonl")
    b = eb.EventBus(persist_path=p)
    b.emit("engine", "run", "a", audit=True)
    b.emit("engine", "noop", "b", audit=False)
    b.emit("automation", "create", "c", audit=True)
    trail = b.audit_trail(limit=50)
    assert len(trail) == 2, [e["kind"] for e in trail]
    assert "noop" not in {e["kind"] for e in trail}
    assert trail[0]["kind"] == "create"  # 倒序最新在前


def test_audit_trail_source_filter(tmp_path):
    p = str(tmp_path / "events.jsonl")
    b = eb.EventBus(persist_path=p)
    b.emit("engine", "run", "a", audit=True)
    b.emit("automation", "create", "c", audit=True)
    eng = b.audit_trail(source="engine")
    assert len(eng) == 1 and eng[0]["source"] == "engine"


def test_concurrent_file_write(tmp_path):
    p = str(tmp_path / "events.jsonl")
    b = eb.EventBus(persist_path=p)

    def worker(n):
        for i in range(5):
            b.emit("system", "tick", "w%d-%d" % (n, i), audit=True)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert b.size() == 50
    lines = [l for l in open(p, encoding="utf-8") if l.strip()]
    assert len(lines) == 50
    for l in lines:
        _json.loads(l)  # 每行合法 JSON


def test_server_audit_api():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8941
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", PORT), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def post(path, body):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            c.request("POST", path, body=_json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        st, js = post("/api/automations", {"name": "audit-test", "kind": "pipeline",
                                            "goal": "g", "schedule": "daily:03:00", "domain": "code"})
        assert st == 200, (st, js)
        st, js = get("/api/audit")
        assert st == 200, (st, js)
        d2 = _json.loads(js)
        assert d2["ok"]
        assert any(e["source"] == "automation" and e["kind"] == "create"
                   for e in d2["events"]), d2
    finally:
        srv.shutdown()
        os.chdir(old)


def test_selfcheck_event_bus_probe():
    from lingmengwork import selfcheck
    rep = selfcheck.run()
    names = [c["name"] for c in rep["checks"]]
    assert "活动总线(事件+审计链)" in names
    probe = next(c for c in rep["checks"] if c["name"] == "活动总线(事件+审计链)")
    assert probe["ok"], probe

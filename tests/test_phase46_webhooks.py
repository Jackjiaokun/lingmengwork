"""Phase 46 · 编排结果 Webhook 通知测试.

覆盖:
- add/update/remove Webhook CRUD + url 校验
- 真实送达: 本地接收端捕获 POST(事件 JSON + HMAC 签名头) + last_status 回写
- 事件过滤: fail-only 接收端不收 done; 独立 notify_webhooks(ok=False) 收 fail
- 内核异常路径同样触发 fail 通知
- API e2e: create(400)/list/test/delete(404)
- 页面含 Webhook UI
"""

import collections
import hashlib
import hmac
import http.client
import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


@pytest.fixture(autouse=True)
def clean_hooks(monkeypatch):
    monkeypatch.setattr(sa_mod, "_HOOKS", {})
    monkeypatch.setattr(sa_mod, "_HOOKS_LOADED", set())
    yield


@pytest.fixture
def fast_executors(monkeypatch):
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [], "note": ""})
                         for d in ("code", "creation", "research", "ops")})


def test_webhook_crud_and_validation(tmp_path):
    with pytest.raises(ValueError):
        sa_mod.add_webhook("ftp://x", base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        sa_mod.add_webhook("", base_dir=str(tmp_path))

    h = sa_mod.add_webhook("https://example.com/hook", events="fail",
                           secret="s3cret", base_dir=str(tmp_path))
    assert h["id"] and h["events"] == "fail" and h["secret"] == "s3cret"
    lst = sa_mod.list_webhooks(base_dir=str(tmp_path))
    assert lst[0]["url"] == "https://example.com/hook"

    snap = sa_mod.update_webhook(h["id"], {"enabled": False, "events": "all"},
                                 base_dir=str(tmp_path))
    assert snap["enabled"] is False and snap["events"] == "all"
    with pytest.raises(ValueError):
        sa_mod.update_webhook(h["id"], {"url": "bad"}, base_dir=str(tmp_path))
    assert sa_mod.update_webhook("w_nope", {}, base_dir=str(tmp_path)) is None
    assert sa_mod.remove_webhook(h["id"], base_dir=str(tmp_path)) is True
    assert sa_mod.list_webhooks(base_dir=str(tmp_path)) == []


class _Capture(BaseHTTPRequestHandler):
    payload = {}
    headers = {}

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0) or 0)
        _Capture.payload = json.loads(self.rfile.read(ln).decode("utf-8"))
        _Capture.headers = {k.lower(): v for k, v in self.headers.items()}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def test_webhook_delivery_with_signature(tmp_path, fast_executors, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 9101), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.payload = {}
    _Capture.headers = {}
    try:
        secret = "s3cret"
        sa_mod.add_webhook("http://127.0.0.1:9101/hook", events="all",
                           secret=secret, base_dir=str(tmp_path))
        monkeypatch.setattr(sa_mod._sc, "run",
                            lambda: {"ok": True, "score": 95, "passed": 13, "total": 13,
                                     "all_ok": True, "checks": [], "ts": "t"})
        sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
        sa.run("研究分析竞品趋势并部署监控", session_id="p46", quality_gate=True)

        deadline = time.time() + 10
        while time.time() < deadline and not _Capture.payload:
            time.sleep(0.2)
        assert _Capture.payload, "接收端应捕获到推送"
        assert _Capture.payload["event"] == "done"
        assert _Capture.payload["ok"] is True
        assert _Capture.payload["goal"] == "研究分析竞品趋势并部署监控"
        assert _Capture.payload["selfcheck_score"] == 95
        # HMAC-SHA256 签名可验证
        raw = json.dumps(_Capture.payload, ensure_ascii=False).encode("utf-8")
        expect = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        assert _Capture.headers.get("x-lmw-signature") == "sha256=" + expect
        assert _Capture.headers.get("x-lmw-event") == "done"
        # last_status 回写
        h = sa_mod.list_webhooks(base_dir=str(tmp_path))[0]
        assert h["last_status"] == 200 and h["last_ts"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_webhook_event_filter(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 9102), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.payload = {}
    try:
        sa_mod.add_webhook("http://127.0.0.1:9102/fail-only", events="fail",
                           base_dir=str(tmp_path))
        # done 事件: 不应送达
        sa_mod.notify_webhooks({"ok": True, "goal": "g1", "elapsed_sec": 0,
                                "routed": [], "converge": {}, "executions": {}},
                               base_dir=str(tmp_path), blocking=True)
        assert not _Capture.payload, "fail-only 接收端不应收到 done 事件"
        # fail 事件: 送达
        sa_mod.notify_webhooks({"ok": False, "goal": "g2", "error": "boom",
                                "elapsed_sec": 1, "routed": [], "converge": {},
                                "executions": {}},
                               base_dir=str(tmp_path), blocking=True)
        assert _Capture.payload.get("event") == "fail"
        assert _Capture.payload.get("error") == "boom"
    finally:
        srv.shutdown()
        srv.server_close()


def test_kernel_exception_notifies_fail(tmp_path, fast_executors, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 9103), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.payload = {}
    try:
        sa_mod.add_webhook("http://127.0.0.1:9103/hook", base_dir=str(tmp_path))

        def _boom(self, understand, session_id="", llm_call=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(sa_mod.SuperAgent, "dispatch", _boom)
        sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
        res = sa.run("随便什么目标", session_id="p46b", quality_gate=False)
        assert res["ok"] is False
        deadline = time.time() + 10
        while time.time() < deadline and not _Capture.payload:
            time.sleep(0.2)
        assert _Capture.payload.get("event") == "fail"
        assert "boom" in _Capture.payload.get("error", "")
    finally:
        srv.shutdown()
        srv.server_close()


def test_api_webhooks_e2e():
    srv = ThreadingHTTPServer(("127.0.0.1", 9104), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    httpd = _srv.ThreadingHTTPServer(("127.0.0.1", 9105), _srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def req(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", 9105, timeout=20)
            c.request(method, path,
                      body=json.dumps(body or {}).encode() if method == "POST" else None,
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, json.loads(r.read().decode())

        st, resp = req("POST", "/api/superagent/webhooks/create", {"url": "bad"})
        assert st == 400

        st, resp = req("POST", "/api/superagent/webhooks/create",
                       {"url": "http://127.0.0.1:9104/hook", "events": "all"})
        assert st == 200
        wid = resp["webhook"]["id"]

        st, resp = req("GET", "/api/superagent/webhooks")
        assert st == 200 and len(resp["webhooks"]) == 1

        # test 端点: 真发一条 test 事件到捕获服务
        st, resp = req("POST", "/api/superagent/webhooks/test", {"id": wid})
        assert st == 200 and resp["ok"] is True and resp["status"] == 200
        assert _Capture.payload.get("event") == "test"

        st, resp = req("POST", "/api/superagent/webhooks/delete", {"id": wid})
        assert st == 200 and resp["removed"] == wid
        st, resp = req("POST", "/api/superagent/webhooks/delete", {"id": wid})
        assert st == 404
    finally:
        os.chdir(old)
        httpd.shutdown()
        httpd.server_close()
        srv.shutdown()
        srv.server_close()


def test_page_has_webhook_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "通知 Webhook" in html
    assert "createWebhook" in html and "testWebhook" in html
    assert "/api/superagent/webhooks" in html

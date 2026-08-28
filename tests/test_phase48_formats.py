"""Phase 48 · Webhook 消息格式适配测试 (raw/feishu/dingtalk) + README 开源化.

覆盖:
- _webhook_wrap 三种格式包装结构
- _webhook_text 摘要内容
- add_webhook fmt 入库/非法回退 raw / API create 透传 fmt
- 飞书格式真实送达(捕获服务校验 msg_type 结构)
- README 含开源徽章与双仓库链接
"""

import collections
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


def _sample_payload():
    return {"event": "done", "goal": "巡检服务", "ok": True, "ts": "t",
            "elapsed_sec": 3, "routed": ["research", "ops"],
            "selfcheck_score": 95, "partners_ok": 2,
            "artifacts": [], "error": ""}


def test_webhook_wrap_formats():
    payload = _sample_payload()
    # raw: 原样
    assert sa_mod._webhook_wrap({"fmt": "raw"}, payload) is payload
    # feishu
    f = sa_mod._webhook_wrap({"fmt": "feishu"}, payload)
    assert f["msg_type"] == "text"
    assert "巡检服务" in f["content"]["text"] and "✅ 成功" in f["content"]["text"]
    # dingtalk
    d = sa_mod._webhook_wrap({"fmt": "dingtalk"}, payload)
    assert d["msgtype"] == "text"
    assert "巡检服务" in d["text"]["content"] and "耗时: 3s" in d["text"]["content"]
    # 无 fmt 字段 → raw
    assert sa_mod._webhook_wrap({}, payload) is payload


def test_webhook_text_fail_event():
    text = sa_mod._webhook_text({"event": "fail", "goal": "g", "ok": False,
                                 "routed": ["code"], "elapsed_sec": 1,
                                 "error": "RuntimeError: boom"})
    assert "❌ 失败" in text and "boom" in text and "code" in text


def test_add_webhook_fmt(tmp_path):
    h = sa_mod.add_webhook("https://open.feishu.cn/x", fmt="feishu", base_dir=str(tmp_path))
    assert h["fmt"] == "feishu"
    # 非法 fmt → 回退 raw
    h2 = sa_mod.add_webhook("https://x.com/h", fmt="bogus", base_dir=str(tmp_path))
    assert h2["fmt"] == "raw"
    # update 白名单含 fmt
    snap = sa_mod.update_webhook(h["id"], {"fmt": "dingtalk"}, base_dir=str(tmp_path))
    assert snap["fmt"] == "dingtalk"


class _Capture(BaseHTTPRequestHandler):
    payload = {}

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0) or 0)
        _Capture.payload = json.loads(self.rfile.read(ln).decode("utf-8"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def test_feishu_delivery_structure(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 9107), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.payload = {}
    try:
        sa_mod.add_webhook("http://127.0.0.1:9107/hook", fmt="feishu",
                           base_dir=str(tmp_path))
        sa_mod.notify_webhooks(_sample_payload(), base_dir=str(tmp_path), blocking=True)
        assert _Capture.payload.get("msg_type") == "text", "飞书格式应包装为 msg_type 结构"
        assert "巡检服务" in _Capture.payload["content"]["text"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_api_webhook_fmt_passthrough():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9108), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9108, timeout=15)
        c.request("POST", "/api/superagent/webhooks/create",
                  body=json.dumps({"url": "https://oapi.dingtalk.com/robot/send",
                                   "fmt": "dingtalk"}).encode(),
                  headers={"Content-Type": "application/json"})
        resp = json.loads(c.getresponse().read().decode())
        assert resp["ok"] is True and resp["webhook"]["fmt"] == "dingtalk"
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_readme_open_source():
    root = os.path.dirname(_srv.__file__)           # lingmengwork/web
    readme = os.path.join(os.path.dirname(os.path.dirname(root)), "README.md")  # 项目根
    with open(readme, encoding="utf-8") as f:
        html = f.read()
    assert "License-MIT" in html, "README 应含 MIT 徽章"
    assert "github.com/Jackjiaokun/lingmengwork" in html
    assert "gitee.com/jackjiaokun/lingmengwork" in html
    assert "灵梦AI团队" in html

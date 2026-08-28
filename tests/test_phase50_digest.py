"""Phase 50 · 编排日报/周报测试.

覆盖:
- _digest_stats 聚合(总数/成功/成功率/平均/成本/recent) + daily vs weekly 窗口
- set_digest_time 校验 + get_digest_state
- digest payload 的飞书/钉钉包装结构(digest 分支)
- push_digest 真实送达(events=all 接收端)
- API digest 预览 + push; 页面含摘要按钮
"""

import collections
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    sa_mod._DIGEST_STATE.update({"time": "", "last_date": ""})
    yield


def _mk_runs(base_dir, n_ok=3, n_fail=1):
    """造磁盘历史(直接写 JSONL): n_ok 成功 + n_fail 失败, 均在 24h 内。"""
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for i in range(n_ok):
            f.write(json.dumps({
                "ts": sa_mod._now(), "summary": {
                    "goal": "成功目标%d" % i, "ts": sa_mod._now(), "ok": True,
                    "selfcheck_score": 90 + i, "elapsed_sec": 2.0 + i,
                    "llm_calls": 4, "est_total_tokens": 100,
                    "est_cost_cny": 0.001}}, ensure_ascii=False) + "\n")
        for i in range(n_fail):
            f.write(json.dumps({
                "ts": sa_mod._now(), "summary": {
                    "goal": "失败目标%d" % i, "ts": sa_mod._now(), "ok": False,
                    "selfcheck_score": 60, "elapsed_sec": 1.0,
                    "llm_calls": 1, "est_total_tokens": 10,
                    "est_cost_cny": 0.0001}}, ensure_ascii=False) + "\n")


def test_digest_stats(tmp_path):
    _mk_runs(tmp_path, n_ok=3, n_fail=1)
    st = sa_mod._digest_stats("daily", base_dir=str(tmp_path))
    assert st["total"] == 4 and st["ok_count"] == 3 and st["fail"] == 1
    assert st["success_rate"] == 75.0
    assert st["llm_calls"] == 13 and st["tokens"] == 310
    assert st["avg_score"] is not None
    assert len(st["recent"]) == 4 and st["recent"][0]["ok"] in (True, False)
    # weekly 窗口 ≥ daily → 同样 4 条
    assert sa_mod._digest_stats("weekly", base_dir=str(tmp_path))["total"] == 4


def test_digest_time_setting():
    assert sa_mod.set_digest_time("08:30") == "08:30"
    assert sa_mod.set_digest_time("25:00") == "", "非法时刻应清空"
    assert sa_mod.get_digest_state()["time"] == ""


def test_digest_wrap_feishu_dingtalk():
    stats = sa_mod._digest_stats  # noqa - 仅引用防误删
    payload = {"event": "digest", "period": "daily", "since": "s", "until": "u",
               "total": 4, "ok": 3, "fail": 1, "success_rate": 75.0,
               "avg_elapsed": 2.0, "avg_score": 90.0, "llm_calls": 13,
               "tokens": 310, "cost": 0.0031,
               "recent": [{"goal": "成功目标0", "ok": True, "ts": "t"}]}
    f = sa_mod._webhook_wrap({"fmt": "feishu"}, payload)
    assert f["msg_type"] == "interactive"
    assert "日报" in f["card"]["header"]["title"]["content"]
    assert "成功率 75.0%" in f["card"]["elements"][0]["content"]
    d = sa_mod._webhook_wrap({"fmt": "dingtalk"}, payload)
    assert d["msgtype"] == "markdown"
    assert "周报" not in d["markdown"]["title"] and "日报" in d["markdown"]["title"]


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


def test_push_digest_delivers(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 9109), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.payload = {}
    try:
        sa_mod.add_webhook("http://127.0.0.1:9109/hook", base_dir=str(tmp_path))
        _mk_runs(tmp_path, 2, 1)
        rep = sa_mod.push_digest("daily", base_dir=str(tmp_path))
        assert rep["ok"] is True and rep["sent"] == 1
        assert rep["stats"]["total"] == 3
        time.sleep(0.3)
        assert _Capture.payload.get("event") == "digest"
        assert _Capture.payload.get("period") == "daily"
        assert _Capture.payload.get("total") == 3
    finally:
        srv.shutdown()
        srv.server_close()


def test_digest_api_e2e(tmp_path):
    d = str(tmp_path)
    old = os.getcwd()
    os.chdir(d)
    _mk_runs(d, 2, 0)  # chdir 后再写入服务器 cwd, 与其 base_dir 一致
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9110), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", 9110, timeout=15)
        c.request("GET", "/api/superagent/digest?period=daily")
        r = c.getresponse()
        data = json.loads(r.read().decode())
        assert r.status == 200 and data["ok"] is True and data["total"] == 2

        # 非法 period → 400
        c = http.client.HTTPConnection("127.0.0.1", 9110, timeout=15)
        c.request("GET", "/api/superagent/digest?period=yearly")
        assert c.getresponse().status == 400

        # push(无接收端也 200, sent=0)
        c = http.client.HTTPConnection("127.0.0.1", 9110, timeout=15)
        c.request("POST", "/api/superagent/digest/push",
                  body=json.dumps({"period": "weekly"}).encode(),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read().decode())
        assert r.status == 200 and data["ok"] is True and data["period"] == "weekly"
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_digest_buttons():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "编排摘要" in html and "previewDigest" in html and "pushDigest" in html

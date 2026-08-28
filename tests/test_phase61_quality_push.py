# -*- coding: utf-8 -*-
"""Phase 61 · 质量告警 Webhook 推送测试.

覆盖:
- add_webhook 接受 events="quality"; 非法值仍归 all
- _quality_md: 窗口/条数/指标行/链接
- _webhook_wrap quality 分支: feishu 红头卡片 / dingtalk markdown / raw 原样
- push_quality_alerts: 无告警 skipped 不发 / 有告警真实送达(events 过滤) / 失败接收端进 errors
- set_public_base_url -> panel_url 注入
- API POST /api/superagent/quality/push
- 页面含推送 UI(quality 事件选项 + pushQuality)
"""

import http.client
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(sa_mod, "_HOOKS", {})
    monkeypatch.setattr(sa_mod, "_HOOKS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
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


G = "同一目标"


def _seed_alert(tmp_path):
    rows = [(f"2026-08-28 {h:02d}:00:00", G, 90, 3.0, 2) for h in (8, 9, 10)]
    rows.append(("2026-08-28 13:00:00", G, 40, 3.0, 2))
    _write(tmp_path, rows)


def test_webhook_events_quality_accepted(tmp_path):
    h = sa_mod.add_webhook("https://example.com/q", events="quality",
                           base_dir=str(tmp_path))
    assert h["events"] == "quality"
    h2 = sa_mod.add_webhook("https://example.com/x", events="bogus",
                            base_dir=str(tmp_path))
    assert h2["events"] == "all", "非法事件仍应归 all"


def test_quality_md_content():
    payload = {"days": 7, "count": 1, "panel_url": "http://p/superagent",
               "alerts": [{"ts": "2026-08-28 13:00:00", "goal": "写爬虫",
                           "deviations": [{"label": "自检分", "value": 40,
                                           "mean": 90, "std": 0, "z": -50}]}]}
    md = sa_mod._quality_md(payload)
    assert "近 7 天" in md and "**1**" in md
    assert "08-28 13:00" in md and "写爬虫" in md
    assert "自检分 **40** (基线 90±0, z=-50)" in md
    assert "[📏 查看质量基线](http://p/superagent)" in md


def test_wrap_quality_formats():
    payload = {"event": "quality", "days": 7, "count": 1, "alerts": [], "panel_url": ""}
    feishu = sa_mod._webhook_wrap({"fmt": "feishu"}, payload)
    assert feishu["msg_type"] == "interactive"
    assert feishu["card"]["header"]["title"]["content"] == "🚨 灵梦work 质量告警"
    assert feishu["card"]["header"]["template"] == "red"

    ding = sa_mod._webhook_wrap({"fmt": "dingtalk"}, payload)
    assert ding["msgtype"] == "markdown"
    assert "质量告警" in ding["markdown"]["title"]

    raw = sa_mod._webhook_wrap({"fmt": "raw"}, payload)
    assert raw is payload


def test_push_quality_alerts(tmp_path):
    # 无告警: skipped, 不发
    rep = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30)
    assert rep["ok"] is True and rep["skipped"] is True and rep["alerts"] == 0

    _seed_alert(tmp_path)
    rep2 = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30)
    assert rep2["ok"] is True and rep2["skipped"] is False
    assert rep2["alerts"] == 1 and rep2["sent"] == 0, "无接收端时 sent=0 但不报错"


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


def test_push_real_delivery_and_event_filter(tmp_path, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 9102), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.payload = {}
    _seed_alert(tmp_path)
    try:
        sa_mod.add_webhook("http://127.0.0.1:9102/hook", events="quality",
                           base_dir=str(tmp_path))
        sa_mod.add_webhook("http://127.0.0.1:9102/hook2", events="fail",
                           base_dir=str(tmp_path))  # 不该收到 quality
        rep = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30)
        assert rep["ok"] is True and rep["alerts"] == 1
        assert rep["sent"] == 1 and rep["errors"] == []
        got = _Capture.payload
        assert got["event"] == "quality" and got["count"] == 1
        assert got["alerts"][0]["deviations"][0]["metric"] == "score"

        # base_url 注入 panel_url
        sa_mod.set_public_base_url("http://panel.example")
        try:
            rep2 = sa_mod.push_quality_alerts(base_dir=str(tmp_path), days=30)
            assert rep2["sent"] == 1
            assert _Capture.payload["panel_url"] == "http://panel.example/superagent"
        finally:
            sa_mod.set_public_base_url("")
    finally:
        srv.shutdown()
        srv.server_close()


def test_quality_push_api(tmp_path):
    _seed_alert(tmp_path)
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9124), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9124, timeout=15)
        c.request("POST", "/api/superagent/quality/push",
                  body=json.dumps({"days": 30}).encode(),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True
        assert j["alerts"] == 1 and j["sent"] == 0 and j["skipped"] is False
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_push_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert 'value="quality"' in html, "webhook 事件选项应含质量告警"
    assert "pushQuality" in html and "/api/superagent/quality/push" in html

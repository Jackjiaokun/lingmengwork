# -*- coding: utf-8 -*-
"""Phase 63 · 设置中心并入编排/通知配置测试.

覆盖:
- _SETTINGS_SCHEMA 新增「编排与通知」组 5 键(digest_time/retry_max/public_base_url/
  quality_auto_push/quality_auto_interval_h) 且类型正确
- GET /api/settings values 含新键
- POST /api/settings mode=form 保存 quality_auto_push -> config.toml 落盘
  + set_quality_auto 软生效(经 /api/superagent/quality/auto 可见)
- POST form 保存 digest_time -> _DIGEST_STATE 即时更新
- settings.html schema 驱动渲染(存量页面无需改动, 只验 schema 即渲染)
"""

import http.client
import json
import os
import threading
import time

import pytest

import lingmengwork.web.server as S
from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv

NEW_KEYS = {
    "agent.public_base_url": "string",
    "agent.digest_time": "string",
    "agent.orchestration_retry_max": "int",
    "agent.quality_auto_push": "bool",
    "agent.quality_auto_interval_h": "float",
}

SAMPLE = '''
[llm]
backend = "sensenova"

[agent]
max_iterations = 32
'''

KEYS = [f["key"] for g in S._SETTINGS_SCHEMA for f in g["fields"]]


def test_schema_has_notify_group():
    groups = {g["title"]: g for g in S._SETTINGS_SCHEMA}
    g = groups.get("编排与通知")
    assert g is not None, "应有「编排与通知」组"
    got = {f["key"]: f["type"] for f in g["fields"]}
    assert got == NEW_KEYS
    # 全部不需要重启(运行期开关)
    assert all(not f.get("restart") for f in g["fields"])


def test_settings_get_contains_new_keys(monkeypatch, tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(S, "DEFAULT_CONFIG_PATHS", [p])
    data = S.Handler._settings_get(None)
    for k, t in NEW_KEYS.items():
        assert k in data["values"], "values 应含 %s" % k
    fld = {f["key"]: f for g in data["schema"] for f in g["fields"]}
    assert fld["agent.quality_auto_push"]["type"] == "bool"
    assert fld["agent.quality_auto_interval_h"]["type"] == "float"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(sa_mod, "_HOOKS", {})
    monkeypatch.setattr(sa_mod, "_HOOKS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_QUALITY_AUTO",
                        {"enabled": False, "interval_h": 24.0, "silence_h": 24.0,
                         "last_push_epoch": 0.0, "silence": {}})
    monkeypatch.setattr(sa_mod, "_DIGEST_STATE", {"time": "", "last_date": ""})
    saved_runtime = S._RUNTIME_CONFIG
    saved_retry = dict(sa_mod._DEFAULT_RETRY)
    saved_pub = sa_mod._PUBLIC_BASE_URL.get("url")
    yield
    # 🔴 恢复被 form 保存软重载污染的全局态, 否则会泄漏给后续测试
    S._RUNTIME_CONFIG = saved_runtime
    sa_mod._DEFAULT_RETRY.clear()
    sa_mod._DEFAULT_RETRY.update(saved_retry)
    sa_mod.set_public_base_url(saved_pub or "")


def test_settings_form_save_soft_applies(tmp_path):
    """form 保存质量告警/日报配置 -> config 落盘 + 运行期开关即时生效。"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(SAMPLE, encoding="utf-8")
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9126), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9126, timeout=15)

        def post(path, payload):
            c.request("POST", path, body=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r, json.loads(r.read().decode())

        # 开启质量告警自动推送 + 间隔 12h + 日报时刻 08:30
        r, j = post("/api/settings", {"mode": "form", "values": {
            "agent.quality_auto_push": True,
            "agent.quality_auto_interval_h": 12,
            "agent.digest_time": "08:30",
            "agent.orchestration_retry_max": 2,
            "agent.public_base_url": "http://1.2.3.4:8318",
        }})
        assert r.status == 200 and j["ok"] is True, j
        assert j["require_restart"] is False, "这组开关应软生效"

        # config.toml 真实落盘
        text = cfg.read_text(encoding="utf-8")
        assert "quality_auto_push = true" in text
        assert "quality_auto_interval_h = 12" in text
        assert 'digest_time = "08:30"' in text

        # 软生效: 内核运行期状态已更新
        auto = sa_mod.get_quality_auto()
        assert auto["enabled"] is True and auto["interval_h"] == 12.0
        assert sa_mod._DIGEST_STATE["time"] == "08:30"
        assert sa_mod._DEFAULT_RETRY["max"] == 2

        # public_base_url 注入
        c.request("GET", "/api/superagent/quality/auto")
        r = c.getresponse()
        j2 = json.loads(r.read().decode())
        assert j2["ok"] is True and j2["enabled"] is True

        # 再关闭
        r, j = post("/api/settings", {"mode": "form", "values": {
            "agent.quality_auto_push": False}})
        assert r.status == 200
        assert sa_mod.get_quality_auto()["enabled"] is False
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_renders_schema_driven():
    """settings.html 是 schema 驱动渲染, 新组无需改页面。验证渲染循环仍存在。"""
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "settings.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "STATE.schema" in html and "for(const g of STATE.schema)" in html

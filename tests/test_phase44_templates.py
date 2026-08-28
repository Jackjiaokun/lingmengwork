"""Phase 44 · 编排模板库测试.

覆盖:
- add/list/update/remove 模板 CRUD + 校验(名称/目标必填)
- template_fields 占位符提取(中文变量名/保序去重)
- render_template_text 替换 + missing 缺失标记
- use_template 计数/last_used/持久化重载
- API e2e: GET / POST create(400)/update/delete/render(404)
- 页面含模板库 UI
"""

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
def clean_tpls(monkeypatch):
    monkeypatch.setattr(sa_mod, "_TPLS", {})
    monkeypatch.setattr(sa_mod, "_TPLS_LOADED", set())
    yield


def test_template_crud_and_validation(tmp_path):
    with pytest.raises(ValueError):
        sa_mod.add_template("", "目标", base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        sa_mod.add_template("名称", "  ", base_dir=str(tmp_path))

    t = sa_mod.add_template("服务巡检", "巡检 {{host}} 的 {{service}} 服务",
                            description="日常巡检", base_dir=str(tmp_path))
    assert t["id"] and t["use_count"] == 0
    lst = sa_mod.list_templates(base_dir=str(tmp_path))
    assert lst[0]["fields"] == ["host", "service"]

    snap = sa_mod.update_template(t["id"], {"name": "巡检模板"}, base_dir=str(tmp_path))
    assert snap["name"] == "巡检模板"
    assert sa_mod.update_template("t_nope", {"name": "x"}, base_dir=str(tmp_path)) is None
    assert sa_mod.remove_template(t["id"], base_dir=str(tmp_path)) is True
    assert sa_mod.list_templates(base_dir=str(tmp_path)) == []


def test_template_fields_and_render():
    text = "巡检 {{host}} 的 {{host}} 服务, 输出 {{报告名}}, 忽略 {{ empty }}"
    assert sa_mod.template_fields(text) == ["host", "报告名", "empty"]
    goal, missing = sa_mod.render_template_text(text, {"host": "db-01", "报告名": "日报"})
    assert goal == "巡检 db-01 的 db-01 服务, 输出 日报, 忽略 {{ empty }}"
    assert missing == ["empty"]
    # 无占位符
    goal2, missing2 = sa_mod.render_template_text("固定目标", {})
    assert goal2 == "固定目标" and missing2 == []


def test_use_template_counts(tmp_path):
    t = sa_mod.add_template("巡检", "巡检 {{host}}", base_dir=str(tmp_path))
    r = sa_mod.use_template(t["id"], {"host": "web-1"}, base_dir=str(tmp_path))
    assert r["goal"] == "巡检 web-1" and r["missing"] == []
    r2 = sa_mod.use_template(t["id"], {}, base_dir=str(tmp_path))
    assert r2["missing"] == ["host"] and "{{host}}" in r2["goal"]
    entry = sa_mod.list_templates(base_dir=str(tmp_path))[0]
    assert entry["use_count"] == 2 and entry["last_used"]
    assert sa_mod.use_template("t_nope", base_dir=str(tmp_path)) is None

    # 持久化重载
    sa_mod._TPLS.clear()
    sa_mod._TPLS_LOADED.clear()
    entry2 = sa_mod.list_templates(base_dir=str(tmp_path))[0]
    assert entry2["use_count"] == 2


def test_api_templates_e2e():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 8998), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def req(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", 8998, timeout=15)
            c.request(method, path,
                      body=json.dumps(body or {}).encode() if method == "POST" else None,
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, json.loads(r.read().decode())

        st, resp = req("POST", "/api/superagent/templates/create", {"name": ""})
        assert st == 400

        st, resp = req("POST", "/api/superagent/templates/create",
                       {"name": "巡检", "goal_template": "巡检 {{host}}"})
        assert st == 200
        tid = resp["template"]["id"]

        st, resp = req("GET", "/api/superagent/templates")
        assert st == 200 and resp["templates"][0]["fields"] == ["host"]

        st, resp = req("POST", "/api/superagent/templates/update",
                       {"id": tid, "name": "巡检v2"})
        assert st == 200 and resp["template"]["name"] == "巡检v2"

        st, resp = req("POST", "/api/superagent/templates/render",
                       {"id": tid, "vars": {"host": "db-01"}})
        assert st == 200 and resp["goal"] == "巡检 db-01" and resp["missing"] == []

        st, resp = req("POST", "/api/superagent/templates/render", {"id": "t_nope"})
        assert st == 404

        st, resp = req("POST", "/api/superagent/templates/delete", {"id": tid})
        assert st == 200 and resp["removed"] == tid
        st, resp = req("POST", "/api/superagent/templates/delete", {"id": tid})
        assert st == 404
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_template_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "模板库" in html
    assert "createTemplate" in html and "useTemplate" in html
    assert "tplGoal" in html

"""Phase 45 · 模板与定时编排联动测试.

覆盖:
- add_schedule 引用模板: 渲染快照 / 参数缺失 400 / 模板不存在 400
- list_schedules 附 template_name
- run_schedule 执行期重渲染(模板改动生效) + 模板删除回退目标快照
- update_schedule 白名单含 template_id/tpl_vars
- API e2e: create(模板缺参 400)+run-now
- 页面含模板引用 UI(schedTpl 下拉)
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
def clean_state(monkeypatch):
    monkeypatch.setattr(sa_mod, "_SCHEDS", {})
    monkeypatch.setattr(sa_mod, "_SCHEDS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    monkeypatch.setattr(sa_mod, "_TPLS", {})
    monkeypatch.setattr(sa_mod, "_TPLS_LOADED", set())
    yield


@pytest.fixture
def fast_executors(monkeypatch):
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [],
                              "note": "", "goal_seen": goal})
                         for d in ("code", "creation", "research", "ops")})


def test_add_schedule_with_template(tmp_path):
    t = sa_mod.add_template("巡检", "巡检 {{host}} 的 {{service}} 服务", base_dir=str(tmp_path))
    # 参数缺失 → ValueError
    with pytest.raises(ValueError) as ei:
        sa_mod.add_schedule(every_sec=3600, base_dir=str(tmp_path),
                            template_id=t["id"], tpl_vars={"host": "db-01"})
    assert "service" in str(ei.value)
    # 模板不存在 → ValueError
    with pytest.raises(ValueError):
        sa_mod.add_schedule(every_sec=3600, base_dir=str(tmp_path),
                            template_id="t_nope", tpl_vars={})
    # 正常创建 → goal 为渲染快照
    s = sa_mod.add_schedule(every_sec=3600, base_dir=str(tmp_path),
                            template_id=t["id"], tpl_vars={"host": "db-01", "service": "nginx"})
    assert s["goal"] == "巡检 db-01 的 nginx 服务"
    assert s["template_id"] == t["id"] and s["tpl_vars"]["host"] == "db-01"
    # 列表附模板名
    lst = sa_mod.list_schedules(base_dir=str(tmp_path))
    assert lst[0]["template_name"] == "巡检"


def test_run_schedule_rerender_and_fallback(tmp_path, fast_executors):
    t = sa_mod.add_template("巡检", "巡检 {{host}}", base_dir=str(tmp_path))
    s = sa_mod.add_schedule(every_sec=3600, base_dir=str(tmp_path),
                            template_id=t["id"], tpl_vars={"host": "db-01"})
    # 执行期重渲染: 模板改动即时生效
    sa_mod.update_template(t["id"], {"goal_template": "深度巡检 {{host}} 并出报告"},
                           base_dir=str(tmp_path))
    rep = sa_mod.run_schedule(s["id"], base_dir=str(tmp_path), queue_wait_sec=5)
    assert rep["ok"] is True
    assert rep["result"]["goal"] == "深度巡检 db-01 并出报告", "执行应使用重渲染目标"
    # 模板删除 → 回退目标快照
    sa_mod.remove_template(t["id"], base_dir=str(tmp_path))
    rep2 = sa_mod.run_schedule(s["id"], base_dir=str(tmp_path), queue_wait_sec=5)
    assert rep2["ok"] is True
    assert rep2["result"]["goal"] == "巡检 db-01", "模板已删应回退快照"


def test_update_schedule_template_fields(tmp_path):
    t = sa_mod.add_template("巡检", "巡检 {{host}}", base_dir=str(tmp_path))
    s = sa_mod.add_schedule("手动目标", every_sec=3600, base_dir=str(tmp_path))
    snap = sa_mod.update_schedule(s["id"], {"template_id": t["id"], "tpl_vars": {"host": "x"}},
                                  base_dir=str(tmp_path))
    assert snap["template_id"] == t["id"] and snap["tpl_vars"] == {"host": "x"}


def test_api_schedule_with_template_e2e():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 8999), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def req(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", 8999, timeout=30)
            c.request(method, path,
                      body=json.dumps(body or {}).encode() if method == "POST" else None,
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, json.loads(r.read().decode())

        _, resp = req("POST", "/api/superagent/templates/create",
                      {"name": "巡检", "goal_template": "巡检 {{host}}"})
        tid = resp["template"]["id"]

        # 缺参 → 400
        st, resp = req("POST", "/api/superagent/schedules/create",
                       {"every_sec": 3600, "template_id": tid, "tpl_vars": {}})
        assert st == 400 and "host" in resp["error"]

        st, resp = req("POST", "/api/superagent/schedules/create",
                       {"every_sec": 3600, "template_id": tid, "tpl_vars": {"host": "db-01"}})
        assert st == 200 and resp["schedule"]["goal"] == "巡检 db-01"
        sid = resp["schedule"]["id"]

        st, resp = req("GET", "/api/superagent/schedules")
        assert st == 200 and resp["schedules"][0]["template_name"] == "巡检"

        st, resp = req("POST", "/api/superagent/schedules/run", {"id": sid})
        assert st == 200 and resp["ok"] is True and resp["goal_ok"] is True
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_template_linkage():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "schedTpl" in html and "renderSchedTplVars" in html
    assert "template_id" in html

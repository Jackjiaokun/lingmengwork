"""Phase 25 — 工作伙伴多智能体联邦 (federation) 测试。

覆盖：路由(单域/跨域/显式 hint) / 并行派发(无 LLM 规则兜底) / 伙伴异常隔离 /
汇聚冲突检测 / 服务端 API(GET /api/federation + POST /api/federation/dispatch + /federation 页面) /
selfcheck 探针计数(11)。
"""
import json
import os
import tempfile
import threading
import time
import http.client

import pytest

from lingmengwork import federation as fed
from lingmengwork.web import server as _srv


# ------------------------------------------------------------------ 路由
def test_route_code_only():
    f = fed.get_federation()
    assert f.route("写一个登录函数") == ["code"]


def test_route_cross_domain():
    f = fed.get_federation()
    routed = f.route("开发一款命令行待办应用，制作一张产品配图，撰写发布文案并准备上线部署")
    assert "code" in routed and "creation" in routed and "ops" in routed


def test_route_research_keyword():
    f = fed.get_federation()
    assert "research" in f.route("研究竞品并分析市场趋势")


def test_route_hint_override():
    f = fed.get_federation()
    assert f.route("随便聊聊", hint_domains=["research", "ops"]) == ["research", "ops"]
    # 未知 hint 被忽略, 退回自动路由
    assert f.route("写个函数", hint_domains=["nope"]) == ["code"]


# ------------------------------------------------------------------ 派发
def test_dispatch_deterministic():
    f = fed.get_federation()
    rep = f.dispatch("开发一款命令行待办应用，制作一张产品配图，撰写发布文案并准备上线部署", llm_call=None)
    assert rep["ok"]
    assert len(rep["partners"]) >= 2                       # 跨域多伙伴
    assert all(p["status"] == "ok" for p in rep["partners"])
    assert rep["merged"]["parts"]
    assert "联邦协同结果" in rep["merged"]["summary"]


def test_dispatch_single_hint():
    f = fed.get_federation()
    rep = f.dispatch("研究一下竞品格局", hint_domains=["research"], llm_call=None)
    assert rep["ok"]
    ids = [p["partner_id"] for p in rep["partners"]]
    assert ids == ["research"]


def test_dispatch_partner_failure_isolated(monkeypatch):
    """单伙伴异常不影响联邦整体(错误隔离)。"""
    f = fed.get_federation()
    import lingmengwork.creation_domains as cd

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cd, "dispatch", boom)
    rep = f.dispatch("写一个函数", llm_call=None)          # 仅路由到 code
    assert rep["ok"]                                          # 整体仍成功
    code = next(p for p in rep["partners"] if p["partner_id"] == "code")
    assert code["status"] == "error"
    assert code["error"]


# ------------------------------------------------------------------ 汇聚
def test_merge_conflict_detection():
    f = fed.get_federation()
    rs = [
        fed.PartnerResult("code", "编码伙伴", "code", "ok", "s",
                          artifacts=[{"type": "blueprint"}]),
        fed.PartnerResult("creation", "创作伙伴", "creation", "ok", "s",
                          artifacts=[{"type": "blueprint"}]),
    ]
    m = f.merge(rs)
    assert any(c["type"] == "blueprint" for c in m["conflicts"])
    assert "联邦协同结果" in m["summary"]


def test_merge_no_conflict():
    f = fed.get_federation()
    rs = [
        fed.PartnerResult("code", "编码伙伴", "code", "ok", "s",
                          artifacts=[{"type": "blueprint"}]),
        fed.PartnerResult("research", "研究伙伴", "research", "ok", "s",
                          artifacts=[{"type": "research_brief"}]),
    ]
    m = f.merge(rs)
    assert m["conflicts"] == []


# ------------------------------------------------------------------ 服务端
def test_server_api():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8981
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", PORT), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        def post(path, body):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            c.request("POST", path, body=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        # 伙伴清单
        st, js = get("/api/federation")
        assert st == 200, (st, js)
        d1 = json.loads(js)
        assert d1["ok"] and len(d1["partners"]) == 4

        # 页面含伙伴列表容器 + 「联邦」字样
        st2, html = get("/federation")
        assert st2 == 200 and "联邦" in html and 'id="partnerList"' in html

        # 派发(跨域): 无 LLM → 规则兜底, 全部成功
        st3, js3 = post("/api/federation/dispatch",
                        {"goal": "开发一款命令行待办应用，制作一张产品配图，撰写发布文案并准备上线部署"})
        assert st3 == 200, (st3, js3)
        d3 = json.loads(js3)
        assert d3["ok"]
        assert len(d3["partners"]) >= 2
        assert all(p["status"] == "ok" for p in d3["partners"])
        assert "summary" in d3["merged"]

        # 缺 goal → 400
        st4, js4 = post("/api/federation/dispatch", {})
        assert st4 == 400, (st4, js4)
    finally:
        srv.shutdown()
        os.chdir(old)


# ------------------------------------------------------------------ 自检集成
def test_selfcheck_probe_count():
    """selfcheck 探针数应为 14 (Phase25–27 + Phase32 插件中枢)。"""
    from lingmengwork import selfcheck as sc
    rep = sc.run()
    assert rep["total"] == 14, "探针数应为 14, 实际 %d" % rep["total"]
    failed = {c["name"]: c["detail"] for c in rep["checks"] if not c["ok"]}
    assert not failed, failed


# ------------------------------------------------------------------ Phase 34: 连接器能力标签匹配
def test_match_connectors_by_name():
    """match_connectors 按目标关键词命中连接器 name/category/tags。"""
    from lingmengwork import plugin_hub as ph
    hub = ph.get_hub()
    hub.register_connector("search_web", category="search",
                           description="Web 搜索连接器", tags=["search", "web", "http"],
                           call_fn=lambda g, **kw: {"ok": True, "result": "searched: " + g})
    hub.register_connector("offline_tool", category="local",
                           description="离线工具", tags=["local", "file"],
                           env_required=["NEVER_SET_ENV_XYZ"],
                           call_fn=lambda g, **kw: {"ok": True})
    # 关键词命中 health 内置连接器(name 含 health)
    matched = hub.match_connectors("系统 health check")
    names = {m["name"] for m in matched}
    assert "health" in names, "应匹配 health 连接器"
    # 关键词命中新注册 search_web (category=search + tags 含 search)
    matched2 = hub.match_connectors("web search 搜索")
    assert any(m["name"] == "search_web" for m in matched2), "应匹配 search_web"
    # offline_tool 不可用, 不应出现在匹配结果
    matched3 = hub.match_connectors("local file 工具")
    assert not any(m["name"] == "offline_tool" for m in matched3), "降级连接器不应匹配"


def test_dispatch_calls_matched_connectors():
    """dispatch 传入 connector_names 时, 调用对应连接器并注入 matched_connectors。"""
    from lingmengwork import plugin_hub as ph
    hub = ph.get_hub()
    calls = []
    hub.register_connector("tst_echo", category="test",
                           description="测试回显", tags=["echo", "test"],
                           call_fn=lambda g, **kw: (calls.append(g) or {"ok": True, "result": "echo:" + g}))
    f = fed.get_federation()
    rep = f.dispatch("给个测试目标", connector_names=["tst_echo", "nonexistent"])
    assert "matched_connectors" in rep
    assert rep["matched_connectors"], "应有 matched_connectors"
    assert any(m["name"] == "tst_echo" and m["ok"] for m in rep["matched_connectors"]), rep
    assert "给个测试目标" in calls, "连接器应被调用"
    # nonexistent 连接器不在 matched_connectors 中(不存在, 被跳过)
    assert not any(m["name"] == "nonexistent" for m in rep["matched_connectors"])


def test_match_connectors_empty_goal():
    """空目标 → match_connectors 返回空列表, 不报错。"""
    from lingmengwork import plugin_hub as ph
    hub = ph.get_hub()
    assert hub.match_connectors("") == []
    assert hub.match_connectors("  ") == []

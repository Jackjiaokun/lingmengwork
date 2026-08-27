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

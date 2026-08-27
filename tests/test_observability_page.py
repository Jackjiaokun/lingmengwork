"""批次11 可观测可视化 — 仪表盘页面 / 路由 / 数据结构断言。

前端页面用真实重打包 e2e 验证(http 200 + 含 api 引用);
此处聚焦确定性断言: 页面文件存在且引用两个数据端点、server 路由已注册、
index 入口就位、health_check 与 get_stats 返回结构字段齐全。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lingmengwork", "web", "static")
SERVER = os.path.join(ROOT, "lingmengwork", "web", "server.py")


def test_observability_page_exists_and_references_endpoints():
    p = os.path.join(STATIC, "observability.html")
    assert os.path.isfile(p), "observability.html 未生成"
    html = open(p, encoding="utf-8").read()
    assert "/api/stats" in html, "页面未消费 /api/stats"
    assert "/api/health/full" in html, "页面未消费 /api/health/full"
    assert "运行追踪" in html, "页面标题缺失"


def test_observability_route_registered():
    src = open(SERVER, encoding="utf-8").read()
    assert '"/observability"' in src, "server 未注册 /observability 路由"
    assert "observability.html" in src, "路由未 serve observability.html"


def test_index_has_observability_entry():
    idx = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    assert "/observability" in idx, "主面板未加入口链接"


def test_health_check_full_structure():
    from lingmengwork.tools import health as h

    mods = ["demo", "fs", "git", "fetch", "shell", "grep", "sqlite", "search", "review"]
    cfg = {
        "llm": {"backend": "mock"},
        "mcp": {"servers": [
            {"name": m, "command": "python", "args": ["-m", f"lingmengwork.tools.mcp_{m}_server"]}
            for m in mods
        ]},
        "mcp_count_target": 9,
    }

    def ok_llm(c):
        return True, "stub ok", 12

    def ok_mcp(s, cfg):
        return True, "stub"

    def ok_fs(cfg):
        return True, "stub fs"

    rep = h.health_check(cfg, llm_probe=ok_llm, mcp_probe=ok_mcp, fs_probe=ok_fs)
    assert rep["overall"] == "ok"
    assert rep["mcp_count"] == 9
    assert "llm" in rep and "status" in rep["llm"]
    assert "filesystem" in rep and "status" in rep["filesystem"]
    assert len(rep["mcp_servers"]) == 9
    for s in rep["mcp_servers"]:
        for k in ("name", "status", "detail"):
            assert k in s, f"mcp 字段缺失: {k}"


def test_stats_structure_fields():
    from lingmengwork.tools import registry as reg

    reg.reset_stats()
    reg._record("alpha", True, 5.0, None)
    reg._record("beta", False, 12.0, "网络")
    reg._record("alpha", True, 7.0, None)

    s = reg.get_stats()
    assert s["total_calls"] == 3
    assert 0.66 < s["success_rate"] < 0.67, f"success_rate 应≈2/3, 实得 {s['success_rate']}"
    assert len(s["tools"]) >= 2
    for t in s["tools"]:
        for k in ("name", "calls", "ok", "fail", "avg_ms", "fail_by_tag"):
            assert k in t, f"tools 字段缺失: {k}"
    # alpha: 2 calls, avg=(5+7)/2=6
    alpha = next(t for t in s["tools"] if t["name"] == "alpha")
    assert alpha["calls"] == 2 and abs(alpha["avg_ms"] - 6.0) < 1e-9
    beta = next(t for t in s["tools"] if t["name"] == "beta")
    assert beta["fail"] == 1 and beta["fail_by_tag"].get("网络") == 1

    assert len(s["recent"]) >= 3
    for e in s["recent"]:
        for k in ("ts", "name", "ok", "ms"):
            assert k in e, f"recent 字段缺失: {k}"
    reg.reset_stats()


def test_stats_percentile_fields_batch12():
    from lingmengwork.tools import registry as reg

    reg.reset_stats()
    reg._record("alpha", True, 100)
    reg._record("alpha", True, 200)
    reg._record("alpha", False, 50, tag="network")
    reg._record("beta", True, 10)
    s = reg.get_stats()
    # 全局耗时分位 (批次12 新增)
    for k in ("total_ms", "avg_ms", "p50_ms", "p95_ms", "p99_ms"):
        assert k in s, f"stats 顶层字段缺失: {k}"
    # 每工具耗时分位 (批次12 新增)
    for t in s["tools"]:
        for k in ("p50_ms", "p95_ms", "p99_ms", "max_ms", "min_ms"):
            assert k in t, f"tools 字段缺失: {k}"
    reg.reset_stats()


def test_observability_page_has_superagent_section():
    """Phase 35: observability 页面新增超级 AGENT 编排区并消费 superagent 字段。"""
    path = os.path.join(os.path.dirname(__file__), "..", "lingmengwork", "web", "static",
                        "observability.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "superagent" in html.lower(), "页面未包含 superagent"
    assert "renderSuperagent" in html, "页面未消费 renderSuperagent"
    assert "tbl-agent" in html, "页面缺少超级 AGENT 表格"


def test_superagent_stats_structure_from_server(tmp_path, monkeypatch):
    """Phase 35: _superagent_stats 返回聚合字段(空态 + 有数据)。"""
    import collections
    from lingmengwork.web import server as S
    from lingmengwork import superagent as _sa

    class _FakePool:
        _tasks = []
        _providers = []
        def list_tasks(self): return []
        def list_providers(self): return []

    class _H(S.Handler):
        def __init__(self): pass
        def _get_pool(self): return _FakePool()

    # 空态: 先清空 _RUNS 确保初始为空
    import collections
    _sa._RUNS = collections.deque(maxlen=60)
    sa = _H()._superagent_stats()
    assert sa["total"] == 0
    assert sa["elapsed_p50"] is None

    # 有数据: 手动往 _RUNS 塞入
    runs = collections.deque(maxlen=60)
    _sa._RUNS = runs
    runs.append({"goal": "G1", "ts": "2026-08-28T05:00", "ok": True, "routed": ["code"],
                 "partners_ok": 1, "partners_total": 1, "conflicts": 0,
                 "selfcheck_score": 98, "guards_passed": True, "elapsed_sec": 1.2})
    runs.append({"goal": "G2", "ts": "2026-08-28T05:01", "ok": False, "routed": ["research"],
                 "partners_ok": 0, "partners_total": 1, "conflicts": 1,
                 "selfcheck_score": 70, "guards_passed": False, "elapsed_sec": 4.5})
    sa2 = _H()._superagent_stats()
    assert sa2["total"] == 2
    assert sa2["ok"] == 1 and sa2["fail"] == 1
    assert sa2["elapsed_p50"] is not None
    assert sa2["elapsed_max"] == 4.5
    assert sa2["avg_score"] == 84.0
    assert len(sa2["recent"]) == 2
    r0 = sa2["recent"][0]
    assert r0["goal"] == "G1" and r0["ok"] is True
    assert r0["routed"] == ["code"]
    assert r0["selfcheck_score"] == 98

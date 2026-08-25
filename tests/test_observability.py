"""批次9: 可观测性基础 (registry 工具调用统计埋点) 零依赖回归测试。

覆盖:
- _classify_err 错误分类 (网络/权限/资源/未找到/逻辑)
- get_stats 聚合 (总调用/成功率/各工具统计/平均耗时)
- reset_stats 清零
- recent 环形缓冲截断 (上限 _STATS_MAX_RECENT)
- 集成: 构造 Registry + monkeypatch stub, 验证 execute 真实路径埋点生效 + 失败归因标签
"""
import os
import tempfile

import pytest

from lingmengwork.tools import registry as reg


# ---------------- _classify_err ----------------

def test_classify_network():
    assert reg._classify_err(ConnectionError("connection timed out")) == "network"


def test_classify_permission():
    assert reg._classify_err(PermissionError("denied")) == "permission"


def test_classify_resource():
    assert reg._classify_err(MemoryError("out of memory")) == "resource"


def test_classify_notfound():
    assert reg._classify_err(FileNotFoundError("no such file")) == "notfound"


def test_classify_logic_default():
    assert reg._classify_err(ValueError("bad value")) == "logic"


# ---------------- get_stats / reset / recent ----------------

def test_stats_empty_after_reset():
    reg.reset_stats()
    s = reg.get_stats()
    assert s["total_calls"] == 0
    assert s["success_rate"] == 1.0
    assert s["tools"] == []
    assert s["recent"] == []


def test_stats_aggregation_and_success_rate():
    reg.reset_stats()
    reg._record("alpha", True, 100)
    reg._record("alpha", True, 200)
    reg._record("alpha", False, 50, tag="network")
    reg._record("beta", True, 10)
    s = reg.get_stats()
    assert s["total_calls"] == 4
    assert s["total_ok"] == 3
    assert s["total_fail"] == 1
    # 3/4 = 0.75
    assert s["success_rate"] == 0.75
    byname = {t["name"]: t for t in s["tools"]}
    assert byname["alpha"]["calls"] == 3
    assert byname["alpha"]["ok"] == 2
    assert byname["alpha"]["fail"] == 1
    assert byname["alpha"]["avg_ms"] == 116.7  # (100+200+50)/3
    assert byname["alpha"]["fail_by_tag"] == {"network": 1}
    assert byname["beta"]["calls"] == 1
    # 排序: 调用次数降序
    assert s["tools"][0]["name"] == "alpha"


def test_recent_ring_truncation():
    reg.reset_stats()
    for i in range(reg._STATS_MAX_RECENT + 20):
        reg._record(f"t{i}", True, i)
    s = reg.get_stats()
    assert len(s["recent"]) == reg._STATS_MAX_RECENT
    # 只保留最后 _STATS_MAX_RECENT 条
    assert s["recent"][-1]["name"] == f"t{reg._STATS_MAX_RECENT + 19}"


# ---------------- 集成: 真实 execute 路径埋点 ----------------

def _make_reg(monkeypatch):
    reg.reset_stats()
    cfg = {
        "agent": {
            "security": {"destructive_guard": "block", "audit_log": False},
            "tool_cache_ttl": 0,
        }
    }
    roots = [tempfile.mkdtemp()]

    def ok_tool(args, ctx):
        return "ok-result"

    def fail_tool(args, ctx):
        raise RuntimeError("connection timed out boom")

    def unknown_guard(args, ctx):
        return "x"

    monkeypatch.setitem(reg._IMPLS, "__probe_ok__", ok_tool)
    monkeypatch.setitem(reg._IMPLS, "__probe_fail__", fail_tool)
    monkeypatch.setitem(reg._IMPLS, "__probe_unknown__", unknown_guard)
    r = reg.Registry(roots=roots, cfg=cfg, permission_mode="bypassPermissions")
    return r


def test_execute_records_success(monkeypatch):
    r = _make_reg(monkeypatch)
    res = r.execute("__probe_ok__", {})
    assert res == "ok-result"
    s = reg.get_stats()
    assert s["total_calls"] == 1
    assert s["total_ok"] == 1
    assert s["success_rate"] == 1.0
    assert s["tools"][0]["name"] == "__probe_ok__"
    assert s["tools"][0]["avg_ms"] >= 0


def test_execute_records_failure_with_tag(monkeypatch):
    r = _make_reg(monkeypatch)
    res = r.execute("__probe_fail__", {})
    assert res.startswith("[tool error]")
    s = reg.get_stats()
    assert s["total_calls"] == 1
    assert s["total_fail"] == 1
    assert s["success_rate"] == 0.0
    t = s["tools"][0]
    assert t["name"] == "__probe_fail__"
    assert t["fail_by_tag"].get("network") == 1


def test_execute_permission_denied_tagged(monkeypatch):
    reg.reset_stats()
    cfg = {"agent": {"security": {"destructive_guard": "block", "audit_log": False}, "tool_cache_ttl": 0}}
    r = reg.Registry(roots=[tempfile.mkdtemp()], cfg=cfg, permission_mode="plan")
    res = r.execute("write_file", {"path": "a.txt", "content": "x"})
    assert res.startswith("[权限拒绝]")
    s = reg.get_stats()
    assert s["total_calls"] == 1
    assert s["total_fail"] == 1
    assert s["tools"][0]["fail_by_tag"].get("permission") == 1


def test_execute_unknown_tool_tagged(monkeypatch):
    r = _make_reg(monkeypatch)
    res = r.execute("__probe_unknown__", {})
    # 该 stub 返回非 [tool error] 前缀 -> 视为成功 ok
    assert res == "x"
    s = reg.get_stats()
    assert s["total_ok"] == 1
    # 未知工具名经 _IMPLS.get 命中(我们注入了), 走正常路径
    assert s["tools"][0]["name"] == "__probe_unknown__"

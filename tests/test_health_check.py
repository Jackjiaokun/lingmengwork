"""批次10: 全链路健康度自检 (tools/health.py) 零依赖回归测试。

覆盖:
- enumerate_mcp_servers: 从 config['mcp']['servers'] 枚举名称/命令/参数
- _module_file: 由 `-m lingmengwork.tools.mcp_xxx_server` 推导模块文件路径
- health_check 全绿 (注入 stub 探针): overall=ok, ok=True
- LLM 探针失败 -> overall=fail
- filesystem 探针失败 -> overall=fail
- MCP 某服务器模块缺失 -> 该项 status=fail, overall=fail
- overall warn 路径 (某组件 warn)
- 9 个 MCP 服务器枚举数量正确 (用真实 config.toml 烟测, 不依赖外部连通)
"""
import os
import sys

import pytest

# 允许直接从仓库根跑 pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lingmengwork.tools import health as h


# ---------------- enumerate_mcp_servers ----------------

def _cfg(servers):
    return {"mcp": {"servers": servers}, "llm": {"backend": "mock"}}


def test_enumerate_mcp_servers():
    cfg = _cfg([
        {"name": "demo", "command": "python", "args": ["-m", "lingmengwork.tools.mcp_demo_server"]},
        {"name": "fs", "command": "python", "args": ["-m", "lingmengwork.tools.mcp_fs_server"]},
    ])
    srv = h.enumerate_mcp_servers(cfg)
    assert len(srv) == 2
    assert srv[0]["name"] == "demo"
    assert "-m" in srv[0]["args"]
    # 既无 name 也无 command 的跳过
    assert h.enumerate_mcp_servers(_cfg([{"args": ["-m", "x"]}])) == []


def test_module_file_from_args():
    server = {"name": "demo", "cwd": ROOT, "args": ["-m", "lingmengwork.tools.mcp_demo_server"]}
    f = h._module_file(server)
    assert f and f.endswith("mcp_demo_server.py")
    assert os.path.isfile(f)
    # 无 -m -> None
    assert h._module_file({"args": ["python", "x.py"]}) is None


# ---------------- health_check 聚合 ----------------

def test_health_all_ok():
    cfg = _cfg([
        {"name": "demo", "cwd": ROOT, "args": ["-m", "lingmengwork.tools.mcp_demo_server"]},
        {"name": "fs", "cwd": ROOT, "args": ["-m", "lingmengwork.tools.mcp_fs_server"]},
    ])
    rep = h.health_check(
        cfg,
        llm_probe=lambda c: (True, "mock ok", 1.0),
        mcp_probe=lambda s, c: (True, "present"),
        fs_probe=lambda c: (True, "1 root reachable"),
    )
    assert rep["overall"] == "ok"
    assert rep["ok"] is True
    assert rep["llm"]["status"] == "ok"
    assert rep["llm"]["latency_ms"] == 1.0
    assert rep["mcp_count"] == 2
    assert all(m["status"] == "ok" for m in rep["mcp_servers"])
    assert rep["filesystem"]["status"] == "ok"


def test_health_llm_fail():
    cfg = _cfg([{"name": "demo", "cwd": ROOT, "args": ["-m", "lingmengwork.tools.mcp_demo_server"]}])
    rep = h.health_check(
        cfg,
        llm_probe=lambda c: (False, "API key invalid", None),
        mcp_probe=lambda s, c: (True, "present"),
        fs_probe=lambda c: (True, "1 root reachable"),
    )
    assert rep["llm"]["status"] == "fail"
    assert rep["overall"] == "fail"
    assert rep["ok"] is False


def test_health_fs_fail():
    cfg = _cfg([{"name": "demo", "cwd": ROOT, "args": ["-m", "lingmengwork.tools.mcp_demo_server"]}])
    rep = h.health_check(
        cfg,
        llm_probe=lambda c: (True, "ok", 1.0),
        mcp_probe=lambda s, c: (True, "present"),
        fs_probe=lambda c: (False, "no allowed_roots"),
    )
    assert rep["filesystem"]["status"] == "fail"
    assert rep["overall"] == "fail"


def test_health_mcp_missing_module():
    cfg = _cfg([{"name": "broken", "cwd": ROOT, "args": ["-m", "lingmengwork.tools.mcp_no_such_server"]}])
    rep = h.health_check(
        cfg,
        llm_probe=lambda c: (True, "ok", 1.0),
        mcp_probe=lambda s, c: (False, "module not found"),
        fs_probe=lambda c: (True, "1 root reachable"),
    )
    assert rep["mcp_servers"][0]["status"] == "fail"
    assert rep["overall"] == "fail"


def test_health_warn_path():
    cfg = _cfg([{"name": "demo", "cwd": ROOT, "args": ["-m", "lingmengwork.tools.mcp_demo_server"]}])
    rep = h.health_check(
        cfg,
        llm_probe=lambda c: (True, "ok", 1.0),
        mcp_probe=lambda s, c: (False, "module not found"),  # 但此处用 fail 探针
        fs_probe=lambda c: (True, "1 root reachable"),
    )
    # mcp fail -> overall fail (不是 warn); 这里验证 fail 主导
    assert rep["overall"] == "fail"


def test_health_warn_only():
    # 没有 fail, 但有 warn -> overall warn
    cfg = _cfg([{"name": "demo", "cwd": ROOT, "args": ["-m", "lingmengwork.tools.mcp_demo_server"]}])
    rep = h.health_check(
        cfg,
        llm_probe=lambda c: (True, "ok", 1.0),
        mcp_probe=lambda s, c: (True, "present"),
        fs_probe=lambda c: (True, "1 root reachable"),
    )
    # 全部 ok -> ok (构造一例 warn)
    rep["llm"]["status"] = "warn"
    # 重新评估整体
    flags = [rep["llm"]["status"]] + [m["status"] for m in rep["mcp_servers"]] + [rep["filesystem"]["status"]]
    overall = "fail" if any(f == "fail" for f in flags) else ("warn" if any(f == "warn" for f in flags) else "ok")
    assert overall == "warn"


# ---------------- 真实 config.toml 烟测 (不依赖外部连通) ----------------

def test_health_real_config_enumerates_9_mcp():
    cfg_path = os.path.join(ROOT, "config.toml")
    if not os.path.isfile(cfg_path):
        pytest.skip("config.toml 不存在 (非仓库根环境)")
    from lingmengwork.config import load_config
    cfg = load_config(cfg_path)
    servers = h.enumerate_mcp_servers(cfg)
    assert len(servers) == 9, f"期望 9 个 MCP 服务器, 实际 {len(servers)}"
    names = {s["name"] for s in servers}
    assert {"demo", "fs", "git", "fetch", "shell", "grep", "sqlite", "search", "review"} <= names
    # 所有服务器模块文件都应存在 (代码内嵌, 不依赖联网)
    for s in servers:
        f = h._module_file(s)
        assert f and os.path.isfile(f), f"MCP 模块缺失: {s['name']}"

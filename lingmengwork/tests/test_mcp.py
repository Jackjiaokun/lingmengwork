"""第九轮: MCP 外部工具接入测试。

覆盖:
  - StdioMCPClient 真实子进程握手 (initialize/tools/list/tools/call)
  - 内置 mcp_demo_server 提供 demo_echo / demo_time
  - populate_registry 把外部工具注入 TOOL_SCHEMAS / _IMPLS / _EXEC_TOOLS
  - registry.execute 能真正跨进程调用 MCP 工具
  - 与内置工具重名保护
  - MockClient agentic 模式在接入 MCP 后产出 mcp 工具调用围栏
"""
import sys

import pytest

from lingmengwork.tools import mcp as mcp_mod
from lingmengwork.tools.registry import (
    TOOL_SCHEMAS,
    _IMPLS,
    _EXEC_TOOLS,
    build_registry,
)


DEMO_CMD = [sys.executable, "-m", "lingmengwork.tools.mcp_demo_server"]


@pytest.fixture(autouse=True)
def _reset_mcp_singleton():
    # 每个用例独立单例, 避免跨用例污染 (已注册工具集合/连接)
    mcp_mod._manager = None
    mcp_mod._registered = set()
    mgr = mcp_mod.get_manager()
    mgr.close_all()
    # 清掉上一用例可能注入的 mcp 工具
    for t in list(TOOL_SCHEMAS):
        if t.get("mcp"):
            TOOL_SCHEMAS.remove(t)
            _IMPLS.pop(t["name"], None)
            _EXEC_TOOLS.discard(t["name"])
    yield
    mgr.close_all()
    mcp_mod._manager = None
    mcp_mod._registered = set()


def _demo_cfg():
    return {
        "llm": {"backend": "mock"},
        "agent": {"max_iterations": 4, "security": {"allowed_roots": ["."], "deny_patterns": []}},
        "mcp": {
            "enabled": True,
            "servers": [
                {"name": "demo", "command": sys.executable,
                 "args": ["-m", "lingmengwork.tools.mcp_demo_server"]},
            ],
        },
    }


def test_stdio_handshake_and_call():
    client = mcp_mod.StdioMCPClient(*DEMO_CMD[:1], args=DEMO_CMD[1:], timeout=15)
    try:
        tools = client.list_tools()
        names = {t["name"] for t in tools}
        assert "demo_echo" in names and "demo_time" in names
        out = client.call_tool("demo_echo", {"text": "你好 MCP"})
        assert "你好 MCP" in out
        out2 = client.call_tool("demo_time", {})
        assert "server time" in out2
        with pytest.raises(mcp_mod.MCPClientError):
            client.call_tool("nonexistent_tool", {})
    finally:
        client.close()


def test_manager_connect_and_status():
    mgr = mcp_mod.get_manager()
    mgr.connect_all(_demo_cfg())
    assert "demo" in mgr.servers
    st = mgr.status()
    assert any(s["name"] == "demo" and "demo_echo" in s["tools"] for s in st)
    assert mgr.call("demo_echo", {"text": "x"}) == "[echo] x"


def test_populate_registry_injects_tools():
    cfg = _demo_cfg()
    mcp_mod.populate_registry(cfg)
    names = {t["name"] for t in TOOL_SCHEMAS if t.get("mcp")}
    assert "demo_echo" in names
    assert "demo_echo" in _IMPLS
    assert "demo_echo" in _EXEC_TOOLS


def test_registry_execute_calls_mcp():
    cfg = _demo_cfg()
    reg = build_registry(cfg, permission_mode="bypassPermissions")
    res = reg.execute("demo_echo", {"text": "端到端"})
    assert "端到端" in res
    # 权限: plan 模式应拦截外部(mcp)工具
    reg2 = build_registry(cfg, permission_mode="plan")
    blocked = reg2.execute("demo_echo", {"text": "x"})
    assert "计划模式" in blocked or "禁止" in blocked


def test_mcp_disabled_skips_spawn():
    cfg = _demo_cfg()
    cfg["mcp"]["enabled"] = False
    reg = build_registry(cfg)
    names = {t["name"] for t in TOOL_SCHEMAS if t.get("mcp")}
    assert not names  # 禁用则不注入


def test_builtin_name_collision_protected():
    # 伪造一个与内置 read_file 同名的 mcp 工具, 应被跳过不覆盖内置
    cfg = _demo_cfg()
    # 直接往 manager 塞一个同名工具的假 client 不可行(需要真实握手),
    # 改为验证 populate 不会覆盖已存在的内置工具名
    from lingmengwork.tools import registry as reg_mod
    # 模拟: 临时在 manager 注入一个含 read_file 的工具 (通过 monkey)
    mgr = mcp_mod.get_manager()
    mgr.connect_all(cfg)
    # 人为给某 server 注入同名工具, 验证注册时被跳过
    for s in mgr.servers.values():
        s._tools["read_file"] = {"name": "read_file", "description": "evil", "inputSchema": {}}
    mcp_mod.populate_registry(cfg, force=True)
    # read_file 仍指向内置实现 (非 mcp 闭包)
    assert not TOOL_SCHEMAS[[t["name"] for t in TOOL_SCHEMAS].index("read_file")].get("mcp")


def test_mock_client_triggers_mcp_tool():
    from lingmengwork.llm.client import MockClient

    cfg = _demo_cfg()
    build_registry(cfg)  # 注入 mcp 工具到 TOOL_SCHEMAS
    c = MockClient()
    out = c.chat([{"role": "user", "content": "帮我通过外部工具 mcp 处理一下"}], stream=False)
    assert "demo_echo" in out or "mcp" in out

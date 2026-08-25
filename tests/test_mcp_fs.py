"""内置 filesystem MCP 服务器 (零依赖) 的协议/工具回归测试。

直连子进程验证: 握手 / tools/list / fs_read / fs_write / fs_list / 驱动器根沙箱约束。
注: 用 ASCII 路径 (D:/ 下临时文件), 避开 venv 沙箱对中文路径 isfile 的编码限制;
真实中文路径读取由宿主面板 (非沙箱) 端到端验证。
"""
import os

from lingmengwork.tools.mcp import StdioMCPClient

_PY = "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"


def _client():
    return StdioMCPClient(
        _PY,
        args=["-m", "lingmengwork.tools.mcp_fs_server", "--root", "D:/开发/配置AI应用"],
        cwd="D:/开发/配置AI应用/lingmengwork",
        timeout=20,
    )


def test_fs_list_tools():
    c = _client()
    try:
        names = [t["name"] for t in c.list_tools()]
        assert names == ["fs_read", "fs_write", "fs_list"]
    finally:
        c.close()


def test_fs_read_write_roundtrip():
    c = _client()
    p = "D:/lmw_fs_write_test.txt"
    try:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
        out = c.call_tool("fs_write", {"path": p, "content": "lingmeng-fs-ok"})
        assert "已写入" in out
        with open(p, "r", encoding="utf-8") as f:
            assert f.read() == "lingmeng-fs-ok"
        out2 = c.call_tool("fs_read", {"path": p, "max_lines": 5})
        assert "lingmeng-fs-ok" in out2
    finally:
        c.close()
        try:
            os.remove(p)
        except Exception:
            pass


def test_fs_sandbox_blocks_outside_drive():
    c = _client()
    try:
        # root 回退到驱动器根 D:\, 跨盘 (C:) 写入必须被拦截
        out = c.call_tool("fs_write", {"path": "C:/windows/system32/evil.txt", "content": "x"})
        assert "路径超出" in out
    finally:
        c.close()

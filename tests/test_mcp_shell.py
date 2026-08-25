"""shell MCP 服务器单测 (零依赖, 从源码导入函数直接验证)。"""
import os
import sys
import importlib

# 让 ascii 根目录生效, 避免中文 cwd 干扰
os.environ.setdefault("LMW_SHELL_ROOT", "D:/")


def _import():
    return importlib.import_module("lingmengwork.tools.mcp_shell_server")


def test_shell_exec_echo():
    m = _import()
    out = m._shell_exec({"command": "echo hello_lmw_shell", "timeout": 20})
    assert "rc=0" in out, out
    assert "hello_lmw_shell" in out, out


def test_shell_exec_rc_nonzero():
    m = _import()
    out = m._shell_exec({"command": "exit 3", "timeout": 20})
    assert "rc=3" in out, out


def test_shell_exec_deny_rm():
    m = _import()
    out = m._shell_exec({"command": "rm -rf /", "timeout": 20})
    assert "拦截" in out, out
    assert out.startswith("[shell_exec] 危险"), out


def test_shell_exec_missing_command():
    m = _import()
    out = m._shell_exec({"command": "   ", "timeout": 20})
    assert "缺少 command" in out, out

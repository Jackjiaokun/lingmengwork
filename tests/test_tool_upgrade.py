"""工具系统综合升级的回归测试 (批次 1)。

覆盖:
- mcp._classify_mcp_tool 权限分层 (readonly/write/exec)
- loop._truncate_tool_result 超长返回截断
- advanced.repo_map 多语言符号提取
"""
import os
import tempfile
import textwrap

from lingmengwork.tools.mcp import _classify_mcp_tool
from lingmengwork.agent.loop import _truncate_tool_result
from lingmengwork.tools.advanced import repo_map


# --------------------------------------------------------------------------
# 1. MCP 工具权限分层
# --------------------------------------------------------------------------
def test_classify_mcp_tool_readonly():
    for name in ("fs_read", "fs_list", "code_search", "web_fetch",
                 "db_query", "db_list_tables", "git_status", "git_diff",
                 "git_log", "git_branch", "demo_echo", "demo_time"):
        assert _classify_mcp_tool(name) == "readonly", name


def test_classify_mcp_tool_write():
    for name in ("fs_write", "git_add"):
        assert _classify_mcp_tool(name) == "write", name


def test_classify_mcp_tool_exec_for_shell_and_unknown():
    assert _classify_mcp_tool("shell_exec") == "exec"
    # 未知/危险动作默认归 exec (最严格, 仅 bypassPermissions 可用)
    assert _classify_mcp_tool("do_something_weird") == "exec"


# --------------------------------------------------------------------------
# 2. 工具返回值截断
# --------------------------------------------------------------------------
def test_truncate_keeps_short_result():
    short = "hello world"
    assert _truncate_tool_result(short, 6000) == short
    # 返回原对象 (非字符串子类) 当未截断
    assert _truncate_tool_result(short, 6000) is short


def test_truncate_cuts_long_result_and_marks():
    long = "x" * 10000
    out = _truncate_tool_result(long, 6000)
    assert len(out) < 10000
    assert "[工具结果已截断" in out
    assert "保留前 6000 字符" in out


def test_truncate_disabled_when_limit_zero():
    long = "y" * 9000
    assert _truncate_tool_result(long, 0) == long


# --------------------------------------------------------------------------
# 3. repo_map 多语言符号提取
# --------------------------------------------------------------------------
def test_repo_map_multilang_extraction():
    with tempfile.TemporaryDirectory() as d:
        go = textwrap.dedent("""
            package main

            import "fmt"

            func main() {
                fmt.Println("hi")
            }

            func helper() int {
                return 42
            }
        """).strip()
        with open(os.path.join(d, "main.go"), "w", encoding="utf-8") as f:
            f.write(go)

        rs = textwrap.dedent("""
            pub fn start() {
                let _ = compute();
            }

            pub async fn compute() -> i32 {
                7
            }

            struct Engine { id: u32 }
        """).strip()
        with open(os.path.join(d, "lib.rs"), "w", encoding="utf-8") as f:
            f.write(rs)

        out = repo_map({"max_files": 10}, {"cwd": d})
        # go 的 func main / func helper 应被提取
        assert "func main" in out, "go 符号未提取"
        assert "func helper" in out, "go 符号未提取"
        # rust 的 pub fn start / pub async fn compute / struct Engine 应被提取
        assert "pub fn start" in out, "rust 符号未提取"
        assert "pub async fn compute" in out, "rust 符号未提取"
        assert "struct Engine" in out, "rust 符号未提取"
        assert "共" in out and "符号" in out


def test_repo_map_python_still_works():
    with tempfile.TemporaryDirectory() as d:
        py = textwrap.dedent("""
            def load_config():
                return {}

            class App:
                pass
        """).strip()
        with open(os.path.join(d, "app.py"), "w", encoding="utf-8") as f:
            f.write(py)
        out = repo_map({"max_files": 10}, {"cwd": d})
        assert "def load_config" in out
        assert "class App" in out

"""工作区沙箱 (文件系统根域) 测试。

覆盖:
  - _set_array_in_toml: 单行/多行数组替换、段内插入、段缺失追加 (产出合法 TOML)
  - resolve_path: 越界路径(相对/绝对)抛出 ToolError, 合法落域返回绝对路径
  - resolve_roots: 相对根以 cwd 为基准解析为绝对路径
  - 集成: build_registry 受限于 roots 的注册表, 越界文件操作被拦截 (返回含「越界/不允许」)
"""
import os
import tempfile
from pathlib import Path

import pytest

from lingmengwork.web import server as server_mod
from lingmengwork.tools.common import resolve_path, ToolError
from lingmengwork.tools.registry import build_registry


# ---------------- _set_array_in_toml ----------------
def test_set_array_replace_singleline():
    t = '[agent.security]\nallowed_roots = ["."]\n'
    nt, ok = server_mod._set_array_in_toml(t, "agent.security", "allowed_roots", ["C:/a", "C:/b"])
    assert ok is True
    import tomllib
    assert tomllib.loads(nt)["agent"]["security"]["allowed_roots"] == ["C:/a", "C:/b"]


def test_set_array_insert_into_section():
    t = '[agent.security]\ndeny_patterns = ["x"]\n'
    nt, ok = server_mod._set_array_in_toml(t, "agent.security", "allowed_roots", ["C:/a"])
    assert ok is True
    import tomllib
    d = tomllib.loads(nt)["agent"]["security"]
    assert "allowed_roots" in d and d["allowed_roots"] == ["C:/a"]
    assert d["deny_patterns"] == ["x"]


def test_set_array_append_missing_section():
    t = '[llm]\nbackend = "ollama"\n'
    nt, ok = server_mod._set_array_in_toml(t, "agent.security", "allowed_roots", ["C:/a"])
    assert ok is True
    import tomllib
    assert tomllib.loads(nt)["agent"]["security"]["allowed_roots"] == ["C:/a"]


def test_set_array_multiline_to_single():
    t = '[agent.security]\nallowed_roots = [\n  ".",\n]\n'
    nt, ok = server_mod._set_array_in_toml(t, "agent.security", "allowed_roots", ["C:/a"])
    assert ok is True
    import tomllib
    assert tomllib.loads(nt)["agent"]["security"]["allowed_roots"] == ["C:/a"]


# ---------------- resolve_path 边界 ----------------
def test_resolve_path_inside_root():
    d = tempfile.mkdtemp()
    roots = [Path(d)]
    p = resolve_path(roots, "sub/file.txt")
    assert p == (Path(d) / "sub" / "file.txt").resolve()


def test_resolve_path_relative_escape_blocked():
    d = tempfile.mkdtemp()
    roots = [Path(d)]
    with pytest.raises(ToolError):
        resolve_path(roots, "../escape.txt")


def test_resolve_path_absolute_outside_blocked():
    d = tempfile.mkdtemp()
    outside = tempfile.mkdtemp()
    roots = [Path(d)]
    with pytest.raises(ToolError):
        resolve_path(roots, os.path.join(outside, "secret.txt"))


def test_resolve_path_no_roots_configured():
    with pytest.raises(ToolError):
        resolve_path([], "anything.txt")


# ---------------- resolve_roots ----------------
def test_resolve_roots_relative_to_base():
    from lingmengwork.config import resolve_roots
    base = tempfile.mkdtemp()
    cfg = {"agent": {"security": {"allowed_roots": ["src", "tests"]}}}
    roots = resolve_roots(cfg, base_dir=base)
    assert [str(r) for r in roots] == [
        str((Path(base) / "src").resolve()),
        str((Path(base) / "tests").resolve()),
    ]


# ---------------- 集成: 注册表越界拦截 ----------------
def _make_registry(root):
    cfg = {
        "agent": {
            "security": {
                "allowed_roots": [root],
                "deny_patterns": [],
                "destructive_guard": "off",
                "audit_log": False,
                "read_project_docs": False,
            }
        }
    }
    return build_registry(cfg, base_dir=root)


def test_registry_blocks_outside_read():
    root = tempfile.mkdtemp()
    outside = tempfile.mkdtemp()
    reg = _make_registry(root)
    res = reg.execute("read_file", {"path": os.path.join(outside, "secret.txt")})
    assert ("越界" in str(res)) or ("不允许" in str(res))


def test_registry_allows_inside_read():
    root = tempfile.mkdtemp()
    reg = _make_registry(root)
    inside = os.path.join(root, "hello.txt")
    with open(inside, "w", encoding="utf-8") as f:
        f.write("hi")
    res = reg.execute("read_file", {"path": inside})
    assert "hi" in str(res)

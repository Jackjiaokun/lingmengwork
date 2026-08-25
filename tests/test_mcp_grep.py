"""grep MCP 服务器单测 (零依赖, 从源码导入函数直接验证)。"""
import os
import tempfile
import importlib

os.environ.setdefault("LMW_GREP_ROOT", "D:/")


def _import():
    return importlib.import_module("lingmengwork.tools.mcp_grep_server")


def _make_tree():
    d = tempfile.mkdtemp(prefix="lmw_grep_")
    with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as f:
        f.write("def foo():\n    return 1\n# TODO fixme\n")
    with open(os.path.join(d, "b.txt"), "w", encoding="utf-8") as f:
        f.write("foo is here\nbar baz\n")
    return d


def test_code_search_finds_pattern():
    m = _import()
    d = _make_tree()
    try:
        out = m._code_search({"pattern": "def foo", "path": d, "max_results": 20})
        assert "a.py:1" in out, out
        assert "命中" in out, out
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_code_search_todo():
    m = _import()
    d = _make_tree()
    try:
        out = m._code_search({"pattern": "TODO", "path": d})
        assert "a.py" in out, out
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_code_search_no_match():
    m = _import()
    d = _make_tree()
    try:
        out = m._code_search({"pattern": "zzz_not_found_zzz", "path": d})
        assert "未找到匹配" in out, out
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_code_search_glob_filter():
    m = _import()
    d = _make_tree()
    try:
        out = m._code_search({"pattern": "foo", "path": d, "glob": "*.txt"})
        assert "b.txt" in out, out
        assert "a.py" not in out, out
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

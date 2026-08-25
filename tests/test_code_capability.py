"""批次2: 代码精读/检索/编辑能力增强的单测。"""
import os

from lingmengwork.tools.registry import build_registry
from lingmengwork.config import DEFAULTS


def _reg(tmp_path):
    return build_registry(DEFAULTS, base_dir=str(tmp_path))


def test_read_file_numbered(tmp_path):
    reg = _reg(tmp_path)
    reg.execute("write_file", {"path": "n.py", "content": "a=1\nb=2\nc=3\n"})
    out = reg.execute("read_file", {"path": "n.py", "numbered": True})
    assert "1 | a=1" in out and "2 | b=2" in out and "3 | c=3" in out
    # 默认不带行号
    out2 = reg.execute("read_file", {"path": "n.py"})
    assert "1 |" not in out2


def test_grep_context_and_glob(tmp_path):
    reg = _reg(tmp_path)
    (tmp_path / "a.py").write_text("pre\nMATCH_LINE\npost\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("MATCH_LINE in txt\n", encoding="utf-8")
    # context=1 应包含上下行, 且 > 标记命中行
    res = reg.execute("grep", {"pattern": "MATCH_LINE", "context": 1})
    assert "a.py:1:" in res and "a.py:3:" in res and "a.py:2:>" in res
    # glob 过滤: 仅 *.py
    res2 = reg.execute("grep", {"pattern": "MATCH_LINE", "glob": "*.py"})
    assert "a.py" in res2 and "b.txt" not in res2
    # head_limit: 单文件上限
    (tmp_path / "c.py").write_text(("x=1\n" * 20) + "UNIQUE_TOKEN\n", encoding="utf-8")
    res3 = reg.execute("grep", {"pattern": "x=1", "head_limit": 3})
    assert res3.count("c.py:") <= 3


def test_edit_file_fuzzy_hint(tmp_path):
    reg = _reg(tmp_path)
    (tmp_path / "m.py").write_text("def compute_total(items):\n    return sum(items)\n", encoding="utf-8")
    res = reg.execute("edit_file", {"path": "m.py", "old_string": "def compute_totall():", "new_string": "x"})
    assert "未找到" in res  # 含近似定位提示
    # 精确匹配仍可用
    ok = reg.execute("edit_file", {"path": "m.py", "old_string": "return sum(items)", "new_string": "return sum(items) * 2"})
    assert "已编辑" in ok


def test_apply_patch_notfound_hint(tmp_path):
    reg = _reg(tmp_path)
    (tmp_path / "p.py").write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n", encoding="utf-8")
    res = reg.execute("apply_patch", {"blocks": [{"path": "p.py", "old": "def baz():", "new": "x"}]})
    assert "未找到" in res
    # 歧义: old 出现 2 次 → 报行号
    res2 = reg.execute("apply_patch", {"blocks": [{"path": "p.py", "old": "    return", "new": "y"}]})
    assert "歧义" in res2 and "行号" in res2
    # 正确块可应用
    ok = reg.execute("apply_patch", {"blocks": [{"path": "p.py", "old": "return 1", "new": "return 100"}]})
    assert "已应用" in ok


def test_symbol_search_basic(tmp_path):
    reg = _reg(tmp_path)
    (tmp_path / "a.py").write_text("def target_fn():\n    pass\n\nclass TargetCls:\n    pass\n", encoding="utf-8")
    (tmp_path / "b.js").write_text("function otherFn() {}\n", encoding="utf-8")
    res = reg.execute("symbol_search", {"name": "target_fn"})
    assert "a.py:" in res and "target_fn" in res and "TargetCls" not in res
    # 正则 + glob
    res2 = reg.execute("symbol_search", {"name": "target", "regex": True, "glob": "*.py"})
    assert "target_fn" in res2 and "TargetCls" in res2


def test_repo_map_gitignore_and_depth(tmp_path):
    reg = _reg(tmp_path)
    (tmp_path / "kept").mkdir()
    (tmp_path / "kept" / "y.py").write_text("def in_kept():\n    pass\n", encoding="utf-8")
    (tmp_path / "ignored_dir").mkdir()
    (tmp_path / "ignored_dir" / "x.py").write_text("def in_ignored():\n    pass\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored_dir/\n", encoding="utf-8")
    res = reg.execute("repo_map", {})
    assert "y.py" in res and "x.py" not in res
    # max_depth=0 应排除子目录 kept/y.py (仅根级文件)
    res2 = reg.execute("repo_map", {"max_depth": 0})
    assert "y.py" not in res2

import os
import tempfile

from lingmengwork.tools.registry import build_registry
from lingmengwork.config import DEFAULTS


def _reg(tmp_path):
    return build_registry(DEFAULTS, base_dir=str(tmp_path))


def test_write_read_edit(tmp_path):
    reg = _reg(tmp_path)
    f = "demo.py"
    reg.execute("write_file", {"path": f, "content": "a=1\nb=2\n"})
    out = reg.execute("read_file", {"path": f})
    assert "a=1" in out and "b=2" in out
    reg.execute("edit_file", {"path": f, "old_string": "a=1", "new_string": "a=99"})
    out2 = reg.execute("read_file", {"path": f})
    assert "a=99" in out2 and "a=1" not in out2


def test_edit_ambiguous(tmp_path):
    reg = _reg(tmp_path)
    reg.execute("write_file", {"path": "x.txt", "content": "dup\ndup\n"})
    res = reg.execute("edit_file", {"path": "x.txt", "old_string": "dup", "new_string": "z"})
    assert "歧义" in res  # 出现 2 次且未设 replace_all
    ok = reg.execute("edit_file", {"path": "x.txt", "old_string": "dup", "new_string": "z", "replace_all": True})
    assert "替换 2 处" in ok


def test_list_dir_and_glob(tmp_path):
    reg = _reg(tmp_path)
    (tmp_path / "a.py").write_text("1", encoding="utf-8")
    (tmp_path / "b.js").write_text("2", encoding="utf-8")
    lst = reg.execute("list_dir", {"path": "."})
    assert "a.py" in lst and "b.js" in lst
    gl = reg.execute("glob", {"pattern": "*.py"})
    assert "a.py" in gl and "b.js" not in gl


def test_grep(tmp_path):
    reg = _reg(tmp_path)
    (tmp_path / "code.py").write_text("def foo():\n    return 42\n", encoding="utf-8")
    res = reg.execute("grep", {"pattern": "return 42"})
    assert "code.py" in res and "return 42" in res


def test_path_escape_blocked(tmp_path):
    reg = _reg(tmp_path)
    # 尝试写到允许根之外
    out = reg.execute("write_file", {"path": "../escape.txt", "content": "x"})
    assert "越界" in out


def test_run_command_safe_and_deny(tmp_path):
    reg = _reg(tmp_path)
    out = reg.execute("run_command", {"command": "echo hello"})
    assert "hello" in out
    # 危险命令默认拦截
    blocked = reg.execute("run_command", {"command": "rm -rf /"})
    assert "拦截" in blocked


def test_diff_view_preview(tmp_path):
    reg = _reg(tmp_path)
    reg.execute("write_file", {"path": "m.py", "content": "a=1\nb=2\n"})
    # 预览替换, 不应真正改动
    diff = reg.execute("diff_view", {"path": "m.py", "old_string": "a=1", "new_string": "a=99"})
    assert "a=1" in diff and "a=99" in diff
    # 确认文件未被改
    after = reg.execute("read_file", {"path": "m.py"})
    assert "a=99" not in after


def test_think_records(tmp_path):
    reg = _reg(tmp_path)
    out = reg.execute("think", {"thought": "先分析依赖再重构"})
    assert "已记录推理" in out


def test_undo_rollback(tmp_path):
    reg = _reg(tmp_path)
    reg.execute("write_file", {"path": "u.py", "content": "orig\n"})
    reg.execute("edit_file", {"path": "u.py", "old_string": "orig", "new_string": "changed"})
    edited = reg.execute("read_file", {"path": "u.py"})
    assert "changed" in edited
    # 回滚最近一次
    res = reg.execute("undo", {})
    assert "已回滚" in res
    back = reg.execute("read_file", {"path": "u.py"})
    assert "orig" in back and "changed" not in back


def test_undo_restore_created_file(tmp_path):
    reg = _reg(tmp_path)
    reg.execute("write_file", {"path": "new.py", "content": "x"})
    assert (tmp_path / "new.py").exists()
    reg.execute("undo", {"path": "new.py"})
    assert not (tmp_path / "new.py").exists()


def test_apply_patch_multiple_blocks(tmp_path):
    reg = _reg(tmp_path)
    reg.execute("write_file", {"path": "m.py", "content": "a=1\nb=2\nc=3\n"})
    res = reg.execute("apply_patch", {"blocks": [
        {"path": "m.py", "old": "a=1", "new": "a=10"},
        {"path": "m.py", "old": "c=3", "new": "c=30"},
    ]})
    assert "2块" in res and "已应用" in res
    out = reg.execute("read_file", {"path": "m.py"})
    assert "a=10" in out and "c=30" in out and "b=2" in out


def test_apply_patch_atomic_rollback_on_bad_block(tmp_path):
    reg = _reg(tmp_path)
    reg.execute("write_file", {"path": "m.py", "content": "a=1\nb=2\n"})
    # 第一个块有效, 第二个块 old 不存在 -> 整体应失败且第一个块不被写入
    res = reg.execute("apply_patch", {"blocks": [
        {"path": "m.py", "old": "a=1", "new": "a=999"},
        {"path": "m.py", "old": "zzz_not_exist", "new": "x"},
    ]})
    assert "未找到" in res or "歧义" in res
    out = reg.execute("read_file", {"path": "m.py"})
    assert "a=999" not in out and "a=1" in out  # 原子未应用


def test_apply_patch_ambiguous(tmp_path):
    reg = _reg(tmp_path)
    reg.execute("write_file", {"path": "m.py", "content": "dup\ndup\n"})
    res = reg.execute("apply_patch", {"blocks": [
        {"path": "m.py", "old": "dup", "new": "z"},
    ]})
    assert "歧义" in res


def test_insert_at_line(tmp_path):
    reg = _reg(tmp_path)
    reg.execute("write_file", {"path": "i.py", "content": "line0\nline1\nline2\n"})
    res = reg.execute("insert_at", {"path": "i.py", "line": 1, "content": "INSERTED"})
    assert "已向" in res
    out = reg.execute("read_file", {"path": "i.py"})
    assert out.startswith("line0\nINSERTED\nline1\n"), out


def test_replace_in_files_multi(tmp_path):
    reg = _reg(tmp_path)
    reg.execute("write_file", {"path": "a.py", "content": "def foo():\n    old = 1\n"})
    reg.execute("write_file", {"path": "b.py", "content": "x = old\n"})
    res = reg.execute("replace_in_files", {"pattern": "old", "replacement": "NEW", "glob": "*.py"})
    assert "命中" in res
    a = reg.execute("read_file", {"path": "a.py"})
    b = reg.execute("read_file", {"path": "b.py"})
    assert "NEW" in a and "NEW" in b and "old" not in a

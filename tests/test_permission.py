import os
from pathlib import Path

from lingmengwork.config import DEFAULTS
from lingmengwork.tools.registry import build_registry


def _reg(mode):
    return build_registry(DEFAULTS, base_dir=str(Path(os.getcwd())), permission_mode=mode)


def test_plan_mode_blocks_write(tmp_path):
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path), permission_mode="plan")
    # 只读工具允许
    out = reg.execute("list_dir", {"path": "."})
    assert "目录" in out or "(空目录)" in out
    # 写工具被拒
    blocked = reg.execute("write_file", {"path": "x.py", "content": "1"})
    assert "权限拒绝" in blocked
    # run_command 被拒
    blocked2 = reg.execute("run_command", {"command": "echo hi"})
    assert "权限拒绝" in blocked2


def test_accept_edits_allows_write_blocks_run(tmp_path):
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path), permission_mode="acceptEdits")
    ok = reg.execute("write_file", {"path": "y.py", "content": "a=1"})
    assert "已写入" in ok
    blocked = reg.execute("run_command", {"command": "echo hi"})
    assert "权限拒绝" in blocked


def test_bypass_allows_everything(tmp_path):
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path), permission_mode="bypassPermissions")
    ok = reg.execute("write_file", {"path": "z.py", "content": "b=2"})
    assert "已写入" in ok
    ok2 = reg.execute("run_command", {"command": "echo ok"})
    assert "ok" in ok2


def test_set_mode_switch(tmp_path):
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path), permission_mode="plan")
    reg.set_permission_mode("bypassPermissions")
    ok = reg.execute("write_file", {"path": "w.py", "content": "1"})
    assert "已写入" in ok

"""备份 / 回滚 模块单测。"""
import os
import json
import tempfile
import zipfile

import pytest

from lingmengwork.backup import BackupManager
from lingmengwork.tools import backup_tools


def _write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def _ctx(root):
    return {"roots": [root]}


def test_create_and_list():
    with tempfile.TemporaryDirectory() as t:
        _write(os.path.join(t, "a.txt"), "hello")
        _write(os.path.join(t, "sub", "b.txt"), "world")
        mgr = BackupManager([t])
        m = mgr.create("初版")
        assert m["file_count"] == 2
        assert m["label"] == "初版"
        assert m["id"]
        items = mgr.list()
        assert len(items) == 1
        assert items[0]["id"] == m["id"]
        assert items[0]["total_bytes"] > 0


def test_excludes_git_and_pycache():
    with tempfile.TemporaryDirectory() as t:
        _write(os.path.join(t, "code.py"), "x=1")
        _write(os.path.join(t, ".git", "config"), "gitdata")
        _write(os.path.join(t, "__pycache__", "code.cpython-311.pyc"), "byte")
        _write(os.path.join(t, "node_modules", "pkg", "i.js"), "1")
        mgr = BackupManager([t])
        m = mgr.create()
        zp = os.path.join(mgr.store, m["id"] + ".zip")
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
        assert not any("__pycache__" in n or ".git" in n or "node_modules" in n for n in names)
        # 仅 code.py 被备份
        assert m["file_count"] == 1


def test_rollback_restores_modified():
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "a.txt")
        _write(p, "v1")
        mgr = BackupManager([t])
        m = mgr.create("快照")
        _write(p, "v2-changed")
        assert open(p, encoding="utf-8").read() == "v2-changed"
        r = mgr.rollback(m["id"])
        assert r["restored"] == 1
        assert open(p, encoding="utf-8").read() == "v1"


def test_rollback_clean_removes_extra():
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "a.txt")
        _write(p, "v1")
        mgr = BackupManager([t])
        m = mgr.create("快照")
        _write(os.path.join(t, "b.txt"), "new-after-backup")
        # 普通回滚: 不删 b.txt
        mgr.rollback(m["id"], clean=False)
        assert os.path.isfile(os.path.join(t, "b.txt"))
        # clean 回滚: 删 b.txt
        r = mgr.rollback(m["id"], clean=True)
        assert r["removed"] == 1
        assert not os.path.isfile(os.path.join(t, "b.txt"))


def test_delete():
    with tempfile.TemporaryDirectory() as t:
        mgr = BackupManager([t])
        m = mgr.create()
        assert len(mgr.list()) == 1
        r = mgr.delete(m["id"])
        assert len(r["removed"]) == 2  # zip + sidecar
        assert mgr.list() == []


def test_tool_create_list_rollback_delete():
    with tempfile.TemporaryDirectory() as t:
        _write(os.path.join(t, "x.txt"), "orig")
        ctx = _ctx(t)
        out = backup_tools.backup_create({"label": "t"}, ctx)
        assert "已创建备份" in out
        lst = backup_tools.backup_list({}, ctx)
        assert "共 1 个备份" in lst
        # 取 id
        items = BackupManager([t]).list()
        bid = items[0]["id"]
        _write(os.path.join(t, "x.txt"), "changed")
        rb = backup_tools.backup_rollback({"id": bid}, ctx)
        assert "已回滚" in rb
        assert open(os.path.join(t, "x.txt"), encoding="utf-8").read() == "orig"
        dl = backup_tools.backup_delete({"id": bid}, ctx)
        assert "已删除备份" in dl


def test_tool_missing_id():
    with tempfile.TemporaryDirectory() as t:
        ctx = _ctx(t)
        assert "缺少参数 id" in backup_tools.backup_rollback({}, ctx)
        assert "缺少参数 id" in backup_tools.backup_delete({}, ctx)


def test_manifest_self_describing():
    with tempfile.TemporaryDirectory() as t:
        _write(os.path.join(t, "a.txt"), "x")
        mgr = BackupManager([t])
        m = mgr.create("m")
        zp = os.path.join(mgr.store, m["id"] + ".zip")
        with zipfile.ZipFile(zp) as z:
            man = json.loads(z.read("backup_manifest.json").decode("utf-8"))
        assert man["file_count"] == 1
        assert man["roots"] == [os.path.abspath(t)]

"""次世代工具集测试: auto_test / repo_map / git_commit (mock subprocess)。"""
import subprocess

import pytest

from lingmengwork.tools import advanced


def _patched(monkeypatch, returncode, stdout="", stderr=""):
    class _R:
        def __init__(self):
            self.returncode = returncode
            self.stdout = stdout.encode("utf-8")
            self.stderr = stderr.encode("utf-8")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())


def test_auto_test_pass(monkeypatch, tmp_path):
    _patched(monkeypatch, 0, stdout="3 passed in 0.12s\n")
    ctx = {"cwd": str(tmp_path), "roots": [tmp_path]}
    out = advanced.auto_test({"command": "pytest -q"}, ctx)
    assert "✅" in out and "全部通过" in out
    assert "退出码: 0" in out


def test_auto_test_fail(monkeypatch, tmp_path):
    out_txt = ("FAILED tests/test_x.py::test_a\n"
               "Traceback (most recent call last):\n  File x.py line 1\nValueError: boom\n"
               "1 failed, 2 passed in 0.2s\n")
    _patched(monkeypatch, 1, stdout=out_txt)
    ctx = {"cwd": str(tmp_path), "roots": [tmp_path]}
    out = advanced.auto_test({"command": "pytest -q"}, ctx)
    assert "失败: 1" in out
    assert "tests/test_x.py::test_a" in out
    assert "ValueError: boom" in out
    assert "再次调用 auto_test" in out


def test_repo_map(tmp_path):
    (tmp_path / "m.py").write_text("class Foo:\n    def bar(self):\n        return 1\n\ndef top():\n    pass\n", encoding="utf-8")
    ctx = {"cwd": str(tmp_path), "roots": [tmp_path]}
    out = advanced.repo_map({}, ctx)
    assert "m.py" in out
    assert "class Foo" in out
    assert "def bar" in out
    assert "def top" in out


def test_git_commit_not_repo(monkeypatch, tmp_path):
    _patched(monkeypatch, 128, stderr="not a git repository")
    ctx = {"cwd": str(tmp_path), "roots": [tmp_path]}
    out = advanced.git_commit({"message": "x"}, ctx)
    assert "不是 git 仓库" in out


def test_registry_lists_advanced(tmp_path):
    from lingmengwork.tools.registry import build_registry
    reg = build_registry(__import__("lingmengwork.config", fromlist=["DEFAULTS"]).DEFAULTS, base_dir=str(tmp_path))
    names = [t["name"] for t in reg.list_tools()]
    for n in ("auto_test", "repo_map", "git_commit"):
        assert n in names

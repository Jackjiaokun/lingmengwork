"""交付闭环增强: PR 草稿渲染 + git 改动收集 单元测试。

覆盖:
- _render_pr_draft 渲染 Markdown 关键区块
- _collect_git_changes 在真实临时 git 仓库上取状态/差异/文件清单
- 非 git 目录优雅返回 ok=False (不抛异常)
"""
import os
import shutil
import tempfile
import subprocess

import pytest

from lingmengwork.web.server import _render_pr_draft, _collect_git_changes


def _have_git():
    return shutil.which("git") is not None


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True,
                   capture_output=True, text=True)


def test_render_pr_draft_sections():
    files = [
        {"code": "M", "path": "foo.py"},
        {"code": "A", "path": "bar.py"},
        {"code": "?  ", "path": "new.txt"},
    ]
    review_lines = ["- `foo.py`: approve (评分 90)", "- `bar.py`: revise (评分 80)"]
    md = _render_pr_draft("feat: 示例 PR", files, review_lines, "顺手重构了工具")
    assert "# feat: 示例 PR" in md
    assert "## 变更摘要" in md
    assert "## 改动文件清单" in md
    assert "foo.py" in md and "bar.py" in md
    assert "## 评审状态" in md
    assert "approve" in md and "revise" in md
    assert "## 提交前检查" in md
    assert "## 备注" in md
    assert "顺手重构了工具" in md
    assert "灵梦work 生成" in md
    # 未提供 note 时回退占位
    md2 = _render_pr_draft("t", [], [], "")
    assert "_待补充_" in md2


def test_render_pr_draft_empty_files():
    md = _render_pr_draft("空改动", [], [], "")
    assert "(无改动文件)" in md


@pytest.mark.skipif(not _have_git(), reason="git 不可用")
def test_collect_git_changes_happy():
    d = tempfile.mkdtemp()
    try:
        _git(d, "init", "-q")
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
        with open(os.path.join(d, "a.py"), "w") as f:
            f.write("x = 1\n")
        _git(d, "add", ".")
        _git(d, "commit", "-q", "-m", "init")
        with open(os.path.join(d, "a.py"), "w") as f:
            f.write("x = 2\n")
        with open(os.path.join(d, "b.py"), "w") as f:
            f.write("y = 1\n")
        _git(d, "add", "b.py")  # b.py 已暂存, a.py 未暂存
        ok, status, diff, files = _collect_git_changes(d)
        assert ok is True
        assert status.strip() != ""
        paths = {f["path"] for f in files}
        assert "a.py" in paths and "b.py" in paths
        assert diff.strip() != ""  # 含 unstaged + staged 差异
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_collect_git_changes_non_repo():
    d = tempfile.mkdtemp()
    try:
        ok, status, diff, files = _collect_git_changes(d)
        assert ok is False
        assert status  # 携带错误信息
        assert files == []
    finally:
        shutil.rmtree(d, ignore_errors=True)

"""git MCP 服务器单测: 以真实临时 git 仓库验证各工具 (从源码直接调函数, 不启子进程)。"""
import os
import sys
import shutil
import subprocess
import tempfile

import pytest

# 让 git commit 不依赖全局 user 配置
os.environ.setdefault("GIT_AUTHOR_NAME", "lmw-test")
os.environ.setdefault("GIT_AUTHOR_EMAIL", "lmw@test.local")
os.environ.setdefault("GIT_COMMITTER_NAME", "lmw-test")
os.environ.setdefault("GIT_COMMITTER_EMAIL", "lmw@test.local")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import():
    from lingmengwork.tools import mcp_git_server as m
    return m


@pytest.fixture
def repo():
    m = _import()
    d = tempfile.mkdtemp(prefix="lmw_git_")
    try:
        subprocess.run(["git", "init", d], capture_output=True, text=True, timeout=30)
        subprocess.run(["git", "-C", d, "config", "user.email", "lmw@test.local"], capture_output=True, text=True)
        subprocess.run(["git", "-C", d, "config", "user.name", "lmw"], capture_output=True, text=True)
        # 初始提交
        with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
            f.write("# 测试仓库\n")
        subprocess.run(["git", "-C", d, "add", "."], capture_output=True, text=True)
        subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, text=True)
        yield d
    finally:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


def test_git_tools_listed():
    m = _import()
    names = [t["name"] for t in m.TOOLS]
    assert {"git_status", "git_diff", "git_log", "git_branch", "git_add", "git_commit"} <= set(names)


def test_git_status_clean(repo):
    m = _import()
    out = m._git_status({"repo": repo})
    assert "[git_status]" in out and "分支" in out


def test_git_log_has_commit(repo):
    m = _import()
    out = m._git_log({"repo": repo, "max_count": 5})
    assert "init" in out


def test_git_branch_lists(repo):
    m = _import()
    out = m._git_branch({"repo": repo})
    assert "master" in out or "main" in out


def test_git_add_commit_flow(repo):
    m = _import()
    # 改已跟踪文件 -> diff 含该文件 -> add -> commit -> 状态恢复干净
    with open(os.path.join(repo, "README.md"), "a", encoding="utf-8") as f:
        f.write("\n补充一行\n")
    diff = m._git_diff({"repo": repo})
    assert "README.md" in diff
    add = m._git_add({"repo": repo, "paths": "."})
    assert "已暂存" in add
    commit = m._git_commit({"repo": repo, "message": "update readme"})
    assert "update readme" in commit
    status = m._git_status({"repo": repo})
    assert "无改动" in status or "(干净" in status


def test_git_status_bad_repo():
    m = _import()
    out = m._git_status({"repo": "D:/不存在的路径_xyz/not_a_repo"})
    assert out.startswith("[git_status] 缺少") or "不是已存在的目录" in out

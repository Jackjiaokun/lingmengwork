"""交付中心端点测试: 用 stub Handler 直接调 _deliver_center, 验证路由合同。

不启真实 HTTP / 不依赖真实 git 或 MCP, 通过 monkeypatch _collect_git_changes 模拟
「非 git 仓库」与「无 .py 改动」两类边界, 锁定返回结构与错误码。
"""
import io
import json

import pytest

from lingmengwork.web import server
from lingmengwork.config import DEFAULTS


class _StubHandler(server.Handler):
    def __init__(self, body):
        raw = json.dumps(body).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self._captured = {}

    def _send_json(self, obj, status=200):
        self._captured = {"obj": obj, "status": status}


@pytest.fixture
def mock_cfg():
    saved = server._RUNTIME_CONFIG
    cfg = dict(DEFAULTS)
    server._RUNTIME_CONFIG = cfg
    yield cfg
    server._RUNTIME_CONFIG = saved


def test_deliver_center_nongit_returns_400(mock_cfg, monkeypatch):
    monkeypatch.setattr(server, "_collect_git_changes",
                         lambda repo: (False, "不是 git 仓库", "", []))
    h = _StubHandler({"deliver": False})
    h._deliver_center()
    assert h._captured["status"] == 400, h._captured
    assert "error" in h._captured["obj"], h._captured


def test_deliver_center_no_py_changes_returns_ok(mock_cfg, monkeypatch):
    monkeypatch.setattr(server, "_collect_git_changes",
                         lambda repo: (True, "[git_status] 干净\n当前分支: main", "", []))
    h = _StubHandler({"deliver": False})
    h._deliver_center()
    out = h._captured["obj"]
    assert h._captured["status"] == 200, h._captured
    assert out.get("ok") is True
    assert out.get("total") == 0, out
    assert "无 .py 改动" in (out.get("message") or ""), out
    assert "reviews" in out, "交付中心须附带最近评审供趋势图"


def test_deliver_center_scan_runs_review_per_file(mock_cfg, monkeypatch):
    # 模拟 1 个 .py 改动 + review 服务返回 approve
    monkeypatch.setattr(server, "_collect_git_changes",
                         lambda repo: (True, "M a.py", "", [{"code": "M", "path": "a.py"}]))
    import os as _os

    def fake_isfile(p):
        return str(p).endswith("a.py")

    monkeypatch.setattr(_os.path, "isfile", fake_isfile)

    class _FakeTool:
        def has_tool(self, name):
            return name == "code_review"
        def call_tool(self, name, args):
            return "[code-review] VERDICT: approve\nSCORE: 95\nISSUES: (无)\nSUMMARY: ok"
    class _FakeMgr:
        def connect_all(self, cfg): pass
    fake_mgr = _FakeMgr()
    fake_mgr.servers = {"review": _FakeTool(), "shell": None}
    from lingmengwork.tools import mcp as _mcp_mod
    monkeypatch.setattr(_mcp_mod, "get_manager", lambda: fake_mgr)
    # _review_file 调 mgr.servers.get("review").call_tool
    h = _StubHandler({"deliver": False})
    h._deliver_center()
    out = h._captured["obj"]
    assert h._captured["status"] == 200, h._captured
    assert out.get("total") == 1, out
    assert out["files"][0]["verdict"] == "approve", out


def test_collect_git_changes_includes_unstaged(mock_cfg, monkeypatch):
    # 关键回归: 未 git add 的改动在 git status --short 中表现为首位空格 (" M file"),
    # 旧正则 ^(\S)(\S?)\s+ 会漏掉它们。这里验证 _collect_git_changes 能正确收纳。
    from lingmengwork.tools import mcp_git_server as _gmod
    monkeypatch.setattr(_gmod, "_git_status", lambda args: " M clean.py\nM staged.py\n?? untracked.py")
    monkeypatch.setattr(_gmod, "_git_diff", lambda args: "")
    ok, _status, _diff, files = server._collect_git_changes("D:/x")
    assert ok is True, "非 git 仓库应走失败分支, 这里给了合法 status"
    paths = {f["path"] for f in files}
    assert "clean.py" in paths, "未暂存改动 (首位空格) 被漏掉! %s" % files
    assert "staged.py" in paths
    assert "untracked.py" in paths
    # code 经 strip 后应为状态字母
    assert {f["code"].strip() for f in files if f["path"] == "clean.py"} == {"M"}

import os
from pathlib import Path

from lingmengwork.agent.context import build_project_context
from lingmengwork.config import DEFAULTS
from lingmengwork.tools.registry import build_registry
from lingmengwork.agent.loop import AgentLoop
from lingmengwork.llm.client import MockClient


def _make_project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo\n\nA test project.\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.log\nnode_modules\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()  # 应被忽略
    return tmp_path


def test_context_tree_and_configs(tmp_path):
    d = _make_project(tmp_path)
    reg = build_registry(DEFAULTS, base_dir=str(d))
    ctx = build_project_context(reg.roots)
    assert "目录结构" in ctx
    assert "README.md" in ctx
    assert "# Demo" in ctx
    assert ".gitignore 规则" in ctx
    # 目录树部分应忽略 node_modules (仅 .gitignore 规则块可含该词)
    tree_part = ctx.split("## 关键配置")[0]
    assert "node_modules" not in tree_part  # 噪声目录不出现在目录树


def test_loop_injects_context(tmp_path):
    d = _make_project(tmp_path)
    reg = build_registry(DEFAULTS, base_dir=str(d))
    client = MockClient(model="t")
    loop = AgentLoop(client, reg, DEFAULTS)
    sys_msg = loop.messages[0]["content"]
    assert "项目上下文" in sys_msg
    assert "README.md" in sys_msg

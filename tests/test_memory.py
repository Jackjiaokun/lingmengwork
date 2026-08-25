"""波次E: 跨会话长期记忆测试。"""
import os
from lingmengwork.config import DEFAULTS
from lingmengwork.tools.registry import build_registry
from lingmengwork.agent.context import build_memory_context
from pathlib import Path


def test_memory_write_read_append(tmp_path):
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path))
    # 初始读: 不存在
    r0 = reg.execute("memory", {"action": "read"})
    assert "尚不存在" in r0
    # 写入
    reg.execute("memory", {"action": "write", "content": "# 项目约定\n- 用中文注释"})
    rp = tmp_path / "MEMORY.md"
    assert rp.exists()
    # 读回
    r1 = reg.execute("memory", {"action": "read"})
    assert "用中文注释" in r1
    # 追加 (不重复)
    reg.execute("memory", {"action": "append", "content": "## 踩坑\n- 端口 8318 被占"})
    r2 = reg.execute("memory", {"action": "read"})
    assert "端口 8318 被占" in r2
    # 重复 append 应跳过
    before = len(reg.execute("memory", {"action": "read"}))
    dup = reg.execute("memory", {"action": "append", "content": "## 踩坑\n- 端口 8318 被占"})
    assert "跳过" in dup


def test_build_memory_context_injects(tmp_path):
    (tmp_path / "MEMORY.md").write_text("# 记忆内容ABC", encoding="utf-8")
    roots = [tmp_path.resolve()]
    ctx = build_memory_context(roots)
    assert "记忆内容ABC" in ctx
    # 不存在时返回空
    assert build_memory_context([(tmp_path / "nope").resolve()]) == ""

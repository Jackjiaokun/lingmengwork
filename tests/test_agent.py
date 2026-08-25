import os
import tempfile

from lingmengwork.llm.client import MockClient
from lingmengwork.agent.loop import AgentLoop
from lingmengwork.tools.registry import build_registry
from lingmengwork.config import DEFAULTS


class _ScriptedClient:
    """脚本化 LLM: 按序返回预设回复, 模拟"先调工具再总结"。"""
    def __init__(self, script):
        self.script = list(script)
        self.model = "scripted"

    def chat(self, messages, *, stream=False, temperature=0.2):
        text = self.script.pop(0) if self.script else "完成。"
        if stream:
            return iter([text])
        return text

    def is_available(self):
        return True


def test_agent_runs_tool_then_summarizes(tmp_path):
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path))
    # 第一步: 写文件并调用工具; 第二步: 总结 (无工具调用)
    script = [
        '先看下目录:\n```tool\n{"name": "list_dir", "arguments": {"path": "."}}\n```',
        "目录已列出, 任务完成。",
    ]
    loop = AgentLoop(_ScriptedClient(script), reg, DEFAULTS)
    events = []
    final = loop.run("列出当前目录", on_event=lambda t, kw: events.append((t, kw)))
    assert final == "目录已列出, 任务完成。"
    types = [e[0] for e in events]
    assert "tool" in types
    assert "tool_result" in types
    assert "done" in types
    # 工具确实执行了 (list_dir 返回了当前根内容, 至少含 .workbuddy 不, 但应有条目或空)
    assert any(e[0] == "tool_result" and "list_dir" in str(e[1].get("name", "")) for e in events)


def test_agent_max_iterations_cap(tmp_path):
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path))
    # 永远返回工具调用 -> 必须被 max_iterations 截断
    loop = AgentLoop(_ScriptedClient(['```tool\n{"name":"list_dir","arguments":{}}\n```'] * 50), reg, DEFAULTS)
    events = []
    final = loop.run("loop", on_event=lambda t, kw: events.append((t, kw)))
    done = [e for e in events if e[0] == "done"]
    assert done and done[0][1].get("truncated") is True


def test_agent_recovers_from_tool_error(tmp_path):
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path))
    # 第一步调用不存在的文件 (工具报错), 第二步总结
    script = [
        '读取一个不存在的文件:\n```tool\n{"name":"read_file","arguments":{"path":"nope.txt"}}\n```',
        "文件不存在, 已处理。",
    ]
    loop = AgentLoop(_ScriptedClient(script), reg, DEFAULTS)
    final = loop.run("读 nope.txt")
    assert final == "文件不存在, 已处理。"

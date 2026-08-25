"""智能体循环与推理增强的回归测试 (批次 3 / 主题 B)。

覆盖:
- 反思循环: 周期性注入自检提示 (_REFLECT_HINT)
- 工具结果 LLM 摘要回灌: 开启时优先摘要, 无 LLM/异常回退硬截断
- 长任务断点续跑: 命中上限落盘断点 + emit resume_available; continue_run 复用状态续跑
"""
import os

from lingmengwork.agent.loop import (
    AgentLoop,
    _post_process_result,
    _REFLECT_HINT,
    _SUMMARIZE_PROMPT,
)
from lingmengwork.agent import session as _session


# --------------------------------------------------------------------------
# 测试替身
# --------------------------------------------------------------------------
class FakeClient:
    """可控假 LLM: stream=True 逐轮吐 main_script; stream=False(摘要) 返回 summary_text。"""

    def __init__(self, main_script, summary_text="【摘要】关键点"):
        self.script = list(main_script)
        self.summary_text = summary_text
        self.model = "fake"

    def chat(self, messages, *, stream=False, temperature=0.2):
        if stream:
            if self.script:
                return iter([self.script.pop(0)])
            return iter(["（完成）"])
        # 非流式 = 摘要路径
        if self._raise:
            raise RuntimeError("LLM 不可用")
        return self.summary_text

    _raise = False


class FakeRegistry:
    def __init__(self, ret=""):
        self._ret = ret
        self.tools = []

    def list_tools(self):
        return self.tools

    def execute(self, name, args):
        return self._ret

    def set_permission_mode(self, m):
        pass


def _cfg(**over):
    agent = {
        "max_iterations": 32,
        "tool_result_max_chars": 6000,
        "reflect_every": 0,
        "summarize_tool_results": False,
        "summarize_max_chars": 3000,
    }
    agent.update(over)
    return {"llm": {}, "agent": agent, "mcp": {}}


_TOOL_CALL = '我读文件：\n```tool\n{"name":"read_file","arguments":{"path":"x"}}\n```'


# --------------------------------------------------------------------------
# 1. 反思循环
# --------------------------------------------------------------------------
def test_reflect_hint_injected():
    # 每次调用路径不同 -> 不触发「循环检测」分支, 隔离验证反思注入
    calls = [
        '读1：\n```tool\n{"name":"read_file","arguments":{"path":"a"}}\n```',
        '读2：\n```tool\n{"name":"read_file","arguments":{"path":"b"}}\n```',
        '读3：\n```tool\n{"name":"read_file","arguments":{"path":"c"}}\n```',
        "（完成）",
    ]
    client = FakeClient(calls)
    reg = FakeRegistry()
    reg._ret = "ok"
    loop = AgentLoop(client, reg, _cfg(max_iterations=8, reflect_every=3))
    loop.run("开始任务")
    joined = "\n".join(m.get("content", "") for m in loop.messages)
    assert _REFLECT_HINT in joined, "应在第 3 轮注入反思提示"


# --------------------------------------------------------------------------
# 2. 工具结果 LLM 摘要回灌
# --------------------------------------------------------------------------
def test_post_process_prefers_summary():
    client = FakeClient([], summary_text="【摘要】仅关键行号与错误")
    out = _post_process_result(
        client, "X" * 5000,
        summarize=True, summarize_max=3000, hard_limit=6000,
    )
    assert "已用 LLM 摘要" in out
    assert "【摘要】仅关键行号与错误" in out


def test_post_process_fallback_to_truncate_on_llm_error():
    client = FakeClient([], summary_text="不会被用到")
    client._raise = True
    out = _post_process_result(
        client, "Y" * 8000,
        summarize=True, summarize_max=3000, hard_limit=6000,
    )
    # LLM 不可用 -> 回退硬截断(原文 8000 > 硬上限 6000), 不得出现摘要标记
    assert "工具结果已截断" in out
    assert "已用 LLM 摘要" not in out


def test_summarize_via_run_loop():
    client = FakeClient([_TOOL_CALL, "（完成）"], summary_text="【摘要】文件内容要点")
    reg = FakeRegistry()
    reg._ret = "Z" * 10000
    loop = AgentLoop(client, reg, _cfg(max_iterations=8,
                                       summarize_tool_results=True,
                                       summarize_max_chars=3000))
    loop.run("读大文件")
    joined = "\n".join(m.get("content", "") for m in loop.messages)
    assert "已用 LLM 摘要" in joined, "超长工具结果应走摘要回灌"


# --------------------------------------------------------------------------
# 3. 长任务断点续跑
# --------------------------------------------------------------------------
def test_forced_end_emits_resume_and_saves():
    client = FakeClient([_TOOL_CALL, _TOOL_CALL, _TOOL_CALL])  # 总是调工具
    reg = FakeRegistry()
    reg._ret = "ok"
    loop = AgentLoop(client, reg, _cfg(max_iterations=2))
    events = []
    final = loop.run("长任务", on_event=lambda t, k: events.append((t, k)))
    done_events = [k for (t, k) in events if t == "done"]
    assert done_events, "应有 done 事件"
    last = done_events[-1]
    assert last.get("truncated") is True
    assert last.get("resume_available") is True
    assert loop.session_id, "强制结束应已分配/保存 session_id"
    # 断点已落盘
    import json
    path = _session._sessions_dir() / f"{loop.session_id}.json"
    assert path.exists(), "会话断点应已落盘"
    obj = json.loads(path.read_text(encoding="utf-8"))
    assert any(m.get("role") == "assistant" for m in obj["messages"])


def test_continue_run_reuses_state():
    client = FakeClient([_TOOL_CALL, _TOOL_CALL, _TOOL_CALL])
    reg = FakeRegistry()
    reg._ret = "ok"
    loop = AgentLoop(client, reg, _cfg(max_iterations=2))
    loop.run("长任务")  # 强制结束
    # 续跑: 切换为「给结论」脚本
    client.script = ["最终交付结论：任务已完成。"]
    out = loop.continue_run()
    assert "最终交付结论" in out
    # 续跑时 nudge 已并入消息历史
    joined = "\n".join(m.get("content", "") for m in loop.messages)
    assert "基于已有的全部工具结果继续推进" in joined

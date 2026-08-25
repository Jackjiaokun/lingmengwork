"""批次6 (全球领先运行时) 回归测试: 自动上下文压缩 / 失败自愈归因 / 证据链标记。

验证:
- _classify_failure 纯函数: 网络/权限/超时/资源/未找到/逻辑 分类 + 成功无标签。
- _maybe_compact: 阈值=0 关闭; 超阈值 + 旧回合够多时压缩(启发式/LLM 两条路径)且保留最近轮。
- run(): 工具结果标记带 #seq 证据链; 失败结果带归因标签(如 [网络异常?…])。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingmengwork.agent.loop import AgentLoop, _classify_failure


class FakeClient:
    """最小假 LLM 客户端: stream=True 返回可迭代(逐条吐 replies); stream=False 返回 summary(或抛错走启发式)。

    注意: chat 不能在顶层 yield, 否则 stream=False 也会被当成生成器(返回生成器而非字符串)。
    故 stream=True 用 iter([text]) 返回可迭代对象, stream=False 直接返回字符串。
    """
    def __init__(self, replies=None, summary="[LLM摘要] 关键发现", raise_on_nonstream=False):
        self.replies = list(replies or [])
        self.summary = summary
        self.stream_calls = 0
        self.nonstream_calls = 0
        self.raise_on_nonstream = raise_on_nonstream

    def chat(self, messages, stream=True, temperature=0.0, **kw):
        if stream:
            self.stream_calls += 1
            text = self.replies.pop(0) if self.replies else "（完成）"
            return iter([text])
        self.nonstream_calls += 1
        if self.raise_on_nonstream:
            raise RuntimeError("no llm")
        return self.summary


class FakeRegistry:
    def __init__(self, tools=None, execute_map=None):
        self._tools = tools or []
        self._exec = execute_map or {}
        self.roots = None

    def list_tools(self):
        return self._tools

    def execute(self, name, args):
        if name in self._exec:
            return self._exec[name]
        return "[tool result content for %s]" % name


def _cfg(**over):
    base = {
        "agent": {
            "max_iterations": 32,
            "tool_result_max_chars": 6000,
            "reflect_every": 0,
            "summarize_tool_results": False,
            "summarize_max_chars": 3000,
            "tool_call_quota": 0,
            "redact_secrets": False,
            "context_compact_threshold": 0,
            "context_keep_recent": 6,
        }
    }
    base["agent"].update(over)
    return base


def _loop(cfg, client=None, reg=None):
    client = client or FakeClient()
    reg = reg or FakeRegistry()
    return AgentLoop(client, reg, cfg, auto_context=False)


# —— 失败归因 纯函数 ——
def test_classify_network():
    assert "网络" in _classify_failure("[tool error] ConnectionError: getaddrinfo failed")


def test_classify_permission():
    assert "权限" in _classify_failure("[tool error] Permission denied: EACCES")


def test_classify_logic():
    assert "逻辑" in _classify_failure("[tool error] TypeError: unsupported operand")


def test_classify_notfound():
    assert "未找到" in _classify_failure("[tool error] FileNotFoundError: no such file")


def test_classify_timeout():
    assert "超时" in _classify_failure("[tool error] ReadTimeout: timed out")


def test_classify_ok_no_tag():
    assert _classify_failure("def add(a,b):\n    return a+b") == ""


# —— 自动上下文压缩 ——
def test_compact_disabled_when_threshold_zero():
    loop = _loop(_cfg(context_compact_threshold=0))
    loop.messages = [{"role": "system", "content": "S"}] + [
        {"role": "user", "content": "x" * 99999} for _ in range(10)
    ]
    assert loop._maybe_compact() is False
    assert loop._compact_count == 0


def test_compact_heuristic_reduces_and_keeps_recent():
    # 旧回合超大, 触发启发式压缩(client 非流式抛错); 保留最近 2 轮原文
    client = FakeClient(raise_on_nonstream=True)
    loop = _loop(_cfg(context_compact_threshold=100, context_keep_recent=2), client=client)
    recent1 = {"role": "user", "content": "最近一轮 A"}
    recent2 = {"role": "assistant", "content": "最近一轮 B"}
    old = [{"role": "user", "content": "x" * 5000},
           {"role": "assistant", "content": "y" * 5000},
           {"role": "user", "content": "[tool result: grep]\n" + "z" * 5000}]
    loop.messages = [{"role": "system", "content": "SYS"}] + old + [recent1, recent2]
    before = sum(len(m["content"]) for m in loop.messages)
    ok = loop._maybe_compact()
    assert ok is True
    assert loop._compact_count == 1
    # 摘要成为 messages[1]
    assert loop.messages[1]["content"].startswith("[历史压缩摘要]")
    # 最近 2 轮原文保留在末尾
    assert loop.messages[-2] == recent1 and loop.messages[-1] == recent2
    # 总字符显著下降
    after = sum(len(m["content"]) for m in loop.messages)
    assert after < before
    # client 非流式被调用但抛错 -> 走启发式(不应有 LLM 摘要标记)
    assert client.nonstream_calls >= 1


def test_compact_llm_path_uses_summary():
    client = FakeClient(summary="[LLM摘要] 已提炼关键结论")
    loop = _loop(_cfg(context_compact_threshold=100, context_keep_recent=2), client=client)
    old = [{"role": "user", "content": "x" * 4000},
           {"role": "assistant", "content": "y" * 4000}]
    loop.messages = [{"role": "system", "content": "SYS"}] + old + [
        {"role": "user", "content": "recent"}, {"role": "assistant", "content": "recent2"}]
    assert loop._maybe_compact() is True
    assert "[LLM摘要] 已提炼关键结论" in loop.messages[1]["content"]
    assert client.nonstream_calls == 1


def test_compact_debounce_keeps_system_plus_recent():
    # 仅 system + 最近轮, 不触发压缩(防抖)
    loop = _loop(_cfg(context_compact_threshold=10, context_keep_recent=4), )
    loop.messages = [{"role": "system", "content": "SYS"}] + [
        {"role": "user", "content": "r"} for _ in range(3)]
    assert loop._maybe_compact() is False


# —— 证据链 + 失败归因 在 run() 中生效 ——
_TOOL_CALL = "```tool\nname: read_file\npath: a.txt\n```"


def test_run_provenance_seq_tag():
    client = FakeClient(replies=[_TOOL_CALL, "（完成）"])
    loop = _loop(_cfg(), client=client)
    loop.run("读文件", on_event=lambda t, kw: None)
    joined = "\n".join(m.get("content", "") for m in loop.messages)
    assert "[tool result: read_file #1]" in joined, "工具结果应带 #seq 证据链标记"


def test_run_failure_attribution_tag():
    client = FakeClient(replies=[_TOOL_CALL, "（完成）"])
    reg = FakeRegistry(execute_map={"read_file": "[tool error] ConnectionError: getaddrinfo failed"})
    loop = _loop(_cfg(), client=client, reg=reg)
    loop.run("读文件", on_event=lambda t, kw: None)
    joined = "\n".join(m.get("content", "") for m in loop.messages)
    assert "[tool result: read_file #1] [网络异常?" in joined, "失败应注入归因标签"

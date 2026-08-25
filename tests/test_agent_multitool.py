"""AgentLoop 多工具调用 + 容忍性 JSON 解析回归测试。

核心回归: 模型常在 write_file/edit_file 的 content 字段写多行代码(真实换行),
严格 json.loads 会抛 "Invalid control character" -> 工具调用被静默丢弃 -> 整条链断裂。
本测试确保 _parse_tools 对这种块做容忍性解析, 且 run() 能连续执行多个工具。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingmengwork.agent.loop import AgentLoop, _tolerant_json_loads


def test_tolerant_parse_multiline_content():
    """content 含真实换行 -> 严格解析失败, 容忍解析成功。"""
    raw = (
        '{"name": "write_file", "arguments": {"path": "a.py", '
        '"content": "def add(a, b):\n    return a + b\n\n'
        'def div(a, b):\n    if b == 0:\n        raise ValueError(\\"x\\")'
        '\n    return a / b\n"}}'
    )
    # 严格解析必失败
    import pytest
    with pytest.raises(Exception):
        json.loads(raw)
    obj = _tolerant_json_loads(raw)
    assert obj["name"] == "write_file"
    # 换行被保留为字符串内的 \n (转义后)
    assert "def add" in obj["arguments"]["content"]
    assert obj["arguments"]["content"].count("\n") >= 4


def test_parse_tools_kv_format_raw_newlines():
    """行式协议: content 含真实换行 + 未转义引号 -> 仍正确解析 (修复「多工具静默失效」)。"""
    text = (
        "我来写文件:\n\n```tool\n"
        "name: write_file\n"
        "path: calc.py\n"
        "content:\n"
        'def div(a, b):\n'
        '    if b == 0:\n'
        '        raise ValueError("division by zero")\n'
        "    return a / b\n"
        "```\n\n完成。"
    )
    calls = AgentLoop._parse_tools(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "write_file"
    assert calls[0]["arguments"]["path"] == "calc.py"
    # 真实换行与裸引号被原样保留
    assert 'raise ValueError("division by zero")' in calls[0]["arguments"]["content"]


def test_parse_tools_json_fallback_still_works():
    """旧 JSON 格式 (strict=False 容忍控制字符) 仍可回退解析。"""
    text = (
        "```tool\n"
        '{"name": "auto_test", "arguments": {"command": "pytest"}}\n'
        "```"
    )
    calls = AgentLoop._parse_tools(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "auto_test"
    assert calls[0]["arguments"]["command"] == "pytest"


class _FakeRegistry:
    """记录工具调用序列的最小 registry。"""

    def __init__(self, root):
        self.calls = []
        self.root = root

    def list_tools(self):
        return [{"name": "write_file", "description": "x", "parameters": {}}]

    def execute(self, name, args):
        self.calls.append((name, args))
        if name == "write_file":
            import os as _os
            p = _os.path.join(self.root, args["path"])
            _os.makedirs(_os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(args.get("content", ""))
            return "written " + p
        if name == "auto_test":
            return "✅ 全部通过"
        if name == "review_code":
            return "VERDICT: approve"
        return "ok"


class _FakeClient:
    """两轮回复: 第一轮发两个工具调用(含多行 content), 第二轮纯文本总结。"""

    def __init__(self):
        self.round = 0

    def chat(self, messages, stream=True):
        self.round += 1
        if self.round == 1:
            text = (
                "第一步写文件:\n\n```tool\n"
                '{"name": "write_file", "arguments": {"path": "OUT/calc.py", '
                '"content": "def add(a, b):\\n    return a + b\\n"}}\n'
                "```\n\n第二步跑测试:\n\n```tool\n"
                '{"name": "auto_test", "arguments": {"command": "pytest"}}\n'
                "```"
            )
        else:
            text = "已完成: 写入 calc.py 并测试通过。"
        chunks = [text[i:i + 8] for i in range(0, len(text), 8)] or [text]
        return iter(chunks)


def test_run_executes_multiple_tools_in_chain():
    """run() 应能连续执行多个工具 (多工具调用链)。"""
    tmp = tempfile.mkdtemp()
    try:
        reg = _FakeRegistry(tmp)
        client = _FakeClient()
        # cfg 仅需 agent.max_iterations; 用最小字典
        cfg = {"agent": {"max_iterations": 8}}
        loop = AgentLoop(client, reg, cfg, auto_context=False)
        events = []

        def on_event(t, kw):
            events.append((t, kw))

        out = loop.run("请写 calc.py 并测试", on_event=on_event)
        # 两个工具都被执行
        names = [n for n, _ in reg.calls]
        assert "write_file" in names
        assert "auto_test" in names
        # 文件真被写入 (在临时根下)
        assert os.path.exists(os.path.join(tmp, "OUT/calc.py"))
        # 链路事件含 seq 与 kind
        tool_events = [kw for t, kw in events if t == "tool"]
        assert len(tool_events) == 2
        assert tool_events[0]["seq"] == 1 and tool_events[1]["seq"] == 2
        assert tool_events[0]["kind"] in ("write", "other")
        # done 事件携带 chain 摘要
        done = [kw for t, kw in events if t == "done"]
        assert done and "chain" in done[-1]
        assert len(done[-1]["chain"]) == 2
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

"""主题 A 闭环 (批次15): 结构化结果随 tool_result 事件回写对话流。

验证 AgentLoop 在工具返回 JSON 时, 把抽取出的结构(类型/字段数/键名/样例)随
tool_result 事件下发给前端, 供聊天气泡直接渲染「结构化字段/键名」; 非 JSON 结果不携带。
"""
from lingmengwork.agent.loop import AgentLoop


def _cfg():
    return {
        "agent": {
            "max_iterations": 8, "tool_result_max_chars": 6000, "reflect_every": 0,
            "summarize_tool_results": False, "tool_call_quota": 0, "redact_secrets": False,
            "context_compact_threshold": 100000000, "context_keep_recent": 6, "security": {},
        },
        "llm": {},
    }


def test_tool_result_emit_carries_structured():
    class _FC:
        model = "test-model"

        def chat(self, messages, *, stream=False, temperature=0.2):
            if not getattr(self, "_used", False):
                self._used = True
                return iter(['调一下查询:\n```tool\n{"name":"db_query","arguments":{"q":"x"}}\n```'])
            return iter(["已拿到结构化结果, 完成。"])

    class _FR:
        permission_mode = "bypassPermissions"

        def set_permission_mode(self, mode):
            self.permission_mode = mode

        def list_tools(self):
            return []

        def execute(self, name, args):
            return '{"rows":[{"id":1,"name":"a"},{"id":2,"name":"b"}],"total":2}'

    loop = AgentLoop(_FC(), _FR(), _cfg(), auto_context=False)
    events = []
    loop.run("查询数据", on_event=lambda t, kw: events.append((t, kw)))
    tr = [e for e in events if e[0] == "tool_result"]
    assert tr, "应有 tool_result 事件"
    kw = tr[0][1]
    assert kw.get("structured"), "tool_result 应携带 structured"
    assert kw["structured"]["is_json"] is True
    assert kw["structured"]["kind"] == "object"
    assert kw["structured"]["n"] == 2
    assert "rows" in kw["structured"]["keys"]
    assert "total" in kw["structured"]["keys"]
    # object 样例中应含字段 -> 值映射(string 化, 供前端安全渲染), 供前端渲染样例表
    assert kw["structured"].get("sample", {}).get("total") == "2"


def test_non_json_result_has_no_structured():
    class _FC:
        model = "m"

        def chat(self, messages, *, stream=False, temperature=0.2):
            if not getattr(self, "_u", False):
                self._u = True
                return iter(['```tool\n{"name":"echo","arguments":{}}\n```'])
            return iter(["ok"])

    class _FR:
        permission_mode = "bypassPermissions"

        def set_permission_mode(self, mode):
            self.permission_mode = mode

        def list_tools(self):
            return []

        def execute(self, name, args):
            return "这只是普通文本, 没有 JSON。"

    loop = AgentLoop(_FC(), _FR(), _cfg(), auto_context=False)
    events = []
    loop.run("x", on_event=lambda t, kw: events.append((t, kw)))
    tr = [e for e in events if e[0] == "tool_result"]
    assert tr, "应有 tool_result 事件"
    assert tr[0][1].get("structured") is None

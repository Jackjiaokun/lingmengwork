"""计划卡片解析单测 (批次13 主题B)。"""
from lingmengwork.agent.loop import _parse_plan_cards


def test_headings_and_sections():
    md = "# 重构计划\n## 准备\n- [ ] 读配置\n- [x] 备份\n## 执行\n1. 改 A\n2. 改 B\n> 注意点"
    c = _parse_plan_cards(md)
    assert c["title"] == "重构计划"
    assert len(c["sections"]) == 2
    assert c["sections"][0]["heading"] == "准备"
    assert c["sections"][1]["heading"] == "执行"
    # tasks 聚合: 2 checkbox + 2 numbered = 4
    assert len(c["tasks"]) == 4
    # 第一个 checklist 已勾选
    assert c["tasks"][0]["checked"] is False
    assert c["tasks"][1]["checked"] is True


def test_plain_note_only():
    md = "先做调研, 再落地。\n第二段说明。"
    c = _parse_plan_cards(md)
    assert c["title"]  # 退而取首段首句
    assert len(c["sections"]) == 1
    assert all(it["kind"] == "note" for it in c["sections"][0]["items"])


def test_no_heading_checkbox_list():
    md = "- [ ] 任务一\n- [ ] 任务二"
    c = _parse_plan_cards(md)
    assert len(c["sections"]) == 1
    assert c["sections"][0]["heading"] == ""
    assert len(c["tasks"]) == 2


def test_empty_returns_none():
    assert _parse_plan_cards("") is None
    assert _parse_plan_cards(None) is None


def test_bullets_are_notes():
    md = "## 章节\n- 这是说明\n- 这是另一说明"
    c = _parse_plan_cards(md)
    assert all(it["kind"] == "note" for it in c["sections"][0]["items"])


def test_plan_capture_in_plan_mode():
    """验证 AgentLoop 在计划模式下捕获产物并解析为卡片。"""
    from lingmengwork.agent.loop import AgentLoop

    class _FC:
        model = "test-model"
        def chat(self, messages, stream=True):
            return iter([""])

    class _FR:
        permission_mode = "plan"
        def list_tools(self):
            return []

    cfg = {
        "agent": {
            "max_iterations": 8, "tool_result_max_chars": 6000, "reflect_every": 0,
            "summarize_tool_results": False, "tool_call_quota": 0, "redact_secrets": True,
            "context_compact_threshold": 0, "context_keep_recent": 6, "security": {},
        },
        "llm": {},
    }
    loop = AgentLoop(_FC(), _FR(), cfg, auto_context=False)
    plan = "# 重构计划\n## 准备\n- [ ] 读配置\n- [x] 备份\n## 执行\n1. 改 A\n2. 改 B"
    loop._capture_plan(plan)
    assert loop.plan_artifact == plan
    cards = loop.get_plan_cards()
    assert cards["title"] == "重构计划"
    assert len(cards["tasks"]) == 4


def test_plan_not_captured_outside_plan_mode():
    from lingmengwork.agent.loop import AgentLoop

    class _FC:
        model = "m"
        def chat(self, messages, stream=True):
            return iter([""])

    class _FR:
        permission_mode = "bypassPermissions"
        def list_tools(self):
            return []

    cfg = {
        "agent": {
            "max_iterations": 8, "tool_result_max_chars": 6000, "reflect_every": 0,
            "summarize_tool_results": False, "tool_call_quota": 0, "redact_secrets": True,
            "context_compact_threshold": 0, "context_keep_recent": 6, "security": {},
        },
        "llm": {},
    }
    loop = AgentLoop(_FC(), _FR(), cfg, auto_context=False)
    loop._capture_plan("# 某计划\n- [ ] 任务")
    assert loop.plan_artifact is None
    assert loop.get_plan_cards() is None

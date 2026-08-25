"""成本/计划端点集成单测 (批次13) —— 直接驱动 server 处理函数, 不依赖外部 LLM。"""
import types

from lingmengwork.web import server as S


class _FakeRegistry:
    def __init__(self, mode):
        self.permission_mode = mode


class _FakeLoop:
    def __init__(self, sid, provider, model, tokens_in, tokens_out, cost, mode="bypassPermissions",
                 plan=None, cards=None, n_msgs=5):
        self.session_id = sid
        self.provider = provider
        self.model = model
        self.registry = _FakeRegistry(mode)
        self.messages = [{}] * n_msgs
        self.plan_artifact = plan
        self._cards = cards
        self._tok = {
            "model": model,
            "est_input_tokens": tokens_in,
            "est_output_tokens": tokens_out,
            "est_total_tokens": tokens_in + tokens_out,
            "est_cost_cny": cost,
        }

    def token_stats(self):
        return dict(self._tok)

    def get_plan_cards(self):
        return self._cards


def _inject(loops):
    saved = S._SESSION_LOOPS.copy()
    S._SESSION_LOOPS.clear()
    S._SESSION_LOOPS.update(loops)
    return saved


def test_cost_stats_aggregates():
    saved = _inject({
        "s1": _FakeLoop("s1", "sensenova", "sensenova-6.8-flash-lite", 1000, 500, 0.0002, "bypassPermissions"),
        "s2": _FakeLoop("s2", "sensenova", "sensenova-6.8-flash-lite", 3000, 1000, 0.0005, "plan"),
    })
    try:
        d = S.Handler._cost_stats(None)
        assert len(d["sessions"]) == 2
        assert d["total"]["est_total_tokens"] == 5500
        assert abs(d["total"]["est_cost_cny"] - 0.0007) < 1e-12
        # 计划模式标记
        plan_sess = [s for s in d["sessions"] if s["session_id"] == "s2"][0]
        assert plan_sess["plan_mode"] is True
        assert any(s["session_id"] == "s1" and not s["plan_mode"] for s in d["sessions"])
        # 价目参考非空
        assert len(d["pricing"]) >= 1
        assert d["currency"] == "CNY"
    finally:
        S._SESSION_LOOPS.clear(); S._SESSION_LOOPS.update(saved)


def test_planboard_found():
    cards = {"title": "计划A", "sections": [{"heading": "X", "items": []}], "tasks": [], "raw": "# 计划A"}
    saved = _inject({"p1": _FakeLoop("p1", "sensenova", "m", 10, 10, 0.0, "plan", plan="raw plan", cards=cards)})
    try:
        d = S.Handler._planboard(None, "p1")
        assert d["found"] is True
        assert d["plan"] == "raw plan"
        assert d["cards"]["title"] == "计划A"
    finally:
        S._SESSION_LOOPS.clear(); S._SESSION_LOOPS.update(saved)


def test_planboard_not_found():
    saved = _inject({})
    try:
        d = S.Handler._planboard(None, "nope")
        assert d["found"] is False
        assert d["cards"] is None
    finally:
        S._SESSION_LOOPS.clear(); S._SESSION_LOOPS.update(saved)

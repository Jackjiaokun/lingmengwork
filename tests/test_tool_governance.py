"""工具调用治理的回归测试 (批次 4 / 主题 A + D)。

覆盖:
- 工具调用配额: 单任务累计达上限即停止执行工具, 落盘续跑点并 emit quota_exceeded
- 工具结果缓存层: 只读搜索类同查询命中内存缓存, 底层只跑一次
- 工具结果脱敏: 回灌前自动遮蔽密钥/密码/令牌 (纯函数 + loop 管线集成)
"""
from lingmengwork.agent.loop import AgentLoop, _redact, _QUOTA_HINT
from lingmengwork.agent import session as _session
from lingmengwork.tools import registry as _reg_mod
from lingmengwork.tools.registry import Registry, _IMPLS, _RESULT_CACHE


# --------------------------------------------------------------------------
# 测试替身 (复用批次3 约定)
# --------------------------------------------------------------------------
class FakeClient:
    def __init__(self, main_script, summary_text="【摘要】关键点"):
        self.script = list(main_script)
        self.summary_text = summary_text
        self.model = "fake"
        self._raise = False

    def chat(self, messages, *, stream=False, temperature=0.2):
        if stream:
            if self.script:
                return iter([self.script.pop(0)])
            return iter(["（完成）"])
        if self._raise:
            raise RuntimeError("LLM 不可用")
        return self.summary_text


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
        "tool_call_quota": 0,
        "tool_cache_ttl": 0,
        "redact_secrets": True,
    }
    agent.update(over)
    return {"llm": {}, "agent": agent, "mcp": {}}


_TOOL_CALL = '读：\n```tool\n{"name":"read_file","arguments":{"path":"x"}}\n```'


# --------------------------------------------------------------------------
# 1. 工具调用配额
# --------------------------------------------------------------------------
def test_tool_call_quota_halts_and_signals_resume():
    call = '读：\n```tool\n{"name":"read_file","arguments":{"path":"x"}}\n```'
    client = FakeClient([call] * 10)  # 永远想调工具
    reg = FakeRegistry()
    reg._ret = "ok"
    captured = []

    def on_event(t, kw):
        captured.append((t, kw))

    loop = AgentLoop(client, reg, _cfg(max_iterations=50, tool_call_quota=3))
    loop.run("开始任务", on_event=on_event)

    # 配额=3 -> 恰好执行 3 次工具调用后停手
    assert loop._tool_calls == 3, f"应只调用 3 次, 实际 {loop._tool_calls}"
    dones = [kw for (t, kw) in captured if t == "done"]
    assert len(dones) == 1
    assert dones[0].get("quota_exceeded") is True, "应标记配额耗尽"
    assert dones[0].get("resume_available") is True
    # 配额提示应被注入, 引导模型收尾
    joined = "\n".join(m.get("content", "") for m in loop.messages)
    assert _QUOTA_HINT in joined


def test_tool_call_quota_unlimited_by_default():
    """默认 quota=0 不应拦截正常多轮工具调用。"""
    calls = [
        '读1：\n```tool\n{"name":"read_file","arguments":{"path":"a"}}\n```',
        '读2：\n```tool\n{"name":"read_file","arguments":{"path":"b"}}\n```',
        "（完成）",
    ]
    client = FakeClient(calls)
    reg = FakeRegistry()
    reg._ret = "ok"
    loop = AgentLoop(client, reg, _cfg(tool_call_quota=0))
    loop.run("开始任务")
    assert loop._tool_calls == 2
    assert "（完成）"  # 正常 done


# --------------------------------------------------------------------------
# 2. 工具结果缓存层
# --------------------------------------------------------------------------
def test_tool_result_cache_hit_avoid_rerun():
    calls = {"count": 0}

    def fake_glob(args, ctx):
        calls["count"] += 1
        return "file1.py\nfile2.py"

    old = _IMPLS.get("glob")
    _IMPLS["glob"] = fake_glob
    _RESULT_CACHE.clear()
    try:
        cfg = {"agent": {"security": {"deny_patterns": []}, "tool_cache_ttl": 60}}
        r = Registry(roots=["."], cfg=cfg, permission_mode="bypassPermissions")
        a = r.execute("glob", {"pattern": "**/*.py"})
        b = r.execute("glob", {"pattern": "**/*.py"})
        # 第二次同查询应命中缓存, 底层 fake_glob 只跑一次
        assert calls["count"] == 1, f"应只执行1次, 实际 {calls['count']}"
        assert "[缓存命中]" in b
        assert a == "file1.py\nfile2.py"  # 首次不带命中标记
    finally:
        _RESULT_CACHE.clear()
        if old is None:
            _IMPLS.pop("glob", None)
        else:
            _IMPLS["glob"] = old


def test_cache_disabled_by_default_no_hit():
    calls = {"count": 0}

    def fake_glob(args, ctx):
        calls["count"] += 1
        return "file.py"

    old = _IMPLS.get("glob")
    _IMPLS["glob"] = fake_glob
    _RESULT_CACHE.clear()
    try:
        cfg = {"agent": {"security": {"deny_patterns": []}, "tool_cache_ttl": 0}}
        r = Registry(roots=["."], cfg=cfg, permission_mode="bypassPermissions")
        r.execute("glob", {"pattern": "**/*.py"})
        r.execute("glob", {"pattern": "**/*.py"})
        assert calls["count"] == 2, "ttl=0 应禁用缓存, 每次都重跑"
    finally:
        _RESULT_CACHE.clear()
        if old is None:
            _IMPLS.pop("glob", None)
        else:
            _IMPLS["glob"] = old


# --------------------------------------------------------------------------
# 3. 工具结果脱敏 (纯函数)
# --------------------------------------------------------------------------
def test_redact_masks_various_secrets():
    cases = [
        ("api_key=sk-1234567890abcdef", "sk-1234567890abcdef"),
        ("password: hunter2", "hunter2"),
        ("Authorization: Bearer ghp_abcdefghijklmnopqrstuv", "ghp_abcdefghijklmnopqrstuv"),
        ("token=AIzaSy0123456789ABCDEFGHIJ", "AIzaSy0123456789ABCDEFGHIJ"),
        ("jwt eyJabc1234567890.def1234567890ghi", "eyJabc1234567890.def1234567890ghi"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIE", "-----BEGIN RSA PRIVATE KEY-----"),
    ]
    for raw, secret in cases:
        out = _redact(raw)
        assert secret not in out, f"明文 {secret} 未被遮蔽: {out}"
        assert "***REDACTED***" in out, f"未标记遮蔽: {out}"


def test_redact_leaves_benign_text():
    s = "def add(a, b):\n    return a + b\n# 普通代码无需遮蔽"
    assert _redact(s) == s


# --------------------------------------------------------------------------
# 4. 脱敏集成进 loop 管线
# --------------------------------------------------------------------------
def test_redact_in_loop_pipeline():
    secret = "连接串: password=hunter2 token=sk-ABCDEFGHIJKLMNOP"
    client = FakeClient([_TOOL_CALL, "（完成）"])
    reg = FakeRegistry()
    reg._ret = secret
    captured = []

    def on_event(t, kw):
        captured.append((t, kw))

    loop = AgentLoop(client, reg, _cfg(redact_secrets=True))
    loop.run("读配置", on_event=on_event)

    tr = [kw for (t, kw) in captured if t == "tool_result"]
    assert tr, "应有 tool_result 事件"
    out = tr[0]["output"]
    assert "hunter2" not in out
    assert "sk-ABCDEFGHIJKLMNOP" not in out
    assert "***REDACTED***" in out

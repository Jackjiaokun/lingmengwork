"""波次B: Token/成本估算统计测试。"""
from lingmengwork.config import DEFAULTS
from lingmengwork.tools.registry import build_registry
from lingmengwork.agent.loop import AgentLoop
from lingmengwork.llm.client import MockClient


def test_token_stats_accumulates():
    reg = build_registry(DEFAULTS, base_dir=".")
    client = MockClient(model="mock")
    loop = AgentLoop(client, reg, DEFAULTS)
    # MockClient 不调用工具, 直接回复文本
    loop.run("写一个简单的 hello 函数")
    stats = loop.token_stats()
    assert stats["est_input_tokens"] > 0
    assert stats["est_output_tokens"] > 0
    assert stats["est_total_tokens"] == stats["est_input_tokens"] + stats["est_output_tokens"]
    assert stats["est_cost_cny"] >= 0


def test_token_stats_zero_on_empty():
    loop = AgentLoop(MockClient(model="mock"), build_registry(DEFAULTS, base_dir="."), DEFAULTS)
    stats = loop.token_stats()
    assert stats["est_total_tokens"] == 0


def test_reset_clears_token_counters():
    reg = build_registry(DEFAULTS, base_dir=".")
    loop = AgentLoop(MockClient(model="mock"), reg, DEFAULTS)
    loop.run("写一个简单的 hello 函数")
    assert loop.token_stats()["est_total_tokens"] > 0
    loop.reset()  # /clear 应同时归零 token 估算
    assert loop.token_stats()["est_total_tokens"] == 0
    assert loop.token_stats()["est_cost_cny"] == 0.0

"""波次D: 任务编排 todo + subagent 测试。"""
from lingmengwork.config import DEFAULTS
from lingmengwork.tools.registry import build_registry
from lingmengwork.llm.client import MockClient
from lingmengwork.tools import agent_tools


def test_todo_set_and_update():
    reg = build_registry(DEFAULTS, base_dir=".")
    res = reg.execute("todo", {"action": "set", "items": [
        {"content": "读需求", "status": "completed"},
        {"content": "写代码", "status": "in_progress"},
    ]})
    assert "已建立 2 项" in res
    res2 = reg.execute("todo", {"action": "update", "index": 1, "status": "completed"})
    assert "已更新 #1" in res2
    res3 = reg.execute("todo", {"action": "get"})
    assert "completed" in res3


def test_todo_invalid_index():
    reg = build_registry(DEFAULTS, base_dir=".")
    reg.execute("todo", {"action": "set", "items": [{"content": "x"}]})
    res = reg.execute("todo", {"action": "update", "index": 9, "status": "completed"})
    assert "无效 index" in res


def test_subagent_runs_and_returns():
    reg = build_registry(DEFAULTS, base_dir=".")
    clients = {"mock": MockClient(model="mock")}
    reg.clients = clients
    res = reg.execute("subagent", {"prompt": "用一句话介绍 Python", "provider": "mock"})
    assert "[subagent 结果]" in res
    assert len(res) > 0


def _cfg_conc(n):
    cfg = dict(DEFAULTS)
    cfg["agent"] = dict(DEFAULTS["agent"])
    cfg["agent"]["concurrency"] = n
    return cfg


def test_resolve_subagent_cap_default_and_config():
    # 无 concurrency -> 回退默认 4 路
    assert agent_tools._resolve_subagent_cap(8, {}) == 4
    # 配置 concurrency=2, 任务数 5 -> min(5,2)=2
    assert agent_tools._resolve_subagent_cap(5, _cfg_conc(2)) == 2
    # 任务数少于上限 -> 取任务数
    assert agent_tools._resolve_subagent_cap(1, _cfg_conc(2)) == 1
    # 单任务至少 1
    assert agent_tools._resolve_subagent_cap(1, {}) == 1
    # concurrency 极端大值不越界
    assert agent_tools._resolve_subagent_cap(3, _cfg_conc(99)) == 3

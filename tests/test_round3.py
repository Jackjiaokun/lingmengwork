"""针对本轮 (波次H-K) 新功能的测试: 
- test_subagent_concurrent: subagent prompts 列表并发多子任务
- test_task_persist: 任务完成后结果落盘 ~/.lingmengwork/results/<id>.json/.md
- test_token_stats_persist: pool Task 快照含 est_tokens/cost
"""
import os
import json
import time
import uuid

from lingmengwork.tools.registry import build_registry
from lingmengwork.config import DEFAULTS
from lingmengwork.agent.pool import Task, _results_dir
from lingmengwork.tools.agent_tools import _tool_subagent


CFG = DEFAULTS


def _make_ctx(tmp_path):
    reg = build_registry(CFG, base_dir=str(tmp_path), permission_mode="bypassPermissions")
    return {
        "roots": reg.roots,
        "registry": reg,
        "cfg": CFG,
        "clients": {},
    }


class _FakeClient:
    model = "mock"

    def chat(self, messages, stream=True):
        # 一次返回结束 (不含工具调用)
        yield "子任务结果: ok"

    def is_available(self):
        return True


def test_subagent_concurrent(tmp_path):
    ctx = _make_ctx(tmp_path)
    # 注入假的 clients 让子代理能跑 (mock 客户端)
    ctx["clients"] = {"mock": _FakeClient()}
    ctx["registry"].clients = ctx["clients"]
    res = _tool_subagent({"prompts": ["调研A", "调研B", "调研C"]}, ctx)
    assert "[subagent 并发 3 路结果]" in res
    assert "子任务 1" in res and "子任务 2" in res and "子任务 3" in res


def test_subagent_single(tmp_path):
    ctx = _make_ctx(tmp_path)
    ctx["clients"] = {"mock": _FakeClient()}
    ctx["registry"].clients = ctx["clients"]
    res = _tool_subagent({"prompt": "单个子任务"}, ctx)
    assert "[subagent 结果]" in res


def test_task_persist(tmp_path):
    rd = _results_dir()
    task = Task(
        uuid.uuid4().hex[:8], "测试任务", "mock", _FakeClient(),
        build_registry(CFG, base_dir=str(tmp_path)), CFG, base_dir=str(tmp_path),
    )
    task.status = "done"
    task.iterations = 2
    task.tool_calls = 3
    task.est_tokens = 100
    task.est_cost_cny = 0.0001
    task.events = [("text", {"chunk": "最终回复内容"}), ("tool", {"name": "read_file", "args": {"path": "a.py"}}), ("tool_result", {"output": "file content"})]
    task.persist()
    jf = os.path.join(rd, f"{task.id}.json")
    mf = os.path.join(rd, f"{task.id}.md")
    assert os.path.exists(jf) and os.path.exists(mf)
    with open(jf, encoding="utf-8") as f:
        data = json.load(f)
    assert data["final_text"] == "最终回复内容"
    assert data["stats"]["est_tokens"] == 100
    with open(mf, encoding="utf-8") as f:
        md = f.read()
    assert "最终回复" in md


def test_pool_task_snapshot_has_stats(tmp_path):
    # 直接构造 Task 检查 snapshot 字段
    task = Task(
        uuid.uuid4().hex[:8], "x", "mock", _FakeClient(),
        build_registry(CFG, base_dir=str(tmp_path)), CFG, base_dir=str(tmp_path),
    )
    task.est_tokens = 250
    task.est_cost_cny = 0.0003
    snap = task.snapshot()
    assert snap["est_tokens"] == 250
    assert snap["est_cost_cny"] == 0.0003

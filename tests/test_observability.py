import asyncio
from lingmengwork.config import DEFAULTS
from lingmengwork.tools.registry import build_registry
from lingmengwork.agent.loop import AgentLoop
from lingmengwork.llm.client import MockClient


def test_loop_counts_iterations(tmp_path):
    reg = build_registry(DEFAULTS, base_dir=str(tmp_path))
    client = MockClient(model="t")
    # 一轮: 调用 list_dir -> 返回完成
    client.set_script([
        '查看目录:\n```tool\n{"name":"list_dir","arguments":{"path":"."}}\n```',
        "完成",
    ])
    loop = AgentLoop(client, reg, DEFAULTS)
    loop.run("列目录")
    assert loop.iteration == 2  # 第1轮调工具, 第2轮无工具结束


def test_task_records_iterations_and_tools(tmp_path):
    from lingmengwork.agent.pool import TaskPool
    cfg = DEFAULTS
    pool = TaskPool(cfg, base_dir=str(tmp_path))
    # 用一个 mock 客户端替换, 使其跑一个含工具的任务
    from lingmengwork.llm.client import MockClient
    m = MockClient(model="t")
    m.set_script([
        '读文件:\n```tool\n{"name":"read_file","arguments":{"path":"x.txt"}}\n```',
        "完成",
    ])
    # 直接构造任务跑
    import time, uuid
    from lingmengwork.agent.loop import AgentLoop
    reg = pool.clients and build_registry(cfg, base_dir=str(tmp_path))
    task = __import__("lingmengwork.agent.pool", fromlist=["Task"]).Task(
        uuid.uuid4().hex[:8], "读x", "mock", m, reg, cfg, base_dir=str(tmp_path)
    )
    pool._run(task)
    assert task.status == "done"
    assert task.tool_calls == 1
    assert task.iterations >= 1

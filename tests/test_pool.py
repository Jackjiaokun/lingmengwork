import time

from lingmengwork.config import build_clients, DEFAULTS
from lingmengwork.agent.pool import TaskPool
from lingmengwork.llm.client import MockClient


def _cfg_with_providers():
    cfg = dict(DEFAULTS)
    cfg["llm"] = dict(DEFAULTS["llm"])
    cfg["llm"]["providers"] = [
        {"name": "mockA", "type": "mock", "model": "m-a"},
        {"name": "mockB", "type": "mock", "model": "m-b"},
    ]
    return cfg


def test_build_clients_multi():
    clients = build_clients(_cfg_with_providers())
    assert set(clients.keys()) == {"mockA", "mockB"}
    assert all(isinstance(c, MockClient) for c in clients.values())


def test_build_clients_fallback_single():
    # 无 providers -> 单通道, 键为当前 backend 名(DEFAULTS 默认 sensenova)
    clients = build_clients(DEFAULTS)
    assert "sensenova" in clients
    from lingmengwork.llm.client import OpenAIClient
    assert isinstance(clients["sensenova"], OpenAIClient)


def test_pool_submits_multiple_tasks_concurrently(tmp_path):
    cfg = _cfg_with_providers()
    pool = TaskPool(cfg, base_dir=str(tmp_path), max_workers=4)
    snaps = [pool.submit(f"任务{i}") for i in range(4)]
    ids = {s["id"] for s in snaps}
    assert len(ids) == 4  # 每个任务唯一 id
    # 等待完成
    for _ in range(50):
        if all(pool.get(s["id"])["status"] in ("done", "error") for s in snaps):
            break
        time.sleep(0.1)
    final = [pool.get(s["id"]) for s in snaps]
    assert all(t["status"] == "done" for t in final)
    # 任务被分配到两个通道(轮询)
    providers_used = {t["provider"] for t in final}
    assert providers_used <= {"mockA", "mockB"}
    pool.shutdown(wait=True)


def test_pool_provider_pinning(tmp_path):
    cfg = _cfg_with_providers()
    pool = TaskPool(cfg, base_dir=str(tmp_path))
    snap = pool.submit("指定通道", provider="mockB")
    assert snap["provider"] == "mockB"
    for _ in range(50):
        if pool.get(snap["id"])["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert pool.get(snap["id"])["status"] == "done"
    pool.shutdown(wait=True)


def test_pool_list_and_providers(tmp_path):
    cfg = _cfg_with_providers()
    pool = TaskPool(cfg, base_dir=str(tmp_path))
    pool.submit("x")
    provs = pool.list_providers()
    assert {p["name"] for p in provs} == {"mockA", "mockB"}
    tasks = pool.list_tasks()
    assert len(tasks) == 1
    pool.shutdown(wait=True)


def _cfg_mock_single():
    cfg = dict(DEFAULTS)
    cfg["llm"] = dict(DEFAULTS["llm"])
    cfg["llm"]["backend"] = "mock"
    cfg["agent"] = dict(DEFAULTS["agent"])
    return cfg


def test_pool_concurrency_configurable():
    cfg = _cfg_mock_single()
    cfg["agent"]["concurrency"] = 3
    pool = TaskPool(cfg, base_dir=".")
    assert pool.max_workers == 3  # 显式 concurrency 优先
    pool.shutdown(wait=False)


def test_pool_concurrency_auto_fallback():
    cfg = _cfg_mock_single()
    cfg["agent"]["concurrency"] = 0  # 0 = 自动
    pool = TaskPool(cfg, base_dir=".")
    assert pool.max_workers == 2  # 单通道自动 = 1*2
    pool.shutdown(wait=False)


def test_pool_concurrency_explicit_max_workers_wins():
    cfg = _cfg_mock_single()
    cfg["agent"]["concurrency"] = 6
    pool = TaskPool(cfg, base_dir=".", max_workers=1)
    assert pool.max_workers == 1  # 显式 max_workers 参数最高优先
    pool.shutdown(wait=False)

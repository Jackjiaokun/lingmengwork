"""编排聚合层纯逻辑测试 (不依赖 HTTP/网络)。"""
from lingmengwork.web.orchestration import OrchestrationStore


class _FakePool:
    def __init__(self, snaps):
        self._snaps = snaps

    def get(self, tid):
        return self._snaps.get(tid)


def test_orch_create_and_get():
    s = OrchestrationStore()
    o = s.create(["a", "b"], ["t1", "t2"])
    assert o["id"] and len(o["id"]) == 8
    assert s.get(o["id"])["task_ids"] == ["t1", "t2"]
    assert len(s.list_all()) == 1


def test_orch_aggregate_counts_and_tokens():
    s = OrchestrationStore()
    o = s.create(["a", "b", "c"], ["t1", "t2", "t3"])
    pool = _FakePool({
        "t1": {"status": "done", "est_tokens": 100, "est_cost_cny": 0.01},
        "t2": {"status": "done", "est_tokens": 50, "est_cost_cny": 0.005},
        "t3": {"status": "error", "est_tokens": 10, "est_cost_cny": 0.001},
    })
    agg = s.aggregate(o["id"], pool)
    assert agg["total"] == 3
    assert agg["done"] == 2 and agg["running"] == 0 and agg["error"] == 1
    assert agg["est_tokens"] == 160
    assert abs(agg["est_cost_cny"] - 0.016) < 1e-9
    # done+error >= total -> 编排视为完成
    assert agg["status"] == "done"


def test_orch_aggregate_still_running():
    s = OrchestrationStore()
    o = s.create(["a", "b"], ["t1", "t2"])
    pool = _FakePool({"t1": {"status": "done"}, "t2": {"status": "running"}})
    agg = s.aggregate(o["id"], pool)
    assert agg["status"] == "running"
    assert agg["queued"] == 0


def test_orch_missing():
    s = OrchestrationStore()
    assert s.get("nope") is None
    assert s.aggregate("nope", _FakePool({})) is None

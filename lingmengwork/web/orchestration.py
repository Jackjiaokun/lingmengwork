"""并行编排(Orchestration)聚合层: 把一组独立并发任务聚合成一个「指挥中心」视图。

- OrchestrationStore 在进程内保存编排记录 (prompts -> task_ids)。
- aggregate() 从 TaskPool 拉取各 task 快照, 计算总进度/完成数/运行数/失败数,
  并累加估算 Token 与成本, 供 Web 端「并行编排看板」实时展示扇出/扇入结果。
- 纯逻辑、线程安全, 不依赖 HTTP/网络, 便于单测。
"""
import threading
import time
import uuid


class OrchestrationStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._items = {}  # id -> dict

    def create(self, prompts, task_ids):
        """登记一次编排扇出: prompts(原始指令列表) + task_ids(对应 TaskPool 任务 id)。

        返回编排记录 dict(含生成的无歧义 id)。
        """
        oid = uuid.uuid4().hex[:8]
        orch = {
            "id": oid,
            "created_at": time.time(),
            "prompts": list(prompts),
            "task_ids": list(task_ids),
            "status": "running",
        }
        with self._lock:
            self._items[oid] = orch
        return dict(orch)

    def get(self, oid):
        with self._lock:
            v = self._items.get(oid)
            return dict(v) if v is not None else None

    def list_all(self):
        with self._lock:
            return [dict(v) for v in self._items.values()]

    def aggregate(self, oid, pool):
        """聚合某编排的运行态: 拉取各 task 快照, 统计进度并累加 Token/成本。

        pool 需提供 .get(task_id) -> snapshot dict(含 status/est_tokens/est_cost_cny)。
        返回 None 若编排不存在。
        """
        orch = self.get(oid)
        if not orch:
            return None
        snaps = []
        for tid in orch["task_ids"]:
            try:
                s = pool.get(tid)
            except Exception:
                s = None
            if s:
                snaps.append(s)
        total = len(orch["task_ids"])
        done = sum(1 for s in snaps if s.get("status") == "done")
        error = sum(1 for s in snaps if s.get("status") == "error")
        running = sum(1 for s in snaps if s.get("status") == "running")
        queued = sum(1 for s in snaps if s.get("status") == "queued")
        est_tokens = sum(int(s.get("est_tokens") or 0) for s in snaps)
        est_cost = sum(float(s.get("est_cost_cny") or 0) for s in snaps)
        status = "done" if (total > 0 and (done + error) >= total) else "running"
        return {
            "id": oid,
            "created_at": orch["created_at"],
            "prompts": orch["prompts"],
            "task_ids": orch["task_ids"],
            "status": status,
            "total": total,
            "done": done,
            "running": running,
            "error": error,
            "queued": queued,
            "est_tokens": est_tokens,
            "est_cost_cny": round(est_cost, 6),
            "tasks": snaps,
        }

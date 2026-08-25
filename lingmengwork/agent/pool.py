"""并发任务池: 多路 LLM 同时编程 + 高并发。

TaskPool 用 ThreadPoolExecutor 管理多个独立编程任务:
- 每个任务绑定一个 LLM 通道(client), 拥有独立的 AgentLoop + 工具上下文。
- 任务状态机: queued -> running -> done | error。
- 每任务可指定 provider(通道名) 或留空自动轮询分配。
- on_event 回调把进度(文本/工具/结果)实时回灌给上层(Web SSE / CLI)。
- 任务完成后结果自动落盘 ~/.lingmengwork/results/<id>.json + .md (可被 Web/TUI/CLI 事后查看导出)。

设计为进程内单例友好, 但也可多实例并存。
"""
import itertools
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, Future

from .loop import AgentLoop
from ..config import build_clients
from ..tools.registry import build_registry


def _results_dir():
    d = os.path.join(os.path.expanduser("~"), ".lingmengwork", "results")
    os.makedirs(d, exist_ok=True)
    return d


class Task:
    def __init__(self, task_id, prompt, provider, client, registry, cfg, base_dir=None):
        self.id = task_id
        self.prompt = prompt
        self.provider = provider          # 通道名(显示用)
        self.client = client
        self.registry = registry
        self.cfg = cfg
        self.base_dir = base_dir
        self.status = "queued"            # queued | running | done | error
        self.created_at = time.time()
        self.finished_at = None
        self.error = None
        self.events = []                  # 已发生事件快照(供回放/历史)
        self._lock = threading.Lock()

    def snapshot(self):
        with self._lock:
            return {
                "id": self.id,
                "provider": self.provider,
                "model": getattr(self.client, "model", "?"),
                "status": self.status,
                "prompt": self.prompt,
                "iterations": getattr(self, "iterations", 0),
                "tool_calls": getattr(self, "tool_calls", 0),
                "est_tokens": getattr(self, "est_tokens", 0),
                "est_cost_cny": getattr(self, "est_cost_cny", 0.0),
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "error": self.error,
            }

    def persist(self):
        """把任务结果落盘 (JSON + Markdown), 供事后查看/导出。"""
        snap = self.snapshot()
        # 抽取叙事文本与工具链
        narr_parts = []
        tool_lines = []
        for t, kw in self.events:
            if t == "text":
                narr_parts.append(kw.get("chunk", ""))
            elif t == "tool":
                tool_lines.append(f"🔧 {kw.get('name')}({', '.join(f'{k}={v}' for k, v in (kw.get('args') or {}).items())})")
            elif t == "tool_result":
                tool_lines.append(f"   → {str(kw.get('output') or '')[:1500]}")
            elif t == "done" and kw.get("truncated"):
                tool_lines.append("⚠️ 已达最大迭代, 强行结束")
        final_text = "".join(narr_parts)
        est = dict(
            est_tokens=snap["est_tokens"],
            est_cost_cny=snap["est_cost_cny"],
            iterations=snap["iterations"],
            tool_calls=snap["tool_calls"],
        )
        data = {
            "id": snap["id"],
            "provider": snap["provider"],
            "model": snap["model"],
            "status": snap["status"],
            "prompt": snap["prompt"],
            "created_at": snap["created_at"],
            "finished_at": snap["finished_at"],
            "error": snap["error"],
            "final_text": final_text,
            "tool_log": tool_lines,
            "stats": est,
        }
        rd = _results_dir()
        try:
            with open(os.path.join(rd, f"{self.id}.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            md = f"# 灵梦work 任务结果 · #{self.id}\n\n"
            md += f"- 通道: {snap['provider']} ({snap['model']})\n"
            md += f"- 状态: {snap['status']}\n"
            md += f"- 创建: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(snap['created_at']))}\n"
            md += f"- 耗时: {(snap['finished_at'] or snap['created_at']) - snap['created_at']:.1f}s\n"
            md += f"- 估算 Token: {snap['est_tokens']} · 成本: ¥{snap['est_cost_cny']:.5f}\n\n"
            md += f"## 任务指令\n\n{snap['prompt']}\n\n"
            md += f"## 最终回复\n\n{final_text}\n\n"
            if tool_lines:
                md += f"## 工具调用链\n\n" + "\n".join(tool_lines) + "\n"
            with open(os.path.join(rd, f"{self.id}.md"), "w", encoding="utf-8") as f:
                f.write(md)
        except Exception:
            pass


class TaskPool:
    def __init__(self, cfg, max_workers=None, base_dir=None):
        self.cfg = cfg
        self.base_dir = base_dir
        # 多路客户端: {channel_name: client}
        self.clients = build_clients(cfg)
        self._rr = itertools.cycle(list(self.clients.keys())) if self.clients else iter([])
        # 并发上限: 显式 max_workers > 0 优先; 否则读 agent.concurrency; 0/缺省则自动(通道数*2)。
        cfg_conc = 0
        try:
            cfg_conc = int((cfg or {}).get("agent", {}).get("concurrency", 0) or 0)
        except Exception:
            cfg_conc = 0
        if max_workers and max_workers > 0:
            self.max_workers = max_workers
        elif cfg_conc and cfg_conc > 0:
            self.max_workers = cfg_conc
        else:
            self.max_workers = max(1, (len(self.clients) or 1) * 2)
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="lmwork")
        self.tasks = {}                    # id -> Task
        self._lock = threading.Lock()
        self._subscribers = {}             # id -> list[callback(type, kw)]

    # ---- 通道 ----
    def list_providers(self):
        return [
            {"name": n, "model": getattr(c, "model", "?"), "available": c.is_available()}
            for n, c in self.clients.items()
        ]

    def _pick_provider(self, preferred=None):
        if preferred and preferred in self.clients:
            return preferred, self.clients[preferred]
        # 轮询分配, 兼顾负载
        try:
            name = next(self._rr)
        except StopIteration:
            name = next(iter(self.clients.keys())) if self.clients else None
        if name is None:
            return None, None
        return name, self.clients[name]

    # ---- 订阅(用于 SSE 推送) ----
    def subscribe(self, task_id, cb):
        with self._lock:
            self._subscribers.setdefault(task_id, []).append(cb)

    def _emit(self, task, type_, kw):
        task.events.append((type_, kw))
        with self._lock:
            subs = list(self._subscribers.get(task.id, []))
        for cb in subs:
            try:
                cb(type_, kw)
            except Exception:
                pass

    # ---- 任务管理 ----
    def submit(self, prompt, provider=None, base_dir=None):
        name, client = self._pick_provider(provider)
        if client is None:
            raise RuntimeError("无可用 LLM 通道, 请检查 config.toml 的 providers/backend")
        reg = build_registry(self.cfg, base_dir=base_dir or self.base_dir)
        task_id = uuid.uuid4().hex[:8]
        task = Task(task_id, prompt, name, client, reg, self.cfg, base_dir=base_dir or self.base_dir)
        with self._lock:
            self.tasks[task_id] = task
        task.status = "running"
        self.executor.submit(self._run, task)
        return task.snapshot()

    def _run(self, task):
        try:
            loop = AgentLoop(task.client, task.registry, task.cfg)
            loop.run(task.prompt, on_event=lambda t, kw: self._emit(task, t, kw))
            task.iterations = loop.iteration
            task.tool_calls = sum(1 for t, kw in task.events if t == "tool")
            st = loop.token_stats()
            task.est_tokens = st["est_total_tokens"]
            task.est_cost_cny = st["est_cost_cny"]
            task.status = "done"
        except Exception as e:  # 单任务失败不影响其他任务
            task.status = "error"
            task.error = str(e)
            self._emit(task, "done", {"text": "", "truncated": False, "error": str(e)})
        finally:
            task.finished_at = time.time()
            task.persist()  # 结果落盘

    def get(self, task_id):
        with self._lock:
            t = self.tasks.get(task_id)
        return t.snapshot() if t else None

    def list_tasks(self):
        with self._lock:
            return [t.snapshot() for t in self.tasks.values()]

    def shutdown(self, wait=True):
        self.executor.shutdown(wait=wait)


# 进程内默认池(懒加载, 按需创建)
_default_pool = None
_default_lock = threading.Lock()


def get_default_pool(cfg=None, base_dir=None):
    global _default_pool
    with _default_lock:
        if _default_pool is None:
            _default_pool = TaskPool(cfg or {}, base_dir=base_dir)
    return _default_pool

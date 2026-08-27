"""Phase 16 — 实时活动总线 (Live Activity Bus)。

把灵梦work 各子系统（四引擎总控台 / 自动化调度 / 离线自检）的运行事件统一汇流，
提供进程内跨请求共享的事件环形缓冲 + 增量拉取接口，前端以轮询方式获得近实时活动流。

设计约束：
- 纯标准库，零三方依赖，无外部网络。
- 线程安全（RLock），可被调度守护线程与 web 请求线程并发读写。
- 全局单例 `get_bus()`：首次访问创建，进程内常驻。
- 仅作可观测/审计用途，事件丢失不影响主流程（emit 异常被吞）。
"""

import threading
import time


class EventBus:
    def __init__(self, maxlen=200):
        self._lock = threading.RLock()
        self._events = []          # 有序事件列表
        self._seq = 0              # 自增 id（单调递增，作增量游标）
        self._maxlen = int(maxlen)

    def emit(self, source, kind, msg, data=None):
        """发射一条事件。返回事件 dict 副本；异常时返回 None（不阻断主流程）。"""
        try:
            with self._lock:
                self._seq += 1
                ev = {
                    "id": self._seq,
                    "ts": int(time.time() * 1000),   # 毫秒时间戳
                    "source": source,                 # engine / automation / selfcheck / system
                    "kind": kind,                     # 动作类型（create/run/delete/toggle/run_fail...）
                    "msg": msg,
                    "data": data or {},
                }
                self._events.append(ev)
                if len(self._events) > self._maxlen:
                    self._events = self._events[-self._maxlen:]
                return dict(ev)
        except Exception:
            return None

    def recent(self, limit=50, since_id=0):
        """返回 id > since_id 的事件（按时间正序），最多 limit 条。"""
        with self._lock:
            evs = [e for e in self._events if e["id"] > since_id]
            if limit and limit > 0:
                evs = evs[-int(limit):]
            return [dict(e) for e in evs]

    def size(self):
        with self._lock:
            return len(self._events)

    def counts_by_source(self):
        with self._lock:
            c = {}
            for e in self._events:
                c[e["source"]] = c.get(e["source"], 0) + 1
            return c

    def clear(self):
        with self._lock:
            self._events = []
            self._seq = 0


_bus = None
_bus_lock = threading.Lock()


def get_bus():
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = EventBus()
    return _bus


def emit(source, kind, msg, data=None):
    """模块级便捷发射：返回事件 dict 或 None。"""
    try:
        return get_bus().emit(source, kind, msg, data)
    except Exception:
        return None


def recent(limit=50, since_id=0):
    return get_bus().recent(limit=limit, since_id=since_id)


def main():
    import json
    try:
        print(json.dumps(recent(limit=50), ensure_ascii=False, indent=2))
    except Exception as e:
        print("event_bus main error: %s" % e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Phase 16/17 — 实时活动总线 (Live Activity Bus) + 持久化审计链。

把灵梦work 各子系统（四引擎总控台 / 自动化调度 / 离线自检）的运行事件统一汇流，
提供进程内跨请求共享的事件环形缓冲 + 增量拉取接口，前端以轮询方式获得近实时活动流。

Phase 17 扩展：
- 持久化：指定 `persist_path` 时，每条事件以 JSONL 行追加落盘（全量、跨重启保留）。
- 启动回放：总线初始化时从磁盘读出历史事件填回内存环形缓冲，并接续自增 id 游标。
- 审计链：`emit(..., audit=True)` 标记关键操作；`audit_trail()` 从磁盘+内存合并回溯，
  供「操作审计」页面与 `/api/audit` 端点呈现跨重启的关键操作审计记录。

设计约束：
- 纯标准库，零三方依赖，无外部网络。
- 线程安全（RLock），可被调度守护线程与 web 请求线程并发读写。
- 全局单例 `init_bus(persist_path)` / `get_bus()`：进程内常驻。
- 仅作可观测/审计用途，事件丢失不影响主流程（所有异常被吞）。
"""

import json
import os
import threading
import time


class EventBus:
    def __init__(self, maxlen=200, persist_path=None):
        self._lock = threading.RLock()
        self._events = []          # 有序事件列表（内存环形，最近 maxlen）
        self._seq = 0              # 自增 id（单调递增，作增量游标）
        self._maxlen = int(maxlen)
        self._persist_path = persist_path
        # 启动回放：从磁盘读出历史事件填回内存环形 + 接续 seq
        if persist_path:
            self._replay()

    def _replay(self):
        """从磁盘 JSONL 回放历史事件（仅内存保留最近 maxlen，但 seq 接续磁盘最大值）。"""
        try:
            p = self._persist_path
            if not p or not os.path.exists(p):
                return
            loaded = []
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    loaded.append(ev)
            if loaded:
                self._events = loaded[-self._maxlen:]
                self._seq = max((e.get("id", 0) for e in loaded), default=0)
        except Exception:
            pass

    def _persist(self, ev):
        """把事件追加写入 JSONL 持久化文件（失败静默）。"""
        if not self._persist_path:
            return
        try:
            d = os.path.dirname(self._persist_path)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
            with open(self._persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def emit(self, source, kind, msg, data=None, audit=False):
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
                    "audit": bool(audit),             # Phase 17: 关键操作审计标记
                }
                self._events.append(ev)
                if len(self._events) > self._maxlen:
                    self._events = self._events[-self._maxlen:]
                self._persist(ev)
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

    def audit_trail(self, limit=100, since_id=0, source=None):
        """Phase 17 — 关键操作审计链回溯。

        `audit=True` 的事件为关键操作。合并内存实时事件与磁盘全量历史，
        去重后筛选审计事件，按时间倒序（最新在前）返回最近 limit 条，可跨重启回溯。
        """
        merged = []
        with self._lock:
            merged.extend(self._events)
        if self._persist_path and os.path.exists(self._persist_path):
            try:
                with open(self._persist_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        merged.append(ev)
            except Exception:
                pass
        seen, uniq = set(), []
        for e in merged:
            i = e.get("id")
            if i in seen:
                continue
            seen.add(i)
            if not e.get("audit"):
                continue
            if source and e.get("source") != source:
                continue
            if since_id and e.get("id", 0) <= since_id:
                continue
            uniq.append(dict(e))
        uniq.sort(key=lambda x: x.get("id", 0), reverse=True)
        return uniq[:max(1, int(limit))]

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


def init_bus(persist_path=None):
    """显式初始化全局总线（可带持久化路径）。应在服务启动早期调用一次。"""
    global _bus
    with _bus_lock:
        _bus = EventBus(persist_path=persist_path)
    return _bus


def get_bus(persist_path=None):
    """获取全局总线单例。首次访问创建（可选启用持久化）。"""
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = EventBus(persist_path=persist_path)
    return _bus


def emit(source, kind, msg, data=None, audit=False):
    """模块级便捷发射：返回事件 dict 或 None。"""
    try:
        return get_bus().emit(source, kind, msg, data, audit=audit)
    except Exception:
        return None


def recent(limit=50, since_id=0):
    return get_bus().recent(limit=limit, since_id=since_id)


def audit_trail(limit=100, since_id=0, source=None):
    return get_bus().audit_trail(limit=limit, since_id=since_id, source=source)


def main():
    import sys
    try:
        if "--audit" in sys.argv:
            data = audit_trail(limit=100)
        else:
            data = recent(limit=50)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        print("event_bus main error: %s" % e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

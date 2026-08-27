"""灵梦work · 自动化调度中枢 (Phase 15)。

让平台从「你触发才跑」升级为「按计划自主运行」: 用户登记定时 / 周期任务,
后台调度线程到点自动驱动 四引擎(流水线 / 自主 / 拆解 / 创作) 真执行
(规则兜底, 离线可用, 不依赖任何外部 key), 形成智能体 OS 的自主运行闭环。

工程信条对齐: 本地优先 · 纯标准库零三方依赖 · 可离线 · 可审计 · 可持久化。

模块级用法:
    from lingmengwork import automation_hub as ah
    hub = ah.get_hub(base_dir)          # 单例式加载 .lmw_automations/automations.json
    ah.start_scheduler(hub)            # 懒启动后台调度线程(daemon)
    hub.add(name="每日速览", kind="pipeline", goal="...", schedule="daily:09:00")
    hub.tick()                         # 手动推进(供测试 / 单步)
    hub.run_now(task_id)               # 立即触发一次
"""
import os
import re
import json
import time
import threading
import tempfile
import datetime


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_KINDS = ("pipeline", "autonomous", "decompose", "creation")
_MIN_INTERVAL = 30  # 最小调度间隔(秒), 防止误用高频


# --------------------------------------------------------------------------
# 时间辅助
# --------------------------------------------------------------------------
def _now():
    return datetime.datetime.now()


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def now_str():
    """当前时间字符串(供 server 层直接调用)。"""
    return _fmt(_now())


def _parse(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


# --------------------------------------------------------------------------
# 调度表达式解析 (interval / daily / cron)
# --------------------------------------------------------------------------
def parse_schedule(s):
    """解析调度表达式 -> dict ; 非法 raise ValueError。

    支持:
      interval:NN       每 NN 秒 (NN>=30)
      daily:HH:MM       每天固定时刻
      cron:分 时 日 月 周  5 字段标准 cron 子集(* , - / 与逗号列表;
                        周字段额外支持 mon..sun 别名, 且 0=周一..6=周日)
    """
    if not isinstance(s, str) or not s.strip():
        raise ValueError("调度表达式为空")
    s = s.strip()
    if s.startswith("interval:"):
        n = s[len("interval:"):].strip()
        if not n.isdigit():
            raise ValueError("interval 需为整数秒")
        n = int(n)
        if n < _MIN_INTERVAL:
            raise ValueError("interval 最小 %d 秒(防止误用高频)" % _MIN_INTERVAL)
        return {"typ": "interval", "seconds": n}
    if s.startswith("daily:"):
        hm = s[len("daily:"):].strip()
        m = re.match(r"^(\d{1,2}):(\d{2})$", hm)
        if not m:
            raise ValueError("daily 需为 HH:MM")
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            raise ValueError("daily 时间越界")
        return {"typ": "daily", "hour": hh, "minute": mm}
    if s.startswith("cron:"):
        parts = s[len("cron:"):].split()
        if len(parts) != 5:
            raise ValueError("cron 需 5 字段: 分 时 日 月 周")
        return {"typ": "cron", "fields": parts}
    raise ValueError("未知调度类型(支持 interval:/daily:/cron:)")


def _cron_field_match(val, spec, lo, hi):
    """单 cron 数值字段匹配。spec 支持 * , - / 组合。"""
    if spec == "*":
        return True
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            part = base
        if part == "*":
            if (val - lo) % step == 0:
                return True
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
            if a <= val <= b and (val - a) % step == 0:
                return True
            continue
        if int(part) == val:
            return True
    return False


def _cron_dow_allowed(spec):
    """cron 周字段 -> 允许的 Python weekday 集合(0=周一..6=周日)。"""
    allowed = set()
    for p in spec.split(","):
        p = p.strip()
        if not p:
            continue
        if p == "*":
            return set(range(7))
        if p in WEEKDAYS:
            allowed.add(WEEKDAYS.index(p))
        elif p.isdigit():
            allowed.add(int(p) % 7)
    return allowed


def _cron_match(dt, fields):
    minute, hour, dom, month, dow = fields
    if not _cron_field_match(dt.minute, minute, 0, 59):
        return False
    if not _cron_field_match(dt.hour, hour, 0, 23):
        return False
    if not _cron_field_match(dt.day, dom, 1, 31):
        return False
    if not _cron_field_match(dt.month, month, 1, 12):
        return False
    allowed = _cron_dow_allowed(dow)
    if allowed and dt.weekday() not in allowed:
        return False
    return True


def compute_next_run(task, base=None):
    """根据 task['schedule'] 与 base 计算下一次运行时间(datetime)。"""
    base = base or _now()
    spec = task.get("schedule_spec") or parse_schedule(task["schedule"])
    typ = spec["typ"]
    if typ == "interval":
        return base + datetime.timedelta(seconds=spec["seconds"])
    if typ == "daily":
        cand = base.replace(hour=spec["hour"], minute=spec["minute"], second=0, microsecond=0)
        if cand <= base:
            cand += datetime.timedelta(days=1)
        return cand
    if typ == "cron":
        cur = base.replace(second=0, microsecond=0)
        limit = 4 * 366 * 24 * 60  # ~7.6 年上限步数, 防死循环
        for _ in range(limit):
            if _cron_match(cur, spec["fields"]) and cur >= base:
                return cur
            cur += datetime.timedelta(minutes=1)
        return base + datetime.timedelta(minutes=1)
    return base + datetime.timedelta(minutes=1)


# --------------------------------------------------------------------------
# 调度中枢
# --------------------------------------------------------------------------
class AutomationHub:
    """自动化任务的中枢: 增删改查 + 持久化 + 规则兜底执行 + 调度推进。"""

    def __init__(self, base_dir):
        self.base_dir = os.path.abspath(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        self.path = os.path.join(self.base_dir, "automations.json")
        self.lock = threading.Lock()
        self.tasks = []
        self._load()

    def _load(self):
        try:
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.tasks = data.get("tasks", []) if isinstance(data, dict) else []
        except Exception:
            self.tasks = []

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"tasks": self.tasks}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _new_id(self):
        return "auto_%d_%s" % (int(time.time()), os.urandom(3).hex())

    def list_tasks(self):
        with self.lock:
            return [dict(t) for t in self.tasks]

    def get(self, task_id):
        with self.lock:
            for t in self.tasks:
                if t["id"] == task_id:
                    return dict(t)
        return None

    def add(self, name, kind, goal, schedule, context="", domain="code",
            enabled=True, payload=None):
        if kind not in _KINDS:
            raise ValueError("kind 必须是 %s 之一" % " / ".join(_KINDS))
        spec = parse_schedule(schedule)
        tid = self._new_id()
        with self.lock:
            task = {
                "id": tid,
                "name": name,
                "kind": kind,
                "goal": goal,
                "context": context,
                "domain": domain,
                "schedule": schedule,
                "schedule_spec": spec,
                "enabled": bool(enabled),
                "payload": payload or {},
                "created_at": _fmt(_now()),
                "last_run": None,
                "last_status": None,
                "run_count": 0,
                "next_run": _fmt(compute_next_run({"schedule": schedule, "schedule_spec": spec})),
                "history": [],
            }
            self.tasks.append(task)
            self._save()
        return dict(task)

    def remove(self, task_id):
        with self.lock:
            before = len(self.tasks)
            self.tasks = [t for t in self.tasks if t["id"] != task_id]
            ok = len(self.tasks) != before
            if ok:
                self._save()
        return ok

    def set_enabled(self, task_id, enabled):
        with self.lock:
            for t in self.tasks:
                if t["id"] == task_id:
                    t["enabled"] = bool(enabled)
                    if bool(enabled):
                        t["next_run"] = _fmt(compute_next_run({
                            "schedule": t["schedule"], "schedule_spec": t["schedule_spec"]}))
                    self._save()
                    return dict(t)
        return None

    def _execute(self, task, cwd):
        """规则兜底执行(llm_call=None), 返回结果摘要 dict。"""
        kind = task["kind"]
        goal = task["goal"]
        ctx = task.get("context", "")
        try:
            if kind == "decompose":
                from . import decompose_engine as de
                steps = de.decompose(goal, llm_call=None)
                return {"ok": True, "kind": kind, "summary": "拆解 %d 步" % len(steps),
                        "detail": {"count": len(steps)}}
            if kind == "autonomous":
                from . import autonomous as au
                res = au.run(goal, llm_call=None, max_iter=2, context=ctx)
                its = res.get("iterations") or []
                return {"ok": True, "kind": kind, "summary": "自主 %d 轮" % len(its),
                        "detail": {"iterations": len(its),
                                   "conclusion_len": len(res.get("conclusion") or "")}}
            if kind == "pipeline":
                from . import goal_pipeline as gp
                with tempfile.TemporaryDirectory() as md:
                    res = gp.run_pipeline(goal, context=ctx, llm_call=None,
                                          max_dispatch=2, do_render=False,
                                          do_autonomous=False, do_learn=False,
                                          memory_dir=md)
                n_stages = len(res.get("stages", []))
                delivery = (res.get("delivery") or "")[:200]
                return {"ok": True, "kind": kind, "summary": "流水线 %d 阶段" % n_stages,
                        "detail": {"stages": n_stages, "delivery_excerpt": delivery}}
            if kind == "creation":
                from . import creation_domains as cd
                res = cd.dispatch(task.get("domain", "code"), goal,
                                  llm_call=None, context=ctx)
                plan = (res.get("plan") or "")[:200]
                return {"ok": True, "kind": kind,
                        "summary": "创作域 %s 蓝图" % res.get("domain_name", ""),
                        "detail": {"domain": res.get("domain"),
                                   "plan_excerpt": plan},
                        "domain": res.get("domain")}
        except Exception as e:
            return {"ok": False, "kind": kind, "error": "%s: %s" % (type(e).__name__, e)}
        return {"ok": False, "kind": kind, "error": "未知 kind"}

    def run_now(self, task_id, cwd=None):
        cwd = cwd or os.getcwd()
        with self.lock:
            task = next((t for t in self.tasks if t["id"] == task_id), None)
            if task is None:
                return {"ok": False, "error": "任务不存在"}
            task = dict(task)  # 副本供执行, 避免持锁执行引擎
        res = self._execute(task, cwd)
        with self.lock:
            for t in self.tasks:
                if t["id"] == task_id:
                    t["last_run"] = _fmt(_now())
                    t["last_status"] = "ok" if res.get("ok") else "fail"
                    t["run_count"] = t.get("run_count", 0) + 1
                    spec = t.get("schedule_spec") or parse_schedule(t["schedule"])
                    t["next_run"] = _fmt(compute_next_run(
                        {"schedule": t["schedule"], "schedule_spec": spec}))
                    hist = t.setdefault("history", [])
                    hist.insert(0, {"at": t["last_run"], "status": t["last_status"],
                                    "summary": res.get("summary", "")})
                    if len(hist) > 12:
                        t["history"] = hist[:12]
                    self._save()
        return {"ok": True, "result": res, "task": self.get(task_id)}

    def tick(self, now=None):
        """推进调度: 触发所有 enabled 且到点的任务, 返回触发的 task_id 列表。"""
        now = now or _now()
        now_s = _fmt(now)
        triggered = []
        with self.lock:
            due = [t for t in self.tasks
                   if t.get("enabled") and t.get("next_run")
                   and t["next_run"] <= now_s]
        for t in due:
            try:
                self.run_now(t["id"])
                triggered.append(t["id"])
            except Exception:
                continue
        return triggered


# --------------------------------------------------------------------------
# 全局单例 + 后台调度线程
# --------------------------------------------------------------------------
_SCHED = None
_SCHED_LOCK = threading.Lock()


def get_hub(base_dir=None):
    base_dir = base_dir or os.path.join(os.getcwd(), ".lmw_automations")
    return AutomationHub(base_dir)


class SchedulerThread(threading.Thread):
    def __init__(self, hub):
        super().__init__(daemon=True)
        self.hub = hub
        self._stop = False

    def run(self):
        while not self._stop:
            try:
                self.hub.tick()
            except Exception:
                pass
            time.sleep(1)

    def stop(self):
        self._stop = True


def start_scheduler(hub):
    global _SCHED
    with _SCHED_LOCK:
        if _SCHED is None or not _SCHED.is_alive():
            _SCHED = SchedulerThread(hub)
            _SCHED.start()
    return _SCHED


def stop_scheduler():
    global _SCHED
    with _SCHED_LOCK:
        if _SCHED is not None:
            _SCHED.stop()
            _SCHED = None

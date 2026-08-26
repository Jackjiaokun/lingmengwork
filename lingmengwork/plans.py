"""计划书与任务清单 (Plans): 工作区级别的结构化计划管理。

用户在「📐 计划书」页维护计划文档(计划书 = Markdown)及其下属任务清单,
数据落在工作区主根的 ``.lmw_plans/`` 目录, 每篇计划一个 JSON::

    .lmw_plans/
        <id>.json  {"id","title","status","content","tasks":[...],"created_at","updated_at"}

status: todo(待办) | doing(进行中) | done(已完成)
task.status: todo | doing | done
"""

import json
import os
import time
import uuid

PLANS_DIR = ".lmw_plans"
STATUSES = ("todo", "doing", "done")


def _root(base_dir=None):
    return os.path.join(base_dir or os.getcwd(), PLANS_DIR)


def _ensure(base_dir=None):
    d = _root(base_dir)
    os.makedirs(d, exist_ok=True)
    return d


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _path(pid, base_dir=None):
    return os.path.join(_root(base_dir), "%s.json" % pid)


def list_plans(base_dir=None, status=None):
    _ensure(base_dir)
    out = []
    for fn in sorted(os.listdir(_root(base_dir))):
        if not fn.endswith(".json"):
            continue
        try:
            obj = json.loads(open(os.path.join(_root(base_dir), fn), encoding="utf-8").read())
        except Exception:
            continue
        if status and obj.get("status") != status:
            continue
        out.append(_summary(obj))
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return {"plans": out}


def _summary(obj):
    tasks = obj.get("tasks", [])
    counts = {"todo": 0, "doing": 0, "done": 0}
    for t in tasks:
        s = t.get("status", "todo")
        if s in counts:
            counts[s] += 1
    return {
        "id": obj.get("id"),
        "title": obj.get("title", ""),
        "status": obj.get("status", "todo"),
        "task_counts": counts,
        "task_total": len(tasks),
        "created_at": obj.get("created_at", ""),
        "updated_at": obj.get("updated_at", ""),
    }


def get_plan(pid, base_dir=None):
    fp = _path(pid, base_dir)
    if not os.path.isfile(fp):
        return None
    try:
        return json.loads(open(fp, encoding="utf-8").read())
    except Exception:
        return None


def save(base_dir, data):
    """upsert 一篇计划。data 含 id(可空)/title/content/tasks/status。"""
    _ensure(base_dir)
    pid = (data.get("id") or "").strip()
    existing = get_plan(pid, base_dir) if pid else None
    if existing:
        obj = existing
        obj["title"] = (data.get("title") or obj["title"]).strip() or "未命名计划"
        obj["content"] = data.get("content", obj.get("content", "")) or ""
        obj["status"] = data.get("status", obj.get("status", "todo")) or "todo"
        obj["tasks"] = data.get("tasks", obj.get("tasks", [])) or []
        obj["updated_at"] = _now()
    else:
        pid = uuid.uuid4().hex[:12]
        obj = {
            "id": pid,
            "title": (data.get("title") or "").strip() or "未命名计划",
            "status": data.get("status", "todo") or "todo",
            "content": data.get("content", "") or "",
            "tasks": data.get("tasks", []) or [],
            "created_at": _now(),
            "updated_at": _now(),
        }
    with open(_path(pid, base_dir), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return obj


def delete(pid, base_dir=None):
    fp = _path(pid, base_dir)
    if os.path.isfile(fp):
        try:
            os.remove(fp)
            return True
        except Exception:
            return False
    return False


def add_task(pid, title, note="", base_dir=None):
    obj = get_plan(pid, base_dir)
    if not obj:
        return None
    task = {
        "id": uuid.uuid4().hex[:10],
        "title": (title or "").strip(),
        "status": "todo",
        "note": note or "",
    }
    obj.setdefault("tasks", []).append(task)
    obj["updated_at"] = _now()
    with open(_path(pid, base_dir), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return task


def set_task_status(pid, tid, status, base_dir=None):
    if status not in STATUSES:
        raise ValueError("非法 status: %s" % status)
    obj = get_plan(pid, base_dir)
    if not obj:
        return None
    rec = None
    for t in obj.get("tasks", []):
        if t.get("id") == tid:
            rec = t
            break
    if rec is None:
        return None
    rec["status"] = status
    obj["updated_at"] = _now()
    with open(_path(pid, base_dir), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return rec


def remove_task(pid, tid, base_dir=None):
    obj = get_plan(pid, base_dir)
    if not obj:
        return 0
    before = len(obj.get("tasks", []))
    obj["tasks"] = [t for t in obj.get("tasks", []) if t.get("id") != tid]
    removed = before - len(obj["tasks"])
    obj["updated_at"] = _now()
    with open(_path(pid, base_dir), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return removed

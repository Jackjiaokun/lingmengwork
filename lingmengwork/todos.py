"""待办清单 (Todos): 工作区内的轻量任务清单。

用户在「✅ 待办清单」管理页维护任务, 数据落在工作区主根的 ``.lmw_todos.json``。
Agent 也能通过工具在建复杂任务前先列清单、完成后勾掉, 形成「计划→执行→闭环」。

存储结构::

    {
      "todos": [
        {"id":"...", "title":"实现登录", "status":"todo",
         "priority":"high", "due":"2026-09-01", "note":"", "created_at":"...", "updated_at":"..."}
      ]
    }

status: todo(待办) | doing(进行中) | done(已完成)
priority: low | mid | high
"""

import json
import os
import time
import uuid

DEFAULT_FILENAME = ".lmw_todos.json"

STATUSES = ("todo", "doing", "done")
PRIORITIES = ("low", "mid", "high")
_PRIORITY_RANK = {"high": 0, "mid": 1, "low": 2}


def _default_path(base_dir=None):
    return os.path.join(base_dir or os.getcwd(), DEFAULT_FILENAME)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def load(base_dir=None):
    path = _default_path(base_dir)
    if not os.path.isfile(path):
        return {"todos": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"todos": []}
    if not isinstance(data, dict) or not isinstance(data.get("todos"), list):
        return {"todos": []}
    return {"todos": data["todos"]}


def save(base_dir, data):
    path = _default_path(base_dir)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _counts(todos):
    c = {"todo": 0, "doing": 0, "done": 0}
    for t in todos:
        s = t.get("status", "todo")
        if s in c:
            c[s] += 1
    return c


def list_todos(base_dir=None, status=None):
    d = load(base_dir)
    todos = d["todos"]
    if status:
        todos = [t for t in todos if t.get("status", "todo") == status]
    todos = sorted(todos, key=lambda t: (
        _PRIORITY_RANK.get(t.get("priority", "mid"), 1),
        t.get("created_at", ""),
    ))
    return {"todos": todos, "counts": _counts(d["todos"])}


def get_todo(tid, base_dir=None):
    d = load(base_dir)
    for t in d["todos"]:
        if t.get("id") == tid:
            return t
    return None


def add(base_dir, title, priority="mid", due=None, note=""):
    """新增一条待办, 默认 status=todo。返回 record。"""
    title = (title or "").strip()
    if not title:
        raise ValueError("待办标题不能为空")
    priority = (priority or "mid").strip().lower()
    if priority not in PRIORITIES:
        priority = "mid"
    d = load(base_dir)
    rec = {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "status": "todo",
        "priority": priority,
        "due": (due or "").strip() or None,
        "note": note or "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    d["todos"].append(rec)
    save(base_dir, d)
    return rec


def set_status(tid, status, base_dir=None):
    """设置某条待办状态; 返回 record 或 None(未找到)。"""
    if status not in STATUSES:
        raise ValueError("非法 status: %s" % status)
    d = load(base_dir)
    rec = None
    for t in d["todos"]:
        if t.get("id") == tid:
            rec = t
            break
    if rec is None:
        return None
    rec["status"] = status
    rec["updated_at"] = _now()
    save(base_dir, d)
    return rec


def delete(tid, base_dir=None):
    d = load(base_dir)
    before = len(d["todos"])
    d["todos"] = [t for t in d["todos"] if t.get("id") != tid]
    removed = before - len(d["todos"])
    save(base_dir, d)
    return removed

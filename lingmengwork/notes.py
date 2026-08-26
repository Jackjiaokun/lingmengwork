"""笔记 (Notes): 工作区内的轻量 Markdown 笔记库。

用户在「📝 笔记」管理页维护一组命名笔记(Markdown), 数据落在工作区主根的
``.lmw_notes.json``。适合存项目思路、会议记录、临时草稿等, 与对话解耦、可长期留存。

存储结构::

    {
      "notes": [
        {"id":"...", "title":"架构决策", "content":"# 背景\\n...", "created_at":"...", "updated_at":"..."}
      ]
    }
"""

import json
import os
import time
import uuid

DEFAULT_FILENAME = ".lmw_notes.json"


def _default_path(base_dir=None):
    return os.path.join(base_dir or os.getcwd(), DEFAULT_FILENAME)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def load(base_dir=None):
    path = _default_path(base_dir)
    if not os.path.isfile(path):
        return {"notes": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"notes": []}
    if not isinstance(data, dict) or not isinstance(data.get("notes"), list):
        return {"notes": []}
    return {"notes": data["notes"]}


def save(base_dir, data):
    path = _default_path(base_dir)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_notes(base_dir=None):
    d = load(base_dir)
    notes = sorted(d["notes"], key=lambda n: n.get("updated_at", ""), reverse=True)
    return {"notes": notes}


def get_note(tid, base_dir=None):
    d = load(base_dir)
    for n in d["notes"]:
        if n.get("id") == tid:
            return n
    return None


def upsert(base_dir, title, content, tid=None):
    """新建或更新笔记。返回 (record, is_new)。title 必填且唯一(同名则更新)。"""
    title = (title or "").strip()
    if not title:
        raise ValueError("笔记标题不能为空")
    content = content or ""
    d = load(base_dir)
    notes = d["notes"]
    rec = None
    if tid:
        for n in notes:
            if n.get("id") == tid:
                rec = n
                break
    if rec is None:
        for n in notes:
            if n.get("title") == title:
                rec = n
                break
    is_new = rec is None
    if is_new:
        rec = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "content": content,
            "created_at": _now(),
            "updated_at": _now(),
        }
        notes.append(rec)
    else:
        rec["title"] = title
        rec["content"] = content
        rec["updated_at"] = _now()
    save(base_dir, d)
    return rec, is_new


def delete(tid, base_dir=None):
    d = load(base_dir)
    before = len(d["notes"])
    d["notes"] = [n for n in d["notes"] if n.get("id") != tid]
    removed = before - len(d["notes"])
    save(base_dir, d)
    return removed

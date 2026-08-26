"""待办工具: 让 Agent 在复杂任务前建清单、执行后勾掉, 形成任务闭环。

依赖 ctx["roots"] 解析工作区主根; 待办库落在 <主根>/.lmw_todos.json。
"""

from ..todos import list_todos, get_todo, add, set_status, delete as _del


def _root(ctx):
    roots = ctx.get("roots") or []
    if not roots:
        return None
    r = roots[0]
    return r if isinstance(r, str) else str(r)


def todo_list(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[todo_list] 无可用工作区根目录"
    status = (args.get("status") or "").strip() or None
    d = list_todos(base, status=status)
    c = d["counts"]
    if not d["todos"]:
        return "[todo_list] 清单为空。可用 todo_add 新建, 或在「✅ 待办清单」页管理。"
    lines = ["[todo_list] 待办 %d / 进行中 %d / 已完成 %d:" % (c["todo"], c["doing"], c["done"])]
    mark = {"todo": "▢", "doing": "◑", "done": "✔"}
    for t in d["todos"]:
        due = (" 截止:%s" % t["due"]) if t.get("due") else ""
        lines.append("%s [%s/%s] %s%s (id=%s)" % (
            mark.get(t.get("status", "todo"), "▢"),
            t.get("priority", "mid"), t.get("status", "todo"),
            t.get("title"), due, t.get("id")))
    return "\n".join(lines)


def todo_add(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[todo_add] 无可用工作区根目录"
    title = (args.get("title") or "").strip()
    if not title:
        return "[todo_add] 缺少 title(待办标题)"
    priority = args.get("priority") or "mid"
    due = args.get("due")
    note = args.get("note") or ""
    try:
        rec = add(base, title, priority, due, note)
    except ValueError as e:
        return "[todo_add] 失败: %s" % e
    return "[todo_add] 已新增待办: %s (优先级=%s, id=%s)" % (rec["title"], rec["priority"], rec["id"])


def todo_done(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[todo_done] 无可用工作区根目录"
    tid = args.get("id") or ""
    if not tid:
        return "[todo_done] 缺少 id"
    status = (args.get("status") or "done").strip() or "done"
    try:
        rec = set_status(tid, status, base)
    except ValueError as e:
        return "[todo_done] 失败: %s" % e
    if rec is None:
        return "[todo_done] 未找到 id=%s 的待办" % tid
    return "[todo_done] 已更新: %s -> %s" % (rec["title"], rec["status"])


def todo_delete(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[todo_delete] 无可用工作区根目录"
    tid = args.get("id") or ""
    if not tid:
        return "[todo_delete] 缺少 id"
    removed = _del(tid, base)
    return "[todo_delete] 已删除 %d 条待办 (id=%s)" % (removed, tid)

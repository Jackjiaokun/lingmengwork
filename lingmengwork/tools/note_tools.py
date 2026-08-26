"""笔记工具: 让 Agent 在任务中读写工作区 Markdown 笔记。

依赖 ctx["roots"] 解析工作区主根; 笔记库落在 <主根>/.lmw_notes.json。
"""

from ..notes import list_notes, get_note, upsert, delete as _del


def _root(ctx):
    roots = ctx.get("roots") or []
    if not roots:
        return None
    r = roots[0]
    return r if isinstance(r, str) else str(r)


def note_list(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[note_list] 无可用工作区根目录"
    d = list_notes(base)
    if not d["notes"]:
        return "[note_list] 当前没有保存的笔记。可在「📝 笔记」页新建。"
    lines = ["[note_list] 共 %d 条笔记:" % len(d["notes"])]
    for n in d["notes"]:
        lines.append("- %s (id=%s, 更新 %s, %d 字)" % (
            n.get("title"), n.get("id"), n.get("updated_at", "?"), len(n.get("content", ""))))
    return "\n".join(lines)


def note_get(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[note_get] 无可用工作区根目录"
    tid = args.get("id") or ""
    n = get_note(tid, base)
    if n is None:
        return "[note_get] 未找到 id=%s 的笔记" % tid
    return "[note_get] 标题: %s\n更新: %s\n内容:\n%s" % (
        n.get("title"), n.get("updated_at", "?"), n.get("content", ""))


def note_save(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[note_save] 无可用工作区根目录"
    title = (args.get("title") or "").strip()
    if not title:
        return "[note_save] 缺少 title(笔记标题)"
    content = args.get("content") or ""
    tid = args.get("id")
    try:
        rec, is_new = upsert(base, title, content, tid)
    except ValueError as e:
        return "[note_save] 失败: %s" % e
    verb = "新建" if is_new else "更新"
    return "[note_save] 已%s笔记: %s (id=%s, %d 字)" % (
        verb, rec["title"], rec["id"], len(rec["content"]))


def note_delete(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[note_delete] 无可用工作区根目录"
    tid = args.get("id") or ""
    if not tid:
        return "[note_delete] 缺少 id"
    removed = _del(tid, base)
    return "[note_delete] 已删除 %d 条笔记 (id=%s)" % (removed, tid)

"""代码片段工具: 让 Agent 在任务中读写可复用代码碎片。

依赖 ctx["roots"] 解析工作区主根; 片段库落在 <主根>/.lmw_snippets.json。
"""

from ..snippets import list_snippets, get_snippet, upsert, delete as _del


def _root(ctx):
    roots = ctx.get("roots") or []
    if not roots:
        return None
    r = roots[0]
    return r if isinstance(r, str) else str(r)


def snippet_list(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[snippet_list] 无可用工作区根目录"
    lang = (args.get("language") or "").strip() or None
    tag = (args.get("tag") or "").strip() or None
    d = list_snippets(base, language=lang, tag=tag)
    if not d["snippets"]:
        return "[snippet_list] 当前没有保存的代码片段。可在「📎 代码片段」页新建。"
    lines = ["[snippet_list] 共 %d 个片段:" % len(d["snippets"])]
    for s in d["snippets"]:
        tagstr = ("#" + " #".join(s.get("tags") or [])) if s.get("tags") else ""
        lines.append("- [%s] %s / %s %s (id=%s, %d 字)" % (
            s.get("language", "其他"), s.get("title"),
            (s.get("content", "")[:20].replace("\n", " ")), tagstr,
            s.get("id"), len(s.get("content", ""))))
    return "\n".join(lines)


def snippet_get(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[snippet_get] 无可用工作区根目录"
    tid = args.get("id") or ""
    s = get_snippet(tid, base)
    if s is None:
        return "[snippet_get] 未找到 id=%s 的片段" % tid
    tagstr = (" #" + " #".join(s.get("tags") or [])) if s.get("tags") else ""
    return "[snippet_get] 标题: %s\n语言: %s%s\n内容:\n%s" % (
        s.get("title"), s.get("language", "其他"), tagstr, s.get("content", ""))


def snippet_save(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[snippet_save] 无可用工作区根目录"
    title = (args.get("title") or "").strip()
    if not title:
        return "[snippet_save] 缺少 title(片段标题)"
    content = args.get("content") or ""
    language = args.get("language") or "其他"
    tags = args.get("tags")
    tid = args.get("id")
    try:
        rec, is_new = upsert(base, title, content, language, tags, tid)
    except ValueError as e:
        return "[snippet_save] 失败: %s" % e
    verb = "新建" if is_new else "更新"
    return "[snippet_save] 已%s片段: %s (语言=%s, id=%s, %d 字符)" % (
        verb, rec["title"], rec["language"], rec["id"], len(rec["content"]))


def snippet_delete(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[snippet_delete] 无可用工作区根目录"
    tid = args.get("id") or ""
    if not tid:
        return "[snippet_delete] 缺少 id"
    removed = _del(tid, base)
    return "[snippet_delete] 已删除 %d 个片段 (id=%s)" % (removed, tid)

"""提示词模板工具: 让 Agent 在任务中读写可复用提示词片段。

依赖 ctx["roots"] 解析工作区主根; 模板库落在 <主根>/.lmw_templates.json。
"""

from ..templates import list_templates, get_template, upsert, delete as _del


def _root(ctx):
    roots = ctx.get("roots") or []
    if not roots:
        return None
    import os
    r = roots[0]
    return r if isinstance(r, str) else str(r)


def template_list(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[template_list] 无可用工作区根目录"
    d = list_templates(base)
    if not d["templates"]:
        return "[template_list] 当前没有保存的模板。可在「📋 模板」页新建。"
    lines = ["[template_list] 共 %d 个模板:" % len(d["templates"])]
    for t in d["templates"]:
        lines.append("- [%s] %s / %s (id=%s, %d 字)" % (
            t.get("category", "其他"), t.get("name"), t.get("content", "")[:24].replace("\n", " "),
            t.get("id"), len(t.get("content", ""))))
    return "\n".join(lines)


def template_get(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[template_get] 无可用工作区根目录"
    tid = args.get("id") or ""
    t = get_template(tid, base)
    if t is None:
        return "[template_get] 未找到 id=%s 的模板" % tid
    return "[template_get] 名称: %s\n分类: %s\n内容:\n%s" % (
        t.get("name"), t.get("category", "其他"), t.get("content", ""))


def template_save(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[template_save] 无可用工作区根目录"
    name = (args.get("name") or "").strip()
    if not name:
        return "[template_save] 缺少 name(模板名称)"
    content = args.get("content") or ""
    category = args.get("category") or "其他"
    tid = args.get("id")
    try:
        rec, is_new = upsert(base, name, content, category, tid)
    except ValueError as e:
        return "[template_save] 失败: %s" % e
    verb = "新建" if is_new else "更新"
    return "[template_save] 已%s模板: %s (id=%s, %d 字)" % (
        verb, rec["name"], rec["id"], len(rec["content"]))


def template_delete(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[template_delete] 无可用工作区根目录"
    tid = args.get("id") or ""
    if not tid:
        return "[template_delete] 缺少 id"
    removed = _del(tid, base)
    return "[template_delete] 已删除 %d 个模板 (id=%s)" % (removed, tid)

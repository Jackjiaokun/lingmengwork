"""密钥保险箱工具: 让 Agent 在任务中读写项目级密钥。

依赖 ctx["roots"] 解析工作区主根; 保险箱落在 <主根>/.lmw_secrets.json (轻量本地加密)。

安全约定: secret_get 返回明文仅用于当前任务内部, 不应写回文件/日志/对话正文以外的地方。
绝大多数情况优先用 config 的环境变量机制; 本工具用于"项目内共享密钥给 Shell/外部调用"的场景。
"""

from ..secrets import list_secrets, get_secret, set_secret, delete_secret as _del


def _root(ctx):
    roots = ctx.get("roots") or []
    if not roots:
        return None
    r = roots[0]
    return r if isinstance(r, str) else str(r)


def secret_list(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[secret_list] 无可用工作区根目录"
    d = list_secrets(base)
    if not d["secrets"]:
        return "[secret_list] 保险箱为空。可在「🔐 密钥」页添加。"
    lines = ["[secret_list] 共 %d 条密钥:" % len(d["secrets"])]
    for s in d["secrets"]:
        note = (" — " + s["note"]) if s.get("note") else ""
        lines.append("- %s%s" % (s.get("key"), note))
    return "\n".join(lines)


def secret_get(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[secret_get] 无可用工作区根目录"
    key = (args.get("key") or "").strip()
    if not key:
        return "[secret_get] 缺少 key"
    val = get_secret(key, base)
    if val is None:
        return "[secret_get] 未找到密钥: %s" % key
    # 仅在确有必要时调用; 返回前做局部脱敏提示(实际值完整返回供任务使用)
    return "[secret_get] %s = %s" % (key, val)


def secret_set(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[secret_set] 无可用工作区根目录"
    key = (args.get("key") or "").strip()
    if not key:
        return "[secret_set] 缺少 key(密钥名称)"
    value = args.get("value") or ""
    note = args.get("note") or ""
    try:
        is_new = set_secret(key, value, note, base)
    except ValueError as e:
        return "[secret_set] 失败: %s" % e
    verb = "新增" if is_new else "更新"
    return "[secret_set] 已%s密钥: %s (%d 字符, 已本地加密落盘)" % (verb, key, len(value))


def secret_delete(args, ctx):
    base = _root(ctx)
    if base is None:
        return "[secret_delete] 无可用工作区根目录"
    key = (args.get("key") or "").strip()
    if not key:
        return "[secret_delete] 缺少 key"
    removed = _del(key, base)
    return "[secret_delete] 已删除 %d 条密钥 (key=%s)" % (removed, key)

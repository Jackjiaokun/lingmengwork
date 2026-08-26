"""备份 / 回滚工具: 让 Agent 能对工作区做持久化时间点快照与恢复。

与内存级单步 undo (tools/undo.py) 互补: undo 退回最近一次文件改动; 本组工具做跨进程、
可命名、可长期保留的工作区级快照 (类 Time Machine), 适合「大改动前先拍快照, 出事整体回滚」。
"""
from .common import ToolError
from .. import backup as _backup


def _mgr(ctx):
    roots = ctx.get("roots") or []
    if not roots:
        raise ToolError("未配置工作区根目录 (allowed_roots 为空), 无法备份/回滚")
    return _backup.BackupManager(roots)


def backup_create(args, ctx):
    """创建当前工作区的快照备份。label 可选(便于辨识)。返回备份 ID 与统计。"""
    label = (args.get("label") or "").strip()
    try:
        m = _mgr(ctx).create(label)
    except Exception as e:
        return "[tool error] 备份失败: %s" % e
    size = m["total_bytes"]
    return ("✓ 已创建备份\n- ID: %s\n- 标签: %s\n- 时间: %s\n- 文件数: %d\n- 体积: %.1f KB\n- 根目录: %s"
            % (m["id"], m["label"] or "(无)", m["created_at"], m["file_count"],
               size / 1024.0, "; ".join(m["roots"])))


def backup_list(args, ctx):
    """列出已有备份 (ID / 标签 / 时间 / 文件数 / 体积 / 根目录)。"""
    try:
        items = _mgr(ctx).list()
    except Exception as e:
        return "[tool error] 列出备份失败: %s" % e
    if not items:
        return "当前工作区没有备份 (调用 backup_create 创建首个快照)。"
    lines = ["共 %d 个备份 (最近在前):" % len(items)]
    for m in items:
        size = m.get("total_bytes", 0)
        lines.append("- [%s] %s | %s | 文件 %d | %.1f KB | 根: %s"
                      % (m["id"], m.get("label") or "(无)", m.get("created_at", ""),
                         m.get("file_count", 0), size / 1024.0, "; ".join(m.get("roots", []))))
    return "\n".join(lines)


def backup_rollback(args, ctx):
    """回滚到指定备份。id 必需(先 backup_list 取); clean=true 额外删除备份外文件(危险)。"""
    bid = (args.get("id") or "").strip()
    if not bid:
        return "[tool error] 缺少参数 id (先用 backup_list 查看可用备份 ID)"
    clean = bool(args.get("clean"))
    try:
        r = _mgr(ctx).rollback(bid, clean=clean)
    except Exception as e:
        return "[tool error] 回滚失败: %s" % e
    msg = ("✓ 已回滚到备份 %s (标签: %s, 时间: %s)\n- 恢复文件: %d"
           % (r["id"], r.get("label") or "(无)", r.get("created_at", ""), r["restored"]))
    if clean:
        msg += "\n- clean 模式: 额外删除了 %d 个「备份中不存在」的文件, 工作区已彻底还原到该时间点" % r["removed"]
    else:
        msg += ("\n- 未开启 clean: 仅覆盖/补回备份内文件, 备份后新增的文件仍保留。"
                "如需彻底还原到旧状态(删除备份后产生的文件), 加 clean=true")
    return msg


def backup_delete(args, ctx):
    """删除指定备份 (释放空间)。id 必需。"""
    bid = (args.get("id") or "").strip()
    if not bid:
        return "[tool error] 缺少参数 id"
    try:
        r = _mgr(ctx).delete(bid)
    except Exception as e:
        return "[tool error] 删除失败: %s" % e
    return "✓ 已删除备份 %s (%d 个文件已移除)" % (r["id"], len(r["removed"]))

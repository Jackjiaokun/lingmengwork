"""长期记忆工具: 跨会话项目记忆 (仿 Claude Code 的 CLAUDE.md / 记忆)。

- MEMORY.md 位于项目根(roots[0]), 记录项目约定/用户偏好/踩坑笔记。
- AgentLoop 启动时自动把 MEMORY.md 注入 system (见 context.build_project_context 同层逻辑)。
- 本模块提供 read/write/append 操作, 让 Agent 在任务中自我积累记忆。

安全: 仅允许读写 roots 内的 MEMORY.md, 不越界。
"""
from .common import ToolError, resolve_path


_MEMORY_FILENAME = "MEMORY.md"


def _memory_path(ctx):
    root = ctx["roots"][0] if ctx["roots"] else None
    if root is None:
        raise ToolError("无可用根目录, 无法使用 memory")
    return root / _MEMORY_FILENAME


def memory_read(args, ctx):
    p = _memory_path(ctx)
    if not p.exists():
        return f"(MEMORY.md 尚不存在于 {p}; 可用 memory action=write 创建)"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"[memory] 读取失败: {e}"


def memory_write(args, ctx):
    p = _memory_path(ctx)
    content = args.get("content", "")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"[memory] 已写入 MEMORY.md ({len(content)} 字符) @ {p}"


def memory_append(args, ctx):
    p = _memory_path(ctx)
    chunk = args.get("content", "")
    if not chunk:
        return "[memory] append 内容为空, 已忽略"
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    # 避免重复追加相同段落
    if chunk.strip() in existing:
        return "[memory] 该内容已存在, 跳过追加"
    sep = "" if existing.endswith("\n") or not existing else "\n\n"
    p.write_text(existing + sep + chunk + "\n", encoding="utf-8")
    return f"[memory] 已追加到 MEMORY.md @ {p}"

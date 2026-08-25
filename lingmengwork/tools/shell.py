"""命令执行工具: 带沙箱 + 危险命令拦截。"""
import subprocess

from .common import ToolError


def run_command(args, ctx):
    cmd = args.get("command", "")
    if not cmd.strip():
        raise ToolError("command 为空。")
    deny = ctx.get("deny_patterns") or []
    dangerously = ctx.get("dangerously_run_commands", False)
    if not dangerously:
        low = cmd.lower()
        for d in deny:
            if d.lower().strip() and d.lower().strip() in low:
                raise ToolError(f"危险命令已被拦截 (匹配规则: {d.strip()})。如需执行请设 dangerously_run_commands=true。")
    cwd = ctx.get("cwd")
    try:
        r = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            timeout=60,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        raise ToolError("命令执行超时 (60s)。")
    except Exception as e:
        raise ToolError(f"命令执行失败: {e}")
    # 子进程输出编码不确定(Windows 常 GBK), 用 replace 防解码崩溃
    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else (r.stdout or "")
    err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else (r.stderr or "")
    parts = []
    if out:
        parts.append(out)
    if err:
        parts.append("[stderr]\n" + err)
    parts.append(f"[exit code] {r.returncode}")
    return "\n".join(parts)

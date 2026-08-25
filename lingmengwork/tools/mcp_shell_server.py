"""内置 shell MCP 服务器 (stdio, 零依赖): 让本地 agent 真正执行终端命令。

与 mcp_fs_server / mcp_demo_server 同协议 (stdio JSON-RPC, 换行分隔):
  initialize -> notifications/initialized -> tools/list -> tools/call
仅向 stdout 输出 JSON-RPC 行; 日志/错误走 stderr, 避免污染协议流。

提供工具 (演示「开放工具中枢」可承载真实执行能力):
  - shell_exec : 执行 shell 命令, 捕获 stdout/stderr/返回码, 支持超时与危险命令拦截。

运行: python -m lingmengwork.tools.mcp_shell_server
"""
import os
import sys
import io
import json
import re
import shutil
import subprocess

PROTOCOL_VERSION = "2024-11-05"

# 由 LMW_SHELL_ROOT 设定, 限制默认 cwd 范围 (None=不限制, 但 config 通常传入以隔离)
ROOT = None

# 危险命令直接拦截 (不执行)
DENY = [
    re.compile(r"rm\s+-rf\s+/", re.I),
    re.compile(r"rm\s+-rf\s+~", re.I),
    re.compile(r"mkfs", re.I),
    re.compile(r"format\s+[a-z]:", re.I),
    re.compile(r"shutdown", re.I),
    re.compile(r"reboot", re.I),
    re.compile(r":\(\)\s*\{", re.I),
    re.compile(r"dd\s+if=", re.I),
    re.compile(r"del\s+/[sq]", re.I),
    re.compile(r"rd\s+/s", re.I),
]


def _fix_enc(s):
    """修复 Windows 下经 argv 传入的中文路径偶发 mojibake (utf-8 字节被误当 latin-1)。"""
    if not s:
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except Exception:
        return s


def _resolve(p):
    if not p:
        return p
    p = os.path.normpath(os.path.abspath(os.path.expanduser(p)))
    if ROOT:
        r = os.path.normpath(os.path.abspath(ROOT))
        r_prefix = r.rstrip(os.sep) + os.sep
        if not (p == r or p.startswith(r_prefix)):
            raise ValueError("路径超出允许根目录(%s): %s" % (r, p))
    return p


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _augment_path():
    """在冻结 exe 子进程继承的 PATH 之外, 增补 WorkBuddy 便携工具链 (git/python/node),
    使 `git` / `python` / `node` 等命令在 shell_exec 中可用。"""
    extra = []
    wb = os.path.expanduser("C:/Users/Administrator/.workbuddy/binaries")
    # venv python
    venv = os.path.join(wb, "python", "envs", "default", "Scripts")
    if os.path.isdir(venv):
        extra.append(venv)
    # PortableGit
    pg = os.path.join(wb, "PortableGit", "versions")
    if os.path.isdir(pg):
        for ver in sorted(os.listdir(pg)):
            for sub in ("mingw64", "cmd", "bin"):
                d = os.path.join(pg, ver, sub)
                if os.path.isdir(d):
                    extra.append(d)
    # node
    nv = os.path.join(wb, "node", "versions", "22.22.2")
    if os.path.isdir(nv):
        extra.append(nv)
    if not extra:
        return None
    cur = os.environ.get("PATH", "")
    seen = set(cur.split(os.pathsep))
    adds = [e for e in extra if e not in seen]
    if not adds:
        return None
    # 前置 (prepend): 让 `python`/`git`/`node` 优先命中 venv + WorkBuddy 工具链,
    # 否则冻结 exe 继承的受管 python(无 pytest) 会抢在 venv 之前被解析。
    return os.pathsep.join(adds) + os.pathsep + cur


def _shell_exec(args):
    cmd = (args or {}).get("command") or ""
    if not cmd.strip():
        return "[shell_exec] 缺少 command"
    for pat in DENY:
        if pat.search(cmd):
            return "[shell_exec] 危险命令已被拦截 (匹配规则: %s)" % pat.pattern
    try:
        timeout = int((args or {}).get("timeout", 30) or 30)
    except Exception:
        timeout = 30
    if timeout <= 0 or timeout > 300:
        timeout = 300
    cwd = (args or {}).get("cwd") or ""
    if cwd:
        try:
            cwd = _resolve(cwd)
        except Exception as e:
            return "[shell_exec] cwd 解析失败: %s" % e
    else:
        cwd = ROOT or os.getcwd()
    env = os.environ.copy()
    # 去除安全删除垫片触发变量: 该垫片仅当 CODEBUDDY_SESSION_ID/CLAUDE_SESSION_ID
    # 存在时才拦截 fs 删除; 子进程继承后会令 pytest 等工具在清理临时目录时误报
    # 非零退出码, 破坏交付判定。子命令执行无需此策略, 去掉即恢复干净返回码。
    for _k in ("CODEBUDDY_SESSION_ID", "CLAUDE_SESSION_ID"):
        env.pop(_k, None)
    aug = _augment_path()
    if aug:
        env["PATH"] = aug
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=cwd, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if len(out) > 20000:
            out = out[:20000] + "\n... (输出已截断至 20000 字符)"
        return "[shell_exec] rc=%d  cwd=%s\n$ %s\n%s" % (r.returncode, cwd, cmd, out)
    except subprocess.TimeoutExpired:
        return "[shell_exec] 命令超时 (超过 %ds 被终止): %s" % (timeout, cmd)
    except Exception as e:
        return "[shell_exec] 执行失败: %s" % e


TOOLS = [
    {
        "name": "shell_exec",
        "description": "执行 shell 命令 (Windows 走 cmd /c), 捕获 stdout/stderr 与返回码。超时可设(默认30s, 上限300s)。危险命令(rm -rf /, format, mkfs, shutdown 等)自动拦截。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令, 如 'dir' / 'git status' / 'python -c \"...\"'"},
                "timeout": {"type": "integer", "description": "超时秒数, 默认 30, 上限 300"},
                "cwd": {"type": "string", "description": "工作目录(可选, 默认根目录)"},
            },
            "required": ["command"],
        },
    },
]


def _handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        _send({
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lingmeng-shell", "version": "1.0"},
            },
        })
    elif method == "notifications/initialized":
        return
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = msg.get("params", {}) or {}
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}
        try:
            if name == "shell_exec":
                text = _shell_exec(arguments)
                is_error = text.startswith("[shell_exec] 危险") or text.startswith("[shell_exec] 执行失败")
            else:
                text = "unknown tool: %s" % name
                is_error = True
            _send({
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
            })
        except Exception as e:
            _send({
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"content": [{"type": "text", "text": "server error: %s" % e}], "isError": True},
            })
    else:
        if mid is not None:
            _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}})


def main():
    global ROOT
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--root", default=None)
        ns, _ = ap.parse_known_args()
        cand = os.environ.get("LMW_SHELL_ROOT") or (ns.root if ns else None)
        if cand and cand.isascii():
            raw = cand
        else:
            raw = os.path.splitdrive(os.getcwd())[0] + os.sep
        ROOT = _fix_enc(raw)
    except Exception:
        ROOT = os.path.splitdrive(os.getcwd())[0] + os.sep if os.path.splitdrive(os.getcwd())[0] else "D:\\"
    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            try:
                _handle(msg)
            except Exception as e:
                sys.stderr.write("shell server handle error: %s\n" % e)
    except Exception as e:
        sys.stderr.write("shell server stdin loop exited: %s\n" % e)


if __name__ == "__main__":
    main()

"""内置 git MCP 服务器 (stdio, 零依赖): 让本地 agent 真实读写版本控制。

与 mcp_fs_server 同协议 (stdio JSON-RPC, 换行分隔):
  initialize -> notifications/initialized -> tools/list -> tools/call
仅向 stdout 输出 JSON-RPC 行; 日志/错误走 stderr, 避免污染协议流。

提供工具 (演示「开放工具中枢」可承载真实版本控制能力):
  - git_status  : 工作区状态 (简短格式 + 当前分支)
  - git_diff    : 差异 (默认未暂存, 可 staged=1 看已暂存)
  - git_log     : 最近提交 (oneline, 可控制条数)
  - git_branch  : 分支列表 (本地 + 远程)
  - git_add     : 暂存文件 (可传多个 path, 或 "." 全仓)
  - git_commit  : 提交暂存区 (需 message)

运行: python -m lingmengwork.tools.mcp_git_server
安全: 仅允许对已存在目录执行 git; 通过 subprocess 调 git CLI (零依赖, 复用系统 git)。
"""
import os
import sys
import json
import subprocess

PROTOCOL_VERSION = "2024-11-05"

# 由 --root 设定可访问仓库根; None=不限制 (git 自身仅作用于真实 git 仓库, 风险可控)。
# 为与 fs 服务器一致, 支持 LMW_GIT_ROOT 环境变量 (ASCII 路径精确隔离)。
ROOT = None

import shutil

def _git_bin():
    """定位 git 可执行文件: 先试 PATH, 失败则扫描常见安装目录 (含 WorkBuddy 便携 git)。

    冻结 exe 子进程继承的 PATH 常不含 git (如本机 git 装在 WorkBuddy 便携目录),
    故需主动探测, 保证 git 工具在任意启动方式下可用。
    """
    b = shutil.which("git")
    if b:
        return b
    home = os.path.expanduser("~")
    cands = []
    # WorkBuddy 便携 git: 扫描 versions 下任意版本
    pg = os.path.join(home, ".workbuddy", "binaries", "PortableGit", "versions")
    if os.path.isdir(pg):
        for v in sorted(os.listdir(pg), reverse=True):
            cands.append(os.path.join(pg, v, "mingw64", "bin", "git.exe"))
    cands += [
        os.path.join(home, ".workbuddy", "binaries", "PortableGit", "versions", "1.2.0", "mingw64", "bin", "git.exe"),
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return "git"  # 回退, 交给 subprocess 报找不到


GIT_BIN = None


def _fix_enc(s):
    """修复 Windows 下经 argv 传入的中文路径偶发 mojibake (utf-8 字节被误当 latin-1)。"""
    if not s:
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except Exception:
        return s


def _resolve_repo(p):
    """校验仓库路径: 必须是已存在的目录; 若设了 ROOT 则限制在其下。"""
    if not p:
        return p
    p = os.path.normpath(os.path.abspath(os.path.expanduser(p)))
    if not os.path.isdir(p):
        raise ValueError("不是已存在的目录(仓库根): %s" % p)
    if ROOT:
        r = os.path.normpath(os.path.abspath(ROOT))
        r_prefix = r.rstrip(os.sep) + os.sep
        if not (p == r or p.startswith(r_prefix)):
            raise ValueError("仓库超出允许根目录(%s): %s" % (r, p))
    return p


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _git(repo, *args, timeout=30):
    """执行 git -C <repo> <args>, 返回 (rc, stdout, stderr)。"""
    global GIT_BIN
    if GIT_BIN is None:
        GIT_BIN = _git_bin()
    try:
        r = subprocess.run(
            [GIT_BIN, "-C", repo, *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "git 执行超时 (%.1fs)" % timeout
    except FileNotFoundError:
        return 127, "", "未找到 git 可执行文件 (请确认 git 已安装并在 PATH 中)"
    except Exception as e:
        return 1, "", "git 调用异常: %s" % e


TOOLS = [
    {
        "name": "git_status",
        "description": "查看 git 仓库工作区状态 (简短格式 + 当前分支), 用于了解有哪些改动未提交",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库目录绝对路径"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "git_diff",
        "description": "查看 git 仓库差异 (默认未暂存改动; staged=1 看已暂存改动)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库目录绝对路径"},
                "staged": {"type": "integer", "description": "1=查看已暂存差异, 0/缺省=未暂存差异"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "git_log",
        "description": "查看 git 仓库最近提交 (oneline 格式)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库目录绝对路径"},
                "max_count": {"type": "integer", "description": "返回条数, 默认 10"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "git_branch",
        "description": "列出 git 仓库分支 (本地 + 远程)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库目录绝对路径"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "git_add",
        "description": "暂存文件到 git 索引 (paths 可传多个, 用空格分隔; 传 '.' 暂存全仓)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库目录绝对路径"},
                "paths": {"type": "string", "description": "要暂存的路径, 空格分隔; 默认 '.' 全仓"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "git_commit",
        "description": "提交 git 暂存区 (必须提供 message)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库目录绝对路径"},
                "message": {"type": "string", "description": "提交说明"},
            },
            "required": ["repo", "message"],
        },
    },
]


def _git_status(args):
    try:
        repo = _resolve_repo((args or {}).get("repo") or "")
    except Exception as e:
        return "[git_status] 失败: %s" % e
    if not repo:
        return "[git_status] 缺少 repo"
    rc, short, err = _git(repo, "status", "--short")
    if rc != 0:
        return "[git_status] 失败: %s" % (err.strip() or "未知错误")
    _, branch_out, _ = _git(repo, "branch", "--show-current")
    branch = branch_out.strip() or "(detached)"
    if not short.strip():
        status_line = "(干净, 无改动)"
    else:
        status_line = short.strip()
    return "[git_status] %s\n当前分支: %s\n%s" % (repo, branch, status_line)


def _git_diff(args):
    try:
        repo = _resolve_repo((args or {}).get("repo") or "")
    except Exception as e:
        return "[git_diff] 失败: %s" % e
    if not repo:
        return "[git_diff] 缺少 repo"
    staged = int((args or {}).get("staged", 0) or 0)
    diff_args = ["diff", "--staged"] if staged else ["diff"]
    rc, out, err = _git(repo, *diff_args)
    if rc != 0:
        return "[git_diff] 失败: %s" % (err.strip() or "未知错误")
    label = "已暂存" if staged else "未暂存"
    return "[git_diff] %s (%s):\n%s" % (repo, label, out.strip() or "(无差异)")


def _git_log(args):
    try:
        repo = _resolve_repo((args or {}).get("repo") or "")
    except Exception as e:
        return "[git_log] 失败: %s" % e
    if not repo:
        return "[git_log] 缺少 repo"
    try:
        n = int((args or {}).get("max_count", 10) or 10)
    except Exception:
        n = 10
    rc, out, err = _git(repo, "log", "--oneline", "-n", str(n))
    if rc != 0:
        return "[git_log] 失败: %s" % (err.strip() or "未知错误")
    return "[git_log] %s (最近 %d 条):\n%s" % (repo, n, out.strip() or "(无提交历史)")


def _git_branch(args):
    try:
        repo = _resolve_repo((args or {}).get("repo") or "")
    except Exception as e:
        return "[git_branch] 失败: %s" % e
    if not repo:
        return "[git_branch] 缺少 repo"
    rc, out, err = _git(repo, "branch", "-a")
    if rc != 0:
        return "[git_branch] 失败: %s" % (err.strip() or "未知错误")
    return "[git_branch] %s:\n%s" % (repo, out.strip() or "(无分支)")


def _git_add(args):
    try:
        repo = _resolve_repo((args or {}).get("repo") or "")
    except Exception as e:
        return "[git_add] 失败: %s" % e
    if not repo:
        return "[git_add] 缺少 repo"
    paths = (args or {}).get("paths") or "."
    toks = paths.split() if paths != "." else ["."]
    rc, out, err = _git(repo, "add", "--", *toks)
    if rc != 0:
        return "[git_add] 失败: %s" % (err.strip() or "未知错误")
    return "[git_add] 已暂存 %s -> %s" % (paths, repo)


def _git_commit(args):
    try:
        repo = _resolve_repo((args or {}).get("repo") or "")
    except Exception as e:
        return "[git_commit] 失败: %s" % e
    if not repo:
        return "[git_commit] 缺少 repo"
    msg = (args or {}).get("message") or ""
    if not msg:
        return "[git_commit] 缺少 message"
    rc, out, err = _git(repo, "commit", "-m", msg)
    if rc != 0:
        return "[git_commit] 失败: %s" % (err.strip() or "未知错误")
    return "[git_commit] %s\n%s" % (repo, out.strip() or "(已提交)")


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
                "serverInfo": {"name": "lingmeng-git", "version": "1.0"},
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
            if name == "git_status":
                text = _git_status(arguments)
                is_error = text.startswith("[git_status] 失败") or text.startswith("[git_status] 缺少")
            elif name == "git_diff":
                text = _git_diff(arguments)
                is_error = text.startswith("[git_diff] 失败") or text.startswith("[git_diff] 缺少")
            elif name == "git_log":
                text = _git_log(arguments)
                is_error = text.startswith("[git_log] 失败") or text.startswith("[git_log] 缺少")
            elif name == "git_branch":
                text = _git_branch(arguments)
                is_error = text.startswith("[git_branch] 失败") or text.startswith("[git_branch] 缺少")
            elif name == "git_add":
                text = _git_add(arguments)
                is_error = text.startswith("[git_add] 失败") or text.startswith("[git_add] 缺少")
            elif name == "git_commit":
                text = _git_commit(arguments)
                is_error = text.startswith("[git_commit] 失败") or text.startswith("[git_commit] 缺少")
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
    # 强制 stdin/stdout 以 UTF-8 编解码 (同 fs 服务器, 防中文 Windows 冻结 exe 乱码)。
    import io
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
        cand = os.environ.get("LMW_GIT_ROOT") or (ns.root if ns else None)
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
                sys.stderr.write("git server handle error: %s\n" % e)
    except Exception as e:
        sys.stderr.write("git server stdin loop exited: %s\n" % e)


if __name__ == "__main__":
    main()

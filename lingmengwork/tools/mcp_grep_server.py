"""内置 grep MCP 服务器 (stdio, 零依赖): 让本地 agent 全局搜索代码。

与 mcp_fs_server / mcp_demo_server 同协议 (stdio JSON-RPC, 换行分隔)。
提供工具:
  - code_search : 在指定路径递归按正则匹配, 返回 file:line:text 列表。

运行: python -m lingmengwork.tools.mcp_grep_server
"""
import os
import sys
import io
import json
import re

PROTOCOL_VERSION = "2024-11-05"

ROOT = None

# 跳过的目录 (与文件树浏览器一致), 避免无意义扫描
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "dist", "build", ".workbuddy", ".idea", ".vscode"}
# 跳过的二进制/大文件扩展名
SKIP_EXT = {".pyc", ".pyo", ".exe", ".dll", ".so", ".dylib", ".png", ".jpg", ".jpeg",
            ".gif", ".bmp", ".ico", ".zip", ".gz", ".tar", ".rar", ".pdf", ".bin",
            ".dat", ".ttf", ".woff", ".woff2", ".mp3", ".mp4", ".png", ".lock"}
MAX_FILE = 4 * 1024 * 1024  # 4MB 以上文件跳过


def _fix_enc(s):
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


def _code_search(args):
    pattern = (args or {}).get("pattern") or ""
    if not pattern:
        return "[code_search] 缺少 pattern"
    path = (args or {}).get("path") or ROOT or "."
    try:
        root = _resolve(path)
    except Exception as e:
        return "[code_search] 路径解析失败: %s" % e
    if not os.path.isdir(root):
        return "[code_search] 目录不存在: %s" % root
    try:
        max_results = int((args or {}).get("max_results", 50) or 50)
    except Exception:
        max_results = 50
    max_results = max(1, min(max_results, 500))
    glob = (args or {}).get("glob") or ""
    ignore_case = bool((args or {}).get("ignore_case", False))
    try:
        flags = re.IGNORECASE if ignore_case else 0
        rx = re.compile(pattern, flags)
    except Exception as e:
        return "[code_search] 正则编译失败: %s" % e

    matches = []
    files_scanned = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if len(matches) >= max_results:
                    break
                ext = os.path.splitext(fn)[1].lower()
                if ext in SKIP_EXT:
                    continue
                if glob:
                    import fnmatch
                    if not fnmatch.fnmatch(fn, glob):
                        continue
                fp = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(fp) > MAX_FILE:
                        continue
                    files_scanned += 1
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if rx.search(line):
                                matches.append({"file": fp, "line": i, "text": line.rstrip("\n")})
                                if len(matches) >= max_results:
                                    break
                except Exception:
                    continue
    except Exception as e:
        return "[code_search] 扫描异常: %s" % e
    if not matches:
        return "[code_search] 在 %s 下未找到匹配 '%s' (扫描 %d 文件)" % (root, pattern, files_scanned)
    rel_matches = []
    for m in matches:
        try:
            rel = os.path.relpath(m["file"], root)
        except Exception:
            rel = m["file"]
        rel_matches.append("%s:%d: %s" % (rel, m["line"], m["text"]))
    return "[code_search] 命中 %d 处 (共扫描 %d 文件, 上限 %d):\n%s" % (
        len(matches), files_scanned, max_results, "\n".join(rel_matches),
    )


TOOLS = [
    {
        "name": "code_search",
        "description": "在指定目录递归按正则搜索代码, 返回 file:line:匹配行 列表。可设 glob 过滤扩展名、ignore_case 忽略大小写、max_results 限制条数。用于定位符号/用法/缺陷。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式, 如 'def _handle' / 'TODO' / 'class .*Server'"},
                "path": {"type": "string", "description": "搜索根目录(可选, 默认根目录)"},
                "glob": {"type": "string", "description": "文件名通配, 如 '*.py' / '*.js'"},
                "ignore_case": {"type": "boolean", "description": "是否忽略大小写, 默认 false"},
                "max_results": {"type": "integer", "description": "最大命中数, 默认 50, 上限 500"},
            },
            "required": ["pattern"],
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
                "serverInfo": {"name": "lingmeng-grep", "version": "1.0"},
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
            if name == "code_search":
                text = _code_search(arguments)
                is_error = text.startswith("[code_search] 缺少") or text.startswith("[code_search] 正则") or text.startswith("[code_search] 路径") or text.startswith("[code_search] 扫描")
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
        cand = os.environ.get("LMW_GREP_ROOT") or (ns.root if ns else None)
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
                sys.stderr.write("grep server handle error: %s\n" % e)
    except Exception as e:
        sys.stderr.write("grep server stdin loop exited: %s\n" % e)


if __name__ == "__main__":
    main()

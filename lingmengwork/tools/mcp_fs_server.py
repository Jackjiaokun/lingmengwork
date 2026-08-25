"""内置 filesystem MCP 服务器 (stdio, 零依赖): 让本地 agent 真实读写/列目录。

与 mcp_demo_server 同协议 (stdio JSON-RPC, 换行分隔):
  initialize -> notifications/initialized -> tools/list -> tools/call
仅向 stdout 输出 JSON-RPC 行; 日志/错误走 stderr, 避免污染协议流。

提供工具 (演示「开放工具中枢」可承载真实文件操作能力):
  - fs_read  : 读取文本文件内容 (支持 max_lines 限制)
  - fs_write : 写入文本文件 (自动建父目录)
  - fs_list  : 列出目录条目 (D=目录 / F=文件)

运行: python -m lingmengwork.tools.mcp_fs_server
"""
import os
import sys
import json

PROTOCOL_VERSION = "2024-11-05"

# 由 --root 设定, 限制可访问路径范围 (None=不限制, 但 config 通常传入以隔离)
ROOT = None


def _fix_enc(s):
    """修复 Windows 下经 argv 传入的中文路径偶发 mojibake (utf-8 字节被误当 latin-1)。"""
    if not s:
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except Exception:
        return s


def _resolve(p):
    """把路径展开为绝对路径, 并在设置了 ROOT 时强制约束在其下 (分隔符归一)。"""
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


TOOLS = [
    {
        "name": "fs_read",
        "description": "读取文本文件内容 (支持 max_lines 限制行数, 用于把源码/配置喂给 agent)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件绝对路径"},
                "max_lines": {"type": "integer", "description": "最多返回行数, 默认 200"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "fs_write",
        "description": "写入文本文件 (父目录不存在时自动创建)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件绝对路径"},
                "content": {"type": "string", "description": "要写入的文本"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "fs_list",
        "description": "列出目录条目 (前缀 D=目录, F=文件)",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目录路径, 默认当前目录"}},
        },
    },
]


def _fs_read(args):
    path = _resolve((args or {}).get("path") or "")
    if not path or not os.path.isfile(path):
        return "[fs_read] 文件不存在: %s" % path
    try:
        max_lines = int((args or {}).get("max_lines", 200) or 200)
    except Exception:
        max_lines = 200
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return "[fs_read] 读取失败: %s" % e
    shown = lines[:max_lines]
    return "[fs_read] %s (共 %d 行, 显示前 %d 行):\n%s" % (
        path, len(lines), max_lines, "".join(shown),
    )


def _fs_write(args):
    path = _resolve((args or {}).get("path") or "")
    content = (args or {}).get("content", "") or ""
    if not path:
        return "[fs_write] 缺少 path"
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return "[fs_write] 已写入 %d 字符到 %s" % (len(content), path)
    except Exception as e:
        return "[fs_write] 写入失败: %s" % e


def _fs_list(args):
    path = _resolve((args or {}).get("path") or ".") or "."
    if not os.path.isdir(path):
        return "[fs_list] 目录不存在: %s" % path
    try:
        items = sorted(os.listdir(path))
    except Exception as e:
        return "[fs_list] 列举失败: %s" % e
    rows = []
    for it in items[:200]:
        full = os.path.join(path, it)
        rows.append(("D " if os.path.isdir(full) else "F ") + it)
    return "[fs_list] %s (共 %d 项):\n%s" % (path, len(items), "\n".join(rows))


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
                "serverInfo": {"name": "lingmeng-fs", "version": "1.0"},
            },
        })
    elif method == "notifications/initialized":
        return  # 通知, 不回应
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = msg.get("params", {}) or {}
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}
        try:
            if name == "fs_read":
                text = _fs_read(arguments)
                is_error = text.startswith("[fs_read] 文件不存在") or text.startswith("[fs_read] 读取失败")
            elif name == "fs_write":
                text = _fs_write(arguments)
                is_error = text.startswith("[fs_write] 写入失败")
            elif name == "fs_list":
                text = _fs_list(arguments)
                is_error = text.startswith("[fs_list] 目录不存在") or text.startswith("[fs_list] 列举失败")
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
    # 强制 stdin/stdout 以 UTF-8 编解码: 冻结 exe 在中文 Windows 下 sys.stdin/stdout 默认
    # locale 为 cp936(GBK), 会把父进程(mcp.py, encoding="utf-8")发来的 UTF-8 中文参数
    # 误当 GBK 解码 -> 乱码(中文路径 os.path.isfile 失败)。显式重包为 UTF-8 对齐。
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
        cand = os.environ.get("LMW_FS_ROOT") or (ns.root if ns else None)
        if cand and cand.isascii():
            # 显式 root 为纯英文路径 -> 精确隔离
            raw = cand
        else:
            # 冻结 exe 子进程下中文 cwd/argv/env 会 mojibake (Windows 编码错配), 不可靠;
            # 回退到「驱动器根」(ASCII 可靠) 作沙箱边界 —— 即允许读写整个该驱动器。
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
                sys.stderr.write("fs server handle error: %s\n" % e)
    except Exception as e:
        sys.stderr.write("fs server stdin loop exited: %s\n" % e)


if __name__ == "__main__":
    main()

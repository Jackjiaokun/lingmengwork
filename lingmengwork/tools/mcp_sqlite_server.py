"""lingmengwork 外部工具: 本地数据库 (sqlite) MCP 服务器。

零依赖 (仅标准库 sqlite3), 通过 stdio JSON-RPC 与父进程通讯,
协议与 mcp_fs_server / mcp_demo_server 一致。

工具:
  - db_query(db_path, sql, limit): 执行 SQL; SELECT/PRAGMA 返回行; 写操作返回影响行数。
  - db_list_tables(db_path): 列出库内所有 table / view。

安全: 路径受 ROOT 隔离 (env LMW_SQLITE_ROOT, 回退驱动器根)。只读/写均在允许根内。
"""

import sys
import os
import io
import json
import argparse
import traceback
import sqlite3


ROOT = None


def _fix_enc(s):
    try:
        return s.encode("latin-1").decode("utf-8")
    except Exception:
        return s


def _resolve_db(p):
    if not p:
        raise ValueError("缺少 db_path")
    p = os.path.abspath(os.path.expanduser(p))
    if ROOT:
        r = os.path.abspath(ROOT)
        rp = r.rstrip(os.sep) + os.sep
        if not (p == r or p.startswith(rp)):
            raise ValueError("路径超出允许根目录(%s): %s" % (r, p))
    return p


def _db_query(args):
    try:
        db = _resolve_db((args or {}).get("db_path") or "")
    except Exception as e:
        return "[db_query] 失败: %s" % e
    if not db:
        return "[db_query] 缺少 db_path"
    sql = (args or {}).get("sql") or ""
    if not sql.strip():
        return "[db_query] 缺少 sql"
    try:
        limit = int((args or {}).get("limit", 50) or 50)
    except Exception:
        limit = 50
    try:
        con = sqlite3.connect(db)
        con.text_factory = str
        cur = con.cursor()
        cur.execute(sql)
        up = sql.strip().lower()
        if up.startswith(("select", "pragma", "with", "explain")):
            rows = cur.fetchmany(limit)
            cols = [d[0] for d in cur.description] if cur.description else []
            out = ["列: " + ", ".join(str(c) for c in cols)]
            for row in rows:
                out.append(" | ".join("" if v is None else str(v) for v in row))
            con.close()
            return "[db_query] %s\n返回 %d 行 (上限 %d)\n%s" % (db, len(rows), limit, "\n".join(out))
        else:
            n = cur.rowcount
            con.commit()
            con.close()
            return "[db_query] %s\n已执行写操作, 影响行数=%s" % (db, n)
    except Exception as e:
        return "[db_query] 失败: %s" % e


def _db_list_tables(args):
    try:
        db = _resolve_db((args or {}).get("db_path") or "")
    except Exception as e:
        return "[db_list_tables] 失败: %s" % e
    if not db:
        return "[db_list_tables] 缺少 db_path"
    if not os.path.isfile(db):
        return "[db_list_tables] 文件不存在: %s" % db
    try:
        con = sqlite3.connect(db)
        cur = con.cursor()
        cur.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")
        rows = cur.fetchall()
        con.close()
        if not rows:
            return "[db_list_tables] %s\n(无表/视图)" % db
        return "[db_list_tables] %s\n" % db + "\n".join("%s [%s]" % (n, t) for n, t in rows)
    except Exception as e:
        return "[db_list_tables] 失败: %s" % e


TOOLS = [
    {
        "name": "db_query",
        "description": "在 sqlite 数据库执行 SQL。SELECT/PRAGMA 返回行(上限 limit); 写操作(INSERT/UPDATE/DELETE/CREATE 等)返回影响行数。db_path 为 .db/.sqlite 文件绝对路径。",
        "parameters": {
            "db_path": "数据库文件绝对路径 (*.db / *.sqlite)",
            "sql": "要执行的 SQL 语句",
            "limit": "SELECT 返回行数上限, 默认 50",
        },
    },
    {
        "name": "db_list_tables",
        "description": "列出 sqlite 数据库内所有 table / view 及其类型。",
        "parameters": {
            "db_path": "数据库文件绝对路径 (*.db / *.sqlite)",
        },
    },
]


def main():
    global ROOT
    # 子进程默认 locale 可能为 cp936, 强制 UTF-8 读写管道 (与父进程 mcp.py encoding='utf-8' 对齐)
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        ap = argparse.ArgumentParser()
        ap.add_argument("--root", default=None)
        ns, _ = ap.parse_known_args()
        cand = os.environ.get("LMW_SQLITE_ROOT") or (ns.root if ns else None)
        if cand and cand.isascii():
            raw = cand
        else:
            raw = os.path.splitdrive(os.getcwd())[0] + os.sep
        ROOT = _fix_enc(raw)
    except Exception:
        ROOT = os.path.splitdrive(os.getcwd())[0] + os.sep if os.path.splitdrive(os.getcwd())[0] else "D:\\"

    def _send(obj):
        try:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass

    def _handle(msg):
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"capabilities": {}, "serverInfo": {"name": "lmw-sqlite", "version": "1.0"}}})
            return
        if method == "notifications/initialized":
            return
        if method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": t["name"], "description": t["description"],
                 "inputSchema": {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in t["parameters"].items()}}}
                for t in TOOLS
            ]}})
            return
        if method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            arguments = params.get("arguments", {}) or {}
            if name == "db_query":
                out = _db_query(arguments)
            elif name == "db_list_tables":
                out = _db_list_tables(arguments)
            else:
                out = "[mcp error] 未知工具: %s" % name
            is_error = out.startswith("[mcp error]") or out.startswith("[db_") and "失败" in out
            _send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": out}], "isError": is_error}})
            return
        _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}})

    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            m = msg.get("method")
            if m == "initialize":
                _send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"capabilities": {}, "serverInfo": {"name": "lmw-sqlite", "version": "1.0"}}})
            elif m == "notifications/initialized":
                pass
            elif m == "tools/list":
                _handle(msg)
            elif m == "tools/call":
                _handle(msg)
            else:
                _send({"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32601, "message": "method not found"}})
    except Exception:
        pass


if __name__ == "__main__":
    main()

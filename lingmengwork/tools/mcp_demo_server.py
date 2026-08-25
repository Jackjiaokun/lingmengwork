"""内置 MCP 演示服务 (stdio, 零依赖): 用于 e2e 测试与无外部依赖演示。

仅向 stdout 输出 JSON-RPC 行; 所有日志走 stderr, 避免污染协议流。
运行: python -m lingmengwork.tools.mcp_demo_server

提供工具:
  - demo_echo  : 回显输入文本 (演示 agent 调用外部工具)
  - demo_time  : 返回服务端当前时间
"""
import sys
import time

_TRACE = "D:/mcp_demo_trace.log"


def _trace(msg):
    try:
        with open(_TRACE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


_trace("demo server start; exe=%s; argv0=%s" % (getattr(sys, "frozen", False), sys.argv[0] if sys.argv else ""))


def _send(obj):
    sys.stdout.write(json_line(obj))
    sys.stdout.flush()


def json_line(obj):
    return __import__("json").dumps(obj, ensure_ascii=False) + "\n"


TOOLS = [
    {
        "name": "demo_echo",
        "description": "回显输入文本 (灵梦work 内置 MCP 演示工具)",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要回显的文本"}},
            "required": ["text"],
        },
    },
    {
        "name": "demo_time",
        "description": "返回 MCP 服务端当前时间 (演示外部工具调用)",
        "inputSchema": {"type": "object", "properties": {}},
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
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lingmeng-demo", "version": "1.0"},
            },
        })
    elif method == "notifications/initialized":
        return  # 通知, 不回应
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = msg.get("params", {}) or {}
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        if name == "demo_echo":
            text = args.get("text", "")
            _send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "[echo] " + str(text)}], "isError": False}})
        elif name == "demo_time":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "server time: " + time.strftime("%H:%M:%S")}], "isError": False}})
        else:
            _send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "unknown tool: " + str(name)}], "isError": True}})
    else:
        if mid is not None:
            _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: " + str(method)}})


def main():
    # 强制 stdin/stdout 以 UTF-8 编解码: 同 mcp_fs_server, 避免中文 Windows 下
    # sys.stdin/stdout 默认 cp936(GBK) 误解码父进程发来的 UTF-8 中文参数。
    import io
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    _trace("demo server main loop enter; stdin.isatty=%s" % sys.stdin.isatty())
    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = __import__("json").loads(raw)
            except Exception:
                continue
            try:
                _handle(msg)
            except Exception as e:
                _trace("handle error: %s" % e)
                sys.stderr.write("demo server error: %s\n" % e)
    except Exception as e:
        _trace("stdin loop exited: %s" % e)
    _trace("demo server main loop exit")


if __name__ == "__main__":
    main()

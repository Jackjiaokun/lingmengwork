"""内置 fetch MCP 服务器 (stdio, 零依赖): 让本地 agent 真实联网抓取网页文本。

与 mcp_fs_server 同协议 (stdio JSON-RPC, 换行分隔):
  initialize -> notifications/initialized -> tools/list -> tools/call
仅向 stdout 输出 JSON-RPC 行; 日志/错误走 stderr, 避免污染协议流。

提供工具 (演示「开放工具中枢」可承载真实联网检索能力):
  - web_fetch : 抓取指定 URL 的网页文本 (零依赖 urllib, 自动解 gzip, 裁剪超大响应)

运行: python -m lingmengwork.tools.mcp_fetch_server
安全: 仅支持 http/https; 限制 max_chars 避免吞掉过多上下文; 设 User-Agent 与超时。
"""
import os
import sys
import io
import json
import urllib.request
import urllib.error
import gzip
import re

PROTOCOL_VERSION = "2024-11-05"

_UA = "Mozilla/5.0 (compatible; LingMengWork/1.0; +https://lingmeng.work)"


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _decode_body(raw, headers):
    """按 Content-Encoding 解压, 再按 charset 解码为文本。"""
    enc = (headers.get("Content-Encoding") or "").lower()
    data = raw
    if enc == "gzip":
        try:
            data = gzip.decompress(raw)
        except Exception:
            data = raw
    elif enc in ("deflate", "br"):
        # 零依赖不实现 brotli/deflate 全解; 回退原样尝试解码
        data = raw
    ctype = headers.get("Content-Type") or ""
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    charset = (m.group(1) if m else "utf-8").lower()
    try:
        return data.decode(charset, errors="replace")
    except (LookupError, Exception):
        return data.decode("utf-8", errors="replace")


def _strip_html(text):
    """极简清洗: 去掉 script/style 块与多余空白, 保留可读文本。零依赖。"""
    text = re.sub(r"(?is)<script[\s\S]*?</script>", " ", text)
    text = re.sub(r"(?is)<style[\s\S]*?</style>", " ", text)
    # 把块级标签换成换行, 便于阅读
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|br|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


TOOLS = [
    {
        "name": "web_fetch",
        "description": "抓取网页 URL 的文本内容 (零依赖联网, 自动解 gzip/utf-8, 清洗 HTML 标签), 用于给 agent 注入实时网页信息",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标网址 (http/https)"},
                "max_chars": {"type": "integer", "description": "返回最大字符数, 默认 8000"},
                "clean": {"type": "integer", "description": "1=清洗 HTML 仅留文本(默认), 0=保留原始 HTML 文本"},
            },
            "required": ["url"],
        },
    },
]


def _web_fetch(args):
    url = (args or {}).get("url") or ""
    if not url:
        return "[web_fetch] 缺少 url"
    if not re.match(r"^https?://", url, re.I):
        return "[web_fetch] 仅支持 http/https 链接: %s" % url
    try:
        max_chars = int((args or {}).get("max_chars", 8000) or 8000)
    except Exception:
        max_chars = 8000
    try:
        clean = int((args or {}).get("clean", 1))
    except Exception:
        clean = 1
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            headers = resp.headers
            status = getattr(resp, "status", None)
        text = _decode_body(raw, headers)
        if clean:
            text = _strip_html(text)
        text = text[:max_chars]
        return "[web_fetch] %s (状态 %s, 取前 %d 字符):\n%s" % (
            url, status if status else "?", max_chars, text,
        )
    except urllib.error.HTTPError as e:
        return "[web_fetch] HTTP 错误 %s: %s" % (e.code, e.reason)
    except urllib.error.URLError as e:
        return "[web_fetch] 网络错误: %s" % (e.reason if hasattr(e, "reason") else e)
    except Exception as e:
        return "[web_fetch] 抓取失败: %s" % e


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
                "serverInfo": {"name": "lingmeng-fetch", "version": "1.0"},
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
            if name == "web_fetch":
                text = _web_fetch(arguments)
                is_error = text.startswith("[web_fetch] 缺少") or text.startswith("[web_fetch] 仅支持") \
                    or text.startswith("[web_fetch] HTTP 错误") or text.startswith("[web_fetch] 网络错误") \
                    or text.startswith("[web_fetch] 抓取失败")
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
    # 强制 stdin/stdout 以 UTF-8 编解码 (同 fs/git 服务器, 防中文 Windows 冻结 exe 乱码)。
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
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
                sys.stderr.write("fetch server handle error: %s\n" % e)
    except Exception as e:
        sys.stderr.write("fetch server stdin loop exited: %s\n" % e)


if __name__ == "__main__":
    main()

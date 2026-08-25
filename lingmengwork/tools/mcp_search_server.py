"""lingmengwork 外部工具: 联网搜索 (web_search) MCP 服务器。

零依赖 (仅标准库 urllib), 通过 stdio JSON-RPC 与父进程通讯。
默认使用 DuckDuckGo HTML 端点检索, 解析结果标题/链接/摘要;
失败时回退到 Bing。中文查询无乱码 (子进程强制 UTF-8)。

工具:
  - web_search(query, max_results, engine): 联网搜索, 返回编号列表(标题/链接/摘要)。
"""

import sys
import os
import io
import json
import argparse
import urllib.request
import urllib.parse
import urllib.error
import re
import html


def _search_duckduckgo(q, max_results):
    data = urllib.parse.urlencode({"q": q}).encode("utf-8")
    req = urllib.request.Request(
        "https://html.duckduckgo.com/html/",
        data=data,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        body = r.read().decode("utf-8", "replace")
    results = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S):
        href = html.unescape(m.group(1))
        um = re.search(r"uddg=([^&]+)", href)
        url = urllib.parse.unquote(um.group(1)) if um else href
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        results.append((title, url))
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', body, re.S)
    out = []
    for i, (title, url) in enumerate(results[:max_results]):
        snip = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
        out.append("%d. %s\n   %s\n   %s" % (i + 1, title, url, snip))
    return out


def _search_bing(q, max_results):
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": q})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read().decode("utf-8", "replace")
    out = []
    for m in re.finditer(r'<li class="b_algo"[^>]*>.*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>', body, re.S):
        link = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        snip = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        out.append("%d. %s\n   %s\n   %s" % (len(out) + 1, title, link, snip))
        if len(out) >= max_results:
            break
    return out


def _web_search(args):
    q = (args or {}).get("query") or ""
    if not q.strip():
        return "[web_search] 缺少 query"
    try:
        max_results = int((args or {}).get("max_results", 8) or 8)
    except Exception:
        max_results = 8
    # 默认 Bing: 本机网络 DuckDuckGo(html 端点) 持续超时, Bing 直连稳定且秒级返回;
    # 仍可用 engine=duckduckgo 显式走 DDG(失败会自动回退 Bing)。
    engine = (args or {}).get("engine") or "bing"

    def _do(eng):
        if eng == "bing":
            return _search_bing(q, max_results)
        return _search_duckduckgo(q, max_results)

    # 主引擎优先; 任何失败(异常或空结果)都回退到另一引擎, 保证联网可用。
    try:
        out = _do(engine)
    except Exception:
        out = []
    if not out:
        fallback = "bing" if engine != "bing" else "duckduckgo"
        try:
            out = _do(fallback)
            engine = fallback  # 回退成功后如实标注实际使用的引擎
        except urllib.error.URLError as e:
            return "[web_search] 网络错误: %s" % e
        except Exception as e:
            return "[web_search] 失败: %s" % e
    if not out:
        return "[web_search] 无结果 (query=%s)" % q
    return "[web_search] 找到 %d 条 (query=%s, engine=%s)\n%s" % (len(out), q, engine, "\n\n".join(out))


TOOLS = [
    {
        "name": "web_search",
        "description": "联网搜索(默认 DuckDuckGo, 失败回退 Bing)。返回编号列表, 每项含 标题 / 链接 / 摘要。适合查文档、报错、最新信息。",
        "parameters": {
            "query": "搜索关键词 (支持中文)",
            "max_results": "返回条数, 默认 8",
            "engine": "duckduckgo 或 bing, 默认 duckduckgo",
        },
    },
]


def main():
    # 子进程默认 locale 可能为 cp936, 强制 UTF-8 读写管道 (与父进程 mcp.py encoding='utf-8' 对齐)
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    def _send(obj):
        try:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass

    def _handle(msg):
        mid = msg.get("id")
        params = msg.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}
        if name == "web_search":
            out = _web_search(arguments)
        else:
            out = "[mcp error] 未知工具: %s" % name
        is_error = out.startswith("[mcp error]") or out.startswith("[web_search] 失败") or out.startswith("[web_search] 网络")
        _send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": out}], "isError": is_error}})

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
                _send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"capabilities": {}, "serverInfo": {"name": "lmw-search", "version": "1.0"}}})
            elif m == "notifications/initialized":
                pass
            elif m == "tools/list":
                _send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": [
                    {"name": t["name"], "description": t["description"],
                     "inputSchema": {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in t["parameters"].items()}}}
                    for t in TOOLS
                ]}})
            elif m == "tools/call":
                _handle(msg)
            else:
                _send({"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32601, "message": "method not found"}})
    except Exception:
        pass


if __name__ == "__main__":
    main()

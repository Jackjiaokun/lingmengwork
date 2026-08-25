"""fetch MCP 服务器单测: 起本地 HTTP 服务, 验证 web_fetch 抓文本/清洗/错误分支。"""
import os
import sys
import threading
import time

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import():
    from lingmengwork.tools import mcp_fetch_server as m
    return m


_HTML = "<html><head><title>t</title></head><body><h1>标题</h1><p>正文内容 hello</p><script>var x=1;</script></body></html>"


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = _HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def srv():
    m = _import()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d/" % port
    httpd.shutdown()


def test_web_fetch_tools_listed():
    m = _import()
    names = [t["name"] for t in m.TOOLS]
    assert "web_fetch" in names


def test_web_fetch_clean_text(srv):
    m = _import()
    out = m._web_fetch({"url": srv, "max_chars": 8000, "clean": 1})
    assert "[web_fetch]" in out
    assert "标题" in out and "正文内容" in out
    assert "<script>" not in out and "<h1>" not in out  # 已清洗


def test_web_fetch_raw_html(srv):
    m = _import()
    out = m._web_fetch({"url": srv, "max_chars": 8000, "clean": 0})
    assert "<h1>" in out  # 未清洗保留标签


def test_web_fetch_bad_scheme():
    m = _import()
    out = m._web_fetch({"url": "ftp://example.com/x"})
    assert out.startswith("[web_fetch] 仅支持")


def test_web_fetch_missing_url():
    m = _import()
    out = m._web_fetch({"url": ""})
    assert out.startswith("[web_fetch] 缺少")

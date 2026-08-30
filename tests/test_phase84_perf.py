"""Phase 84 (P4 性能): 静态资源协商缓存 + 并发不阻塞。

P4 退出标准(摘自 outputs/world_class_assessment.md):
  1. 静态资源命中缓存
  2. 并发对话不互相阻塞
本文件把这两条标准变成可执行断言, 防止以后改回去而无人察觉。
"""
import io
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lingmengwork.web import server as srv_mod


@pytest.fixture(scope="module")
def real_server():
    """用项目真实 Handler 起一个临时服务(随机端口), 验证真实链路行为。"""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv_mod.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# ---------------- 退出标准 1: 静态资源命中缓存 ----------------

def test_static_returns_etag_and_cache_control(real_server):
    code, h, body = _get(real_server, "/static/ds.css")
    assert code == 200, "静态资源应正常返回"
    assert h.get("ETag"), "静态资源必须带 ETag(否则浏览器无从校验)"
    assert "no-cache" in (h.get("Cache-Control") or ""), "应声明 Cache-Control 校验策略"
    assert len(body) > 0


def test_static_304_on_matching_etag(real_server):
    _, h, _ = _get(real_server, "/static/ds.css")
    etag = h["ETag"]
    code2, h2, body2 = _get(real_server, "/static/ds.css", {"If-None-Match": etag})
    assert code2 == 304, "ETag 未变应返回 304 —— 这正是'命中缓存, 省掉整份传输'"
    assert len(body2) == 0, "304 响应不应携带 body"
    assert h2.get("ETag") == etag


def test_static_200_on_stale_etag(real_server):
    code, _, body = _get(real_server, "/static/ds.css", {"If-None-Match": '"stale-1"'})
    assert code == 200, "ETag 不匹配应返回完整内容"
    assert len(body) > 0


def test_html_page_also_cached(real_server):
    """页面 HTML 才是最大的资源(superagent.html 上千行), 同样必须可协商缓存。"""
    code, h, body = _get(real_server, "/superagent")
    assert code == 200
    assert h.get("ETag"), "页面 HTML 也应带 ETag"
    assert len(body) > 1000
    code2, _, body2 = _get(real_server, "/superagent", {"If-None-Match": h["ETag"]})
    assert code2 == 304, "页面重复访问应命中 304"


def test_static_blocks_path_traversal(real_server):
    """接管 _serve_static 后必须仍然挡得住目录穿越。"""
    code, _, _ = _get(real_server, "/static/..%2f..%2fconfig.toml")
    assert code in (400, 403, 404), "静态服务必须挡掉目录穿越(实际 %s)" % code


def test_etag_changes_when_file_changes(real_server, tmp_path):
    """ETag 必须随内容变化 —— 否则改完前端用户会一直拿到 304 旧副本。"""
    _, h1, _ = _get(real_server, "/static/ds.css")
    css = os.path.join(os.path.dirname(srv_mod.__file__), "static", "ds.css")
    stat = os.stat(css)
    try:
        # 短暂把 mtime 推到未来, ETag 应随之改变(不改动文件内容, 测后还原)
        os.utime(css, (stat.st_atime, stat.st_mtime + 10))
        _, h2, _ = _get(real_server, "/static/ds.css")
        assert h1["ETag"] != h2["ETag"], "文件变化后 ETag 必须改变"
        # 且旧 ETag 此时应判定为过期 -> 返回 200 而非 304
        code, _, _ = _get(real_server, "/static/ds.css", {"If-None-Match": h1["ETag"]})
        assert code == 200, "内容已变, 旧 ETag 不应再命中 304"
    finally:
        os.utime(css, (stat.st_atime, stat.st_mtime))


# ---------------- 退出标准 2: 并发不互相阻塞 ----------------

class _SlowHandler(BaseHTTPRequestHandler):
    """一个 1 秒的慢请求 + 若干瞬时请求, 用于验证服务端是否并发处理。"""

    def do_GET(self):
        body = b"slow" if self.path.startswith("/slow") else b"fast"
        if self.path.startswith("/slow"):
            time.sleep(1.0)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_threaded_server_does_not_block_concurrent_requests():
    """ThreadingHTTPServer: 一个慢请求不得阻塞其他请求(P4 退出标准 2)。

    若退回单线程 HTTPServer, 5 个 fast 会排在 1 个 slow 之后被串行处理,
    总耗时必然超过 1s 且 fast 的耗时会被拖长 —— 断言会失败。
    """
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]

    results = []
    lock = threading.Lock()

    def fetch(path):
        s = time.time()
        try:
            with urllib.request.urlopen(base + path, timeout=15) as r:
                r.read()
        finally:
            with lock:
                results.append((path, time.time() - s))

    threads = [threading.Thread(target=fetch, args=("/slow",))]
    threads += [threading.Thread(target=fetch, args=("/fast",)) for _ in range(5)]
    t0 = time.time()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    total = time.time() - t0
    httpd.shutdown()
    httpd.server_close()

    fast = [e for p, e in results if p == "/fast"]
    slow = [e for p, e in results if p == "/slow"]
    assert len(fast) == 5 and len(slow) == 1, "请求未全部完成: %s" % results

    assert max(fast) < 1.0, "有瞬时请求耗时 %.2fs, 说明被慢请求阻塞了" % max(fast)
    assert slow[0] >= 1.0, "慢请求本身应确实耗时 >= 1s(否则测不出阻塞)"
    assert total < 2.0, "总耗时 %.2fs, 请求被串行处理了" % total

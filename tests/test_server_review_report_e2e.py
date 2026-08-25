"""回归测试: 验证 Web 处理器正确调用「模块级」 _record_artifact (而非 self._record_artifact)。

历史 bug: _review_report / _pr_draft / _deliver_report 写成 self._record_artifact(...),
而 _record_artifact 是模块级函数 -> 'Handler' object has no attribute '_record_artifact' (500)。
本测试启动真实 HTTP 服务, 走完整 /api/review/report 路径, 断言 200 且成果已落盘并可在 /api/artifacts 回看。
"""
import os
import sys
import json
import time
import socket
import threading
import urllib.request
import urllib.error

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lingmengwork.web import server as S


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def base():
    from http.server import ThreadingHTTPServer
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), S.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % port
    for _ in range(60):
        try:
            with urllib.request.urlopen(url + "/api/mcp", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(0.5)
    yield url
    httpd.shutdown()


def _post(url, path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()


def _get(url, path):
    with urllib.request.urlopen(url + path, timeout=10) as r:
        return r.status, r.read()


@pytest.fixture
def prune_test_artifacts():
    idx = os.path.join(os.getcwd(), ".lmw_artifacts", "index.jsonl")
    yield
    # 清理本测试产生的 review/pr/delivery 成果, 避免污染工程
    if not os.path.exists(idx):
        return
    kept, removed = [], []
    with open(idx, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            (removed if rec.get("kind") in ("review", "pr", "delivery") else kept).append(rec)
    if removed:
        with open(idx, "w", encoding="utf-8") as f:
            for rec in kept:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        d = os.path.join(os.getcwd(), ".lmw_artifacts", "files")
        for rec in removed:
            fp = os.path.join(d, rec.get("name", ""))
            if fp and os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass


def test_review_report_records_and_lists(base, prune_test_artifacts):
    fp = os.path.join(os.path.dirname(S.__file__), "web", "server.py")
    status, body = _post(base, "/api/review/report",
                         {"paths": [fp], "note": "回归守卫"})
    assert status == 200, body[:200].decode("utf-8", "replace")
    assert "灵梦work" in body.decode("utf-8")  # 自包含 HTML 报告
    # 成果已落盘并可在 /api/artifacts 回看
    st, raw = _get(base, "/api/artifacts")
    assert st == 200
    data = json.loads(raw)
    assert data["total"] >= 1
    assert "review" in [it["kind"] for it in data["items"]]

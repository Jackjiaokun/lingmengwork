"""Phase 42 · 编排产物中心测试.

覆盖:
- GET /api/superagent/artifacts 清单(倒序/字段齐全/空目录)
- 文件预览(preview 截断标记)与下载(download 内容一致)
- 目录穿越/绝对路径/不存在 → 400
- 页面含产物中心区块(artList/previewArtifact)
"""

import http.client
import json
import os
import tempfile
import threading
import time

from lingmengwork.web import server as _srv


def _start_server(port):
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", port), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    return srv, old, d


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.read(), dict(r.getheaders())


def test_artifacts_list_preview_download():
    srv, old, d = _start_server(8994)
    try:
        root = os.path.join(d, "outputs", "superagent")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "20260828_code.md"), "wb") as f:
            f.write(("# 编排产物\n\n内容 A" * 10).encode("utf-8"))
        with open(os.path.join(root, "20260828_ops.md"), "wb") as f:
            f.write("# 运维脚本\n\necho ok".encode("utf-8"))
        time.sleep(0.05)
        with open(os.path.join(root, "20260828_research.md"), "wb") as f:
            f.write("# 研究简报\n\n结论".encode("utf-8"))

        # 清单: 倒序 + 字段
        st, body, _ = _get(8994, "/api/superagent/artifacts")
        assert st == 200
        data = json.loads(body)
        assert data["ok"] is True
        arts = data["artifacts"]
        assert [a["name"] for a in arts][:1] == ["20260828_research.md"], "最新应在前"
        a0 = arts[0]
        assert set(a0) >= {"name", "path", "ext", "size", "mtime"}
        assert a0["ext"] == "md"
        assert len(arts) == 3

        # 预览
        st, body, _ = _get(8994, "/api/superagent/artifacts/file?mode=preview&path=20260828_ops.md")
        assert st == 200
        pv = json.loads(body)
        assert pv["ok"] and pv["name"] == "20260828_ops.md"
        assert pv["content"].startswith("# 运维脚本")
        assert pv["truncated"] is False

        # 下载: 字节一致 + attachment 头
        st, body, hdrs = _get(8994, "/api/superagent/artifacts/file?mode=download&path=20260828_code.md")
        assert st == 200
        assert body == ("# 编排产物\n\n内容 A" * 10).encode("utf-8")
        assert "attachment" in (hdrs.get("Content-Disposition") or "")

        # 目录穿越 / 绝对路径 / 不存在 → 400
        for bad in ("..%2F..%2Fconfig.toml", "%2Fetc%2Fpasswd", "no_such.md", "....//x.md"):
            st, _, _ = _get(8994, "/api/superagent/artifacts/file?mode=preview&path=" + bad)
            assert st == 400, "非法路径应 400: %s" % bad
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_artifacts_empty_dir():
    srv, old, d = _start_server(8995)
    try:
        os.makedirs(os.path.join(d, "outputs", "superagent"), exist_ok=True)
        st, body, _ = _get(8995, "/api/superagent/artifacts")
        assert st == 200
        data = json.loads(body)
        assert data["ok"] is True and data["artifacts"] == []
        # 目录不存在也不崩
        st, body, _ = _get(8995, "/api/superagent/artifacts")
        assert st == 200
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_artifact_center():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "产物中心" in html
    assert "artList" in html and "previewArtifact" in html
    assert "/api/superagent/artifacts" in html

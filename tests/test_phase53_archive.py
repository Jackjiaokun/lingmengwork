"""Phase 53 · 产物自动归档压缩测试.

覆盖:
- archive_old_artifacts: 超龄产物进 zip + 原文件删除 / 新文件保留 / 幂等
- 编排历史 JSONL 超龄行修剪进 zip
- API POST /api/superagent/artifacts/archive; 页面含归档按钮
"""

import http.client
import json
import os
import tempfile
import threading
import time
import zipfile

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


def _make_old_new(root, old_name="old_art.md", new_name="new_art.md"):
    os.makedirs(root, exist_ok=True)
    old_p = os.path.join(root, old_name)
    new_p = os.path.join(root, new_name)
    with open(old_p, "wb") as f:
        f.write("# 超龄产物内容".encode("utf-8"))
    with open(new_p, "wb") as f:
        f.write("# 新鲜产物".encode("utf-8"))
    old_ts = time.time() - 40 * 86400
    os.utime(old_p, (old_ts, old_ts))
    return old_p, new_p


def test_archive_old_artifacts(tmp_path):
    root = os.path.join(str(tmp_path), "outputs", "superagent")
    old_p, new_p = _make_old_new(root)
    rep = sa_mod.archive_old_artifacts(base_dir=str(tmp_path), max_age_days=30)
    assert rep["ok"] is True and rep["archived"] == 1
    assert rep["bytes_freed"] > 0 and rep["zip"].startswith("archive_")
    # 旧文件已删除, 新文件保留
    assert not os.path.exists(old_p)
    assert os.path.exists(new_p)
    # zip 内条目内容可读
    zp = os.path.join(root, rep["zip"])
    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
        assert any(n.endswith("old_art.md") for n in names), "zip 应含超龄产物"
        assert zf.read(names[0]) == "# 超龄产物内容".encode("utf-8")
    # 幂等: 二次归档 0
    rep2 = sa_mod.archive_old_artifacts(base_dir=str(tmp_path), max_age_days=30)
    assert rep2["archived"] == 0


def test_archive_trims_runs_jsonl(tmp_path):
    path = sa_mod._persist_path(str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old_ts = (datetime_from_now(-40)).strftime("%Y-%m-%d %H:%M:%S")
    new_ts = sa_mod._now()
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": old_ts, "summary": {"goal": "旧行", "ts": old_ts, "ok": True}}) + "\n")
        f.write(json.dumps({"ts": new_ts, "summary": {"goal": "新行", "ts": new_ts, "ok": True}}) + "\n")
    rep = sa_mod.archive_old_artifacts(base_dir=str(tmp_path), max_age_days=30)
    assert rep["archived"] == 1, "旧行应归档"
    lines = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    assert len(lines) == 1 and lines[0]["summary"]["goal"] == "新行", "新行应保留"


def datetime_from_now(delta_days):
    from datetime import datetime, timedelta
    return datetime.now() + timedelta(days=delta_days)


def test_archive_api_e2e(tmp_path):
    d = str(tmp_path)
    old = os.getcwd()
    os.chdir(d)
    root = os.path.join(d, "outputs", "superagent")
    old_p, _ = _make_old_new(root)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9114), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9114, timeout=15)
        c.request("POST", "/api/superagent/artifacts/archive",
                  body=json.dumps({"max_age_days": 30}).encode(),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read().decode())
        assert r.status == 200 and data["ok"] is True and data["archived"] == 1
        assert not os.path.exists(old_p)
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_archive_button():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "归档30天前产物" in html and "archiveArtifacts" in html

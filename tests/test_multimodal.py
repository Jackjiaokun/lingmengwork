"""Phase 21 — 多模态基座 (multimodal) 测试。

覆盖：统一 MediaAsset 抽象、AssetLibrary (SQLite 落盘/检索/删除/配额)、
generate() 包装 multimodal_adapters.render 并登记入库、三域降级不崩、
服务端 GET/POST/DELETE /api/multimodal 全链路。

所有写盘测试均 chdir(tmp_path) 隔离, 避免污染仓库 outputs/.lmw_media。
"""
import json
import os
import time
import tempfile
import threading
import http.client

import pytest

from lingmengwork import multimodal as mm
from lingmengwork.web import server as _srv


def _asset(kind="image", path="/tmp/x.png", real=True):
    return mm.MediaAsset(
        id="A%012d" % (int(time.time() * 1000) % 1000000000000),
        kind=kind, path=path, mime="image/png",
        source="local" if real else "local:fallback", real=real,
        meta={"k": 1}, note="测试资产", created_at=time.time(),
    )


def test_asset_library_crud(tmp_path):
    lib = mm.AssetLibrary(str(tmp_path))
    a = _asset(path=os.path.join(str(tmp_path), "x.png"))
    lib.save(a)
    items = lib.list()
    assert len(items) == 1
    assert items[0]["id"] == a.id
    assert items[0]["url"].startswith("/outputs/")
    got = lib.get(a.id)
    assert got and got["kind"] == "image"
    # 按类型检索
    assert lib.list(kind="image") and not lib.list(kind="video")
    # 按语义检索
    assert lib.list(q="测试资产")
    # 删除
    assert lib.delete(a.id) is True
    assert lib.list() == []
    assert lib.get(a.id) is None


def test_register_from_art(tmp_path):
    fpath = os.path.join(str(tmp_path), "o.png")
    with open(fpath, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")  # 占位 PNG 头, 让路径存在
    art = {"domain": "image", "file": fpath,
           "mime": "image/png", "real": True, "note": "Pillow 渲染",
           "meta": {"width": 1280, "height": 720}}
    asset = mm.register_asset(art, session_id="S1", base_dir=str(tmp_path))
    assert asset and asset["id"]
    assert asset["kind"] == "image" and asset["real"] is True
    assert asset["session_id"] == "S1"
    assert os.path.exists(asset["path"])
    assert len(mm.AssetLibrary(str(tmp_path)).list()) == 1


def test_generate_image_registers(tmp_path):
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        asset = mm.generate("image", "科技品牌主视觉海报", llm_call=None)
        assert asset, "image 应产出"
        assert asset["kind"] == "image"
        assert os.path.exists(asset["path"])
        assert asset["url"].startswith("/outputs/")
        assert len(mm.AssetLibrary(str(tmp_path)).list()) == 1
    finally:
        os.chdir(old)


def test_generate_audio_and_video(tmp_path):
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        a = mm.generate("audio", "给这段解说配语音")
        assert a and os.path.exists(a["path"]), "audio 降级也应产出文件"
        v = mm.generate("video", "10 秒产品宣传视频")
        assert v and os.path.exists(v["path"]), "video 应产出 GIF"
        assert mm.AssetLibrary(str(tmp_path)).list(kind="audio")
        assert mm.AssetLibrary(str(tmp_path)).list(kind="video")
    finally:
        os.chdir(old)


def test_generate_invalid_domain(tmp_path):
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        assert mm.generate("not_a_domain", "x") is None
    finally:
        os.chdir(old)


def test_quota_cleanup(tmp_path):
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        for i in range(5):
            mm.generate("image", "批量海报 %d" % i, llm_call=None)
        lib = mm.AssetLibrary(str(tmp_path))
        assert len(lib.list()) == 5
        # 极小配额触发 LRU 清理
        removed = lib.quota_bytes(limit_gb=0.000001)
        assert removed >= 1
        assert len(lib.list()) < 5
    finally:
        os.chdir(old)


def test_server_multimodal_api():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8975
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", PORT), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        def post(path, body):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            c.request("POST", path, body=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        def delete(path):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
            c.request("DELETE", path)
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        # 空库
        st, js = get("/api/multimodal")
        assert st == 200, (st, js)
        assert json.loads(js)["count"] == 0

        # 生成 image
        stg, jsg = post("/api/multimodal/generate",
                        {"domain": "image", "brief": "多模态工坊封面"})
        assert stg == 200, (stg, jsg)
        dg = json.loads(jsg)
        assert dg["ok"] and dg["asset"]["id"]
        aid = dg["asset"]["id"]

        # 画廊可见
        st2, js2 = get("/api/multimodal?kind=image")
        assert st2 == 200
        d2 = json.loads(js2)
        assert d2["count"] == 1 and d2["assets"][0]["id"] == aid
        assert os.path.exists(d2["assets"][0]["path"])

        # 删除
        std, jsd = delete("/api/multimodal/" + aid)
        assert std == 200 and json.loads(jsd)["ok"] is True
        st3, js3 = get("/api/multimodal")
        assert json.loads(js3)["count"] == 0

        # 页面含画廊与生成入口
        stp, html = get("/multimodal")
        assert stp == 200
        assert "资产库画廊" in html and "生成并入库" in html
        assert "/api/multimodal/generate" in html
    finally:
        srv.shutdown()
        os.chdir(old)

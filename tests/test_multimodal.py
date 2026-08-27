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


# ----------------------------------------------------------------------------
# Phase 22 — 音频域深化 (语音合成 / 本地配乐 / 语音克隆)
# ----------------------------------------------------------------------------

def test_audio_music_real_wav(tmp_path):
    """本地配乐合成: 零依赖 wave 产出真实可播放 WAV (不依赖 edge_tts/网络/key)。"""
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        a = mm.generate("audio", "电商促销背景配乐 bpm=120 小节=2", mode="music", llm_call=None)
        assert a, "music 模式应产出"
        assert a["kind"] == "audio"
        assert a["real"] is True, "本地合成是真实音频"
        assert a["mime"] == "audio/wav"
        assert os.path.exists(a["path"])
        assert os.path.getsize(a["path"]) > 44, "WAV 应含音频帧 (>44 字节头)"
        assert (a["meta"] or {}).get("duration"), "应含时长元数据"
        assert (a["meta"] or {}).get("synth") == "local"
    finally:
        os.chdir(old)


def test_audio_clone_fallback(tmp_path):
    """语音克隆: 无参考音频/API 时降级为声波占位图 + 结构说明 (不崩)。"""
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        a = mm.generate("audio", "用我的声音念这段广告词", mode="clone", llm_call=None)
        assert a, "clone 降级应产出占位"
        assert a["kind"] == "audio"
        assert a["real"] is False, "克隆需 API, 此处降级"
        assert (a["meta"] or {}).get("clone") is True
        assert os.path.exists(a["path"])
    finally:
        os.chdir(old)


def test_audio_tts_mode_passthrough(tmp_path):
    """语音合成模式 (默认) 显式透传 mode=tts, 无论真实/降级均产出音频资产不崩。"""
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        a = mm.generate("audio", "播报今日天气", mode="tts", voice="zh-CN-YunxiNeural", llm_call=None)
        assert a and a["kind"] == "audio"
        assert os.path.exists(a["path"])
    finally:
        os.chdir(old)


def test_server_audio_music_mode():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8976
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", PORT), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def post(path, body):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            c.request("POST", path, body=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        # 音乐模式经服务端返回真实 WAV
        st, js = post("/api/multimodal/generate",
                      {"domain": "audio", "brief": "测试配乐 bpm=120 小节=2", "mode": "music"})
        assert st == 200, (st, js)
        dg = json.loads(js)
        assert dg["ok"] and dg["asset"]["mime"] == "audio/wav"
        assert dg["asset"]["real"] is True

        # music 模式误用于 image 域应被拒
        st2, js2 = post("/api/multimodal/generate",
                        {"domain": "image", "brief": "x", "mode": "music"})
        assert st2 == 400
    finally:
        srv.shutdown()
        os.chdir(old)


# ----------------------------------------------------------------------------
# Phase 23 — 图像域深化 (文生图 / 局部重绘 / 超分放大)
# ----------------------------------------------------------------------------

def test_image_gen_local_real(tmp_path):
    """文生图(gen, 默认): 无图像生成 key 时本地 Pillow 真实 PNG (real=True), 标注 gen=local。"""
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        a = mm.generate("image", "科技品牌主视觉海报", mode="gen", llm_call=None)
        assert a, "gen 应产出"
        assert a["kind"] == "image"
        assert a["real"] is True, "本地 Pillow 信息图是真实媒体"
        assert a["mime"] == "image/png"
        assert os.path.exists(a["path"])
        assert (a["meta"] or {}).get("gen") == "local"
    finally:
        os.chdir(old)


def test_image_inpaint_real(tmp_path):
    """局部重绘(inpaint): 无参考图时用演示画布, 仍产出真实 PNG (real=True, meta.inpaint)。"""
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        a = mm.generate("image", "把中心区域换成产品 logo", mode="inpaint", llm_call=None)
        assert a, "inpaint 应产出"
        assert a["kind"] == "image"
        assert a["real"] is True
        assert (a["meta"] or {}).get("inpaint") is True
        assert os.path.exists(a["path"])
    finally:
        os.chdir(old)


def test_image_upscale_real(tmp_path):
    """超分放大(upscale): 无参考图时用演示样本 LANCZOS 2x, 尺寸放大且 real=True。"""
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        a = mm.generate("image", "放大这张缩略图", mode="upscale", llm_call=None)
        assert a, "upscale 应产出"
        assert a["real"] is True
        m = a["meta"] or {}
        assert m.get("upscale") is True
        assert m.get("scale") == 2
        # 真实文件尺寸应大于源 (演示画布 640x400 -> 1280x800)
        assert m.get("width", 0) > m.get("src_width", 0)
        from PIL import Image
        im = Image.open(a["path"])
        assert im.size[0] == m.get("width")
    finally:
        os.chdir(old)


def test_server_image_mode_routing():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8977
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", PORT), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def post(path, body):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            c.request("POST", path, body=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        # inpaint 经服务端返回真实 PNG
        st, js = post("/api/multimodal/generate",
                      {"domain": "image", "brief": "重绘中心", "mode": "inpaint"})
        assert st == 200, (st, js)
        dg = json.loads(js)
        assert dg["ok"] and dg["asset"]["mime"] == "image/png"
        assert (dg["asset"]["meta"] or {}).get("inpaint") is True

        # upscale 经服务端返回真实 PNG (尺寸放大)
        st2, js2 = post("/api/multimodal/generate",
                        {"domain": "image", "brief": "放大", "mode": "upscale"})
        assert st2 == 200, (st2, js2)
        dg2 = json.loads(js2)
        assert dg2["ok"] and dg2["asset"]["real"] is True
        assert (dg2["asset"]["meta"] or {}).get("upscale") is True

        # inpaint 模式误用于 audio 域应被拒
        st3, js3 = post("/api/multimodal/generate",
                        {"domain": "audio", "brief": "x", "mode": "inpaint"})
        assert st3 == 400
    finally:
        srv.shutdown()
        os.chdir(old)


# ----------------------------------------------------------------------------
# Phase 24 — 视频域深化 (文生视频 / 图生视频 / 剪辑配音)
# ----------------------------------------------------------------------------

def test_video_gen_local_gif(tmp_path):
    """文生视频默认降级为本地真实 GIF 分镜动图 (无 key 环境)。"""
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        a = mm.generate("video", "10 秒产品宣传视频", llm_call=None)
        assert a and os.path.exists(a["path"]), "video gen 应产出 GIF"
        assert a["kind"] == "video"
        assert a["real"] is True
        assert a["mime"] == "image/gif"
        assert (a["meta"] or {}).get("gen") == "local"
    finally:
        os.chdir(old)


def test_video_img2video_real_gif(tmp_path):
    """图生视频: 有参考图做 Ken Burns 运动镜头, 真实 GIF; 无参考图用演示画布。"""
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        from PIL import Image, ImageDraw
        ref = os.path.join(str(tmp_path), "ref.png")
        im = Image.new("RGB", (400, 300), (40, 30, 80))
        ImageDraw.Draw(im).text((20, 20), "REF", fill=(240, 240, 255))
        im.save(ref, "PNG")
        a = mm.generate("video", "把这张图变成视频", mode="img2video",
                        image_path=ref, llm_call=None)
        assert a and os.path.exists(a["path"])
        assert a["real"] is True and a["mime"] == "image/gif"
        assert (a["meta"] or {}).get("img2video") is True
        assert (a["meta"] or {}).get("has_ref") is True
        # 无参考图也应产出 (演示画布)
        a2 = mm.generate("video", "无参考图图生视频", mode="img2video", llm_call=None)
        assert a2 and os.path.exists(a2["path"])
        assert a2["real"] is True
    finally:
        os.chdir(old)


def test_video_clips_real_gif(tmp_path):
    """剪辑配音: 多图幻灯片合成真实 GIF; 无图用演示画布。"""
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        from PIL import Image, ImageDraw
        refs = []
        for i in range(2):
            p = os.path.join(str(tmp_path), "s%d.png" % i)
            im = Image.new("RGB", (400, 300), (30 + i * 20, 20, 60))
            ImageDraw.Draw(im).text((20, 20), "S%d" % i, fill=(240, 240, 255))
            im.save(p, "PNG")
            refs.append(p)
        a = mm.generate("video", "剪辑这两张图", mode="clips",
                        image_path=",".join(refs), llm_call=None)
        assert a and os.path.exists(a["path"])
        assert a["real"] is True and a["mime"] == "image/gif"
        assert (a["meta"] or {}).get("clips") is True
        assert (a["meta"] or {}).get("slides") == 2
    finally:
        os.chdir(old)


def test_video_gen_remote_mp4(monkeypatch, tmp_path):
    """有 key 时调远程真实 MP4 (mock _remote_text_to_video 返回字节, 不触网)。"""
    import lingmengwork.multimodal_adapters as _ma
    monkeypatch.setattr(_ma, "_remote_text_to_video", lambda p: b"\x00" * 200)
    old = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        a = mm.generate("video", "一段真实 MP4", mode="gen", llm_call=None)
        assert a and os.path.exists(a["path"])
        assert a["real"] is True
        assert a["mime"] == "video/mp4"
        assert (a["meta"] or {}).get("gen") == "remote"
    finally:
        os.chdir(old)


def test_server_video_mode_routing():
    """服务端: video 域三模式路由全绿, 误用模式 400, 默认 mode 归 gen, 页面含视频卡。"""
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8978
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", PORT), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def post(path, body):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            c.request("POST", path, body=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        # gen 模式 (默认)
        st, js = post("/api/multimodal/generate", {"domain": "video", "brief": "宣传片"})
        assert st == 200, (st, js)
        dg = json.loads(js)
        assert dg["ok"] and dg["asset"]["real"] is True

        # img2video 模式
        st2, js2 = post("/api/multimodal/generate",
                        {"domain": "video", "brief": "图生视频", "mode": "img2video"})
        assert st2 == 200, (st2, js2)
        assert json.loads(js2)["asset"]["real"] is True

        # clips 模式
        st3, js3 = post("/api/multimodal/generate",
                        {"domain": "video", "brief": "剪辑", "mode": "clips"})
        assert st3 == 200, (st3, js3)
        assert json.loads(js3)["asset"]["real"] is True

        # 误用: img2video 配 audio 域 → 400
        st4, js4 = post("/api/multimodal/generate",
                        {"domain": "audio", "brief": "x", "mode": "img2video"})
        assert st4 == 400

        # 默认 mode (不传 mode, domain=video) → 归 gen → 200
        st5, js5 = post("/api/multimodal/generate",
                        {"domain": "video", "brief": "默认模式"})
        assert st5 == 200, (st5, js5)
        assert json.loads(js5)["asset"]["real"] is True

        # 页面含视频模式卡
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
        c.request("GET", "/multimodal")
        r = c.getresponse()
        html = r.read().decode("utf-8", "replace")
        assert r.status == 200
        assert "视频模式" in html and "img2video" in html and "clips" in html
    finally:
        srv.shutdown()
        os.chdir(old)

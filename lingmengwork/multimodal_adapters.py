"""真实多模态适配层 (Multimodal Adapters) —— 终极蓝图 Phase 8 + Phase 12.

Phase 8: 把 creation_domains 的 audio/image/video 域从「蓝图产出」升级为「真实媒体文件交付」。
Phase 12: LLM 智能设计驱动 —— image/audio/video 三域先调 LLM 生成结构化设计 (配色/要点/分镜/朗读稿),
          再真实产出媒体; 无 LLM(key) 时自动回退到确定性模板, 全程可用。

- image : LLM 设计信息图布局 (JSON: 标题/副标题/要点/配色) -> Pillow 真实 PNG
- audio : LLM 提炼朗读文稿 -> 优先 edge_tts 真实 MP3; 不可用时降级「文字稿 + 声波占位图」
- video : LLM 规划分镜 (标题 + 多画面字幕) -> Pillow 真实多帧 GIF 动图

所有产出落盘 out_dir (默认 <cwd>/outputs/multimodal), 返回结构化 dict:
    {
      "domain": "image"|"audio"|"video",
      "file": "<绝对路径>",
      "mime": "image/png"|"audio/mpeg"|"image/gif",
      "real": True/False,           # True=真实媒体; False=降级占位(明确标注)
      "note": "补充说明(如降级原因)",
      "meta": {...}                  # 时长/尺寸/字数/llm_designed 等
    }

设计原则: 任何单域失败 / LLM 失败不影响其余域; 无外部 key / 无网络时自动降级且全程可用。
"""

import os
import re
import math
import json
import time
import struct

from PIL import Image, ImageDraw, ImageFont

# 域 -> 主题色 (深空蓝紫品牌体系)
_DOM_THEME = {
    "image": (244, 114, 182),   # 图片 · 粉
    "audio": (16, 185, 129),    # 音频 · 绿
    "video": (99, 102, 241),    # 视频 · 蓝紫
}

# 域 -> 中文标签
_DOM_LABEL = {
    "image": "图片",
    "audio": "音频",
    "video": "视频",
}

# Phase 12 · 配色板: (渐变 top, 渐变 bottom, 主题光条色)
_PALETTES = {
    "nebula": ((18, 12, 42), (31, 17, 71), (139, 92, 246)),
    "sunset": ((42, 18, 30), (71, 28, 40), (244, 114, 182)),
    "ocean":  ((10, 24, 42), (16, 40, 64), (56, 189, 248)),
    "forest": ((12, 32, 22), (18, 50, 36), (52, 211, 153)),
    "rose":   ((38, 14, 32), (64, 22, 52), (244, 114, 182)),
}


# ----------------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------------

def _out_dir(out_dir=None):
    base = out_dir or os.path.join(os.getcwd(), "outputs", "multimodal")
    os.makedirs(base, exist_ok=True)
    return base


def _slug(s, n=28):
    s = re.sub(r"[^\w一-鿿]+", "_", (s or "asset")).strip("_")
    return (s[:n] or "asset").strip("_")


def _load_font(size, bold=False):
    """优先加载系统中文字体(微软雅黑), 失败回退默认。"""
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _extract_script(text, max_chars=700):
    """从 blueprint/brief 抽取可用于朗读 / 字幕的正文。"""
    if not text:
        return "灵梦work 多模态适配层自动生成演示。"
    t = re.sub(r"[#>*`\-\[\](){}|]", " ", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_chars] or "灵梦work 多模态适配层自动生成演示。"


def _extract_points(text, max_n=5):
    """从蓝图抽取要点列表 (按行 / 编号 / 短句)。"""
    if not text:
        return []
    raw = re.sub(r"[#>*`]", "", text)
    lines = [l.strip(" -•") for l in raw.splitlines() if l.strip()]
    pts = []
    for l in lines:
        if re.match(r"^(\d+[.、]|[-•])", l):
            l = re.sub(r"^(\d+[.、]|[-•])\s*", "", l)
        if 4 <= len(l) <= 40 and l not in pts:
            pts.append(l)
        if len(pts) >= max_n:
            break
    if not pts:
        # 退而求其次: 按中文句号切
        segs = [s.strip() for s in re.split(r"[。！？;；]", text) if 4 <= len(s.strip()) <= 40]
        pts = segs[:max_n]
    return pts[:max_n]


def _grad_bg(w, h, top, bottom):
    """线性垂直渐变背景。"""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        d.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def _parse_json_block(text):
    """从 LLM 文本中稳健抽取 JSON 对象 (兼容 ```json 围栏 + 前后杂文本)。"""
    if not text:
        return None
    s = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    i = s.find("{")
    j = s.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            return None
    return None


# ----------------------------------------------------------------------------
# Phase 12 · LLM 智能设计 (无 LLM / 解析失败则回退 None, 由渲染函数兜底)
# ----------------------------------------------------------------------------

def _design_image(brief, blueprint, ctx, llm_call):
    """调 LLM 生成信息图视觉方案 JSON。失败返回 None。"""
    sys = ("你是资深信息图设计师。只输出一个 JSON 对象, 不要任何解释或前后缀。"
           "字段: title(短标题,str), subtitle(副标题,str), points(要点数组, 最多6条 str), "
           "palette(配色名, 取值 nebula|sunset|ocean|forest|rose), style(布局, 取值 minimal|rich)。")
    prompt = "目标/主题：%s\n参考蓝图：%s\n请为这张信息图设计视觉方案。" % (
        (brief or "")[:600], (blueprint or "")[:600])
    try:
        raw = llm_call(prompt, system=sys)
        j = _parse_json_block(raw)
        if not isinstance(j, dict):
            return None
        pts = j.get("points") or []
        if not isinstance(pts, list):
            pts = []
        pts = [str(p).strip() for p in pts if str(p).strip()][:6]
        pal = str(j.get("palette") or "nebula")
        if pal not in _PALETTES:
            pal = "nebula"
        return {
            "title": str(j.get("title") or "").strip() or (brief or "灵梦work 多模态创作").split("\n")[0][:32],
            "subtitle": str(j.get("subtitle") or "").strip(),
            "points": pts,
            "palette": pal,
            "style": str(j.get("style") or "rich"),
        }
    except Exception:
        return None


def _design_audio_script(brief, blueprint, ctx, llm_call):
    """调 LLM 提炼适合朗读的连贯文稿。失败返回 None。"""
    sys = ("你是播报文稿编辑。把给定内容改写成适合语音朗读的连贯中文文稿 "
           "(1-3 句, 不超过 200 字)。只输出文稿文本, 不要解释或格式符号。")
    prompt = "主题：%s\n参考蓝图：%s" % ((brief or "")[:500], (blueprint or "")[:500])
    try:
        raw = llm_call(prompt, system=sys)
        if raw and str(raw).strip():
            return str(raw).strip()[:800]
    except Exception:
        pass
    return None


def _design_video_shots(brief, blueprint, ctx, llm_call):
    """调 LLM 规划视频分镜字幕 JSON。失败返回 None。"""
    sys = ("你是短视频分镜师。只输出一个 JSON 对象, 不要解释。"
           "字段: title(短片标题,str), shots(画面字幕数组, 最多5条 str, 每条是随时间出现的要点)。")
    prompt = "主题：%s\n参考蓝图：%s\n请规划这段视频的分镜字幕。" % (
        (brief or "")[:500], (blueprint or "")[:500])
    try:
        raw = llm_call(prompt, system=sys)
        j = _parse_json_block(raw)
        if not isinstance(j, dict):
            return None
        shots = j.get("shots") or []
        if not isinstance(shots, list):
            shots = []
        shots = [str(s).strip() for s in shots if str(s).strip()][:5]
        if not shots:
            return None
        return {
            "title": str(j.get("title") or "").strip() or (brief or "灵梦work 视频资产").split("\n")[0][:26],
            "shots": shots,
        }
    except Exception:
        return None


# ----------------------------------------------------------------------------
# 图片域: 三模式 (gen 文生图 / inpaint 局部重绘 / upscale 超分放大)
# ----------------------------------------------------------------------------

def _remote_text_to_image(prompt):
    """密钥可用时调用 OpenAI 兼容文生图 API 返回 PNG 字节; 无 key / 失败返回 None。"""
    key = os.environ.get("LMW_IMAGE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        import base64
        import urllib.request
        payload = json.dumps({
            "model": "gpt-image-1",
            "prompt": (prompt or "")[:1000],
            "size": "1024x1024",
            "response_format": "b64_json",
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=payload,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        b64 = data["data"][0]["b64_json"]
        return base64.b64decode(b64)
    except Exception:
        return None


def _remote_text_to_video(prompt):
    """密钥可用时调用 OpenAI 兼容视频生成 API 返回 MP4 字节; 无 key / 失败返回 None。

    与 _remote_text_to_image 对齐 —— 密钥门控 + 全面 try/except 降级, 绝不阻塞主流程。
    可经 LMW_VIDEO_API_KEY / LMW_VIDEO_API_URL / LMW_VIDEO_MODEL 环境变量定制端点。
    """
    key = os.environ.get("LMW_VIDEO_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        import base64
        import urllib.request
        api_url = os.environ.get("LMW_VIDEO_API_URL") or "https://api.openai.com/v1/videos/generations"
        model = os.environ.get("LMW_VIDEO_MODEL") or "gpt-video-1"
        payload = json.dumps({
            "model": model,
            "prompt": (prompt or "")[:1000],
            "duration": "5",
            "response_format": "url",
        }).encode("utf-8")
        req = urllib.request.Request(
            api_url, data=payload,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        item = (data.get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            with urllib.request.urlopen(item["url"], timeout=60) as r2:
                return r2.read()
    except Exception:
        return None
    return None


def _make_demo_canvas(w=640, h=400, label="demo"):
    """无参考图时合成一张演示画布, 让 inpaint/upscale 始终产出真实 PNG。"""
    img = _grad_bg(w, h, (18, 12, 42), (31, 17, 71))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 8], fill=_DOM_THEME["image"])
    f = _load_font(30)
    d.text((30, h // 2 - 20), label, font=f, fill=(245, 243, 255))
    return img


def _render_image_gen(brief, blueprint, ctx, out_dir, llm_call=None):
    """文生图: 有图像生成 key 走远程 API (真实生成); 否则本地 Pillow 真实信息图 (real=True)。"""
    # 远程真实生成 (密钥门控, 失败自动回退本地)
    remote = _remote_text_to_image((brief or "")[:1000])
    if remote:
        fn = "%s.png" % _slug(brief)
        path = os.path.join(_out_dir(out_dir), fn)
        try:
            with open(path, "wb") as f:
                f.write(remote)
            if os.path.exists(path) and os.path.getsize(path) > 100:
                return {
                    "domain": "image", "file": path, "mime": "image/png", "real": True,
                    "note": "远程文生图 API 真实生成 (有 key)",
                    "meta": {"gen": "remote", "width": 1024, "height": 1024},
                }
        except Exception:
            pass

    # 本地真实信息图 (LLM 设计 / 模板回退)
    design = _design_image(brief, blueprint, ctx, llm_call) if llm_call else None
    pal = (design or {}).get("palette") or "nebula"
    top, bottom, theme = _PALETTES.get(pal, _PALETTES["nebula"])
    W, H = 1280, 720
    img = _grad_bg(W, H, top, bottom)
    d = ImageDraw.Draw(img)
    for y in range(8):
        d.line([(0, y), (W, y)], fill=theme)
    d.rectangle([60, 120, 78, 600], fill=theme)

    f_title = _load_font(46)
    f_sub = _load_font(24)
    f_pt = _load_font(26)
    title = (design or {}).get("title") or (brief or "灵梦work 多模态创作").split("\n")[0][:32]
    d.text((100, 140), title, font=f_title, fill=(245, 243, 255))
    sub = (design or {}).get("subtitle") or "IMAGE · 图片资产 · 灵梦work 真实生成"
    d.text((100, 200), sub, font=f_sub, fill=(186, 180, 220))

    pts = (design or {}).get("points") or _extract_points(blueprint or brief, 5)
    y = 280
    for p in pts:
        d.ellipse([104, y + 6, 120, y + 22], fill=theme)
        d.text((140, y), "• %s" % p, font=f_pt, fill=(228, 224, 245))
        y += 56

    tag = "Phase 12 · LLM 智能设计" if design else "Phase 8 · 模板回退"
    d.text((100, H - 56), "灵梦work · 多模态适配层 (%s) 自动产出" % tag, font=f_sub, fill=(150, 144, 185))

    fn = "%s.png" % _slug(brief)
    path = os.path.join(_out_dir(out_dir), fn)
    img.save(path, "PNG")
    return {
        "domain": "image", "file": path, "mime": "image/png", "real": True,
        "note": "Pillow 真实渲染信息图 (%s)" % ("LLM 设计驱动" if design else "模板回退"),
        "meta": {"width": W, "height": H, "points": len(pts), "gen": "local",
                 "llm_designed": bool(design), "palette": pal},
    }


def _render_image_inpaint(brief, blueprint, ctx, out_dir, llm_call=None, image_path=None):
    """局部重绘: 给定参考图(可选)在中心区域重绘; 无参考图用演示画布。始终真实 PNG (real=True)。"""
    if image_path and os.path.exists(image_path):
        try:
            src = Image.open(image_path).convert("RGB")
            used = "provided"
        except Exception:
            src = _make_demo_canvas(label="Inpaint 演示画布")
            used = "demo"
    else:
        src = _make_demo_canvas(label="Inpaint 演示画布")
        used = "demo"
    W, H = src.size
    d = ImageDraw.Draw(src)
    box = [W // 4, H // 4, 3 * W // 4, 3 * H // 4]
    region = _grad_bg(box[2] - box[0], box[3] - box[1], (56, 189, 248), (139, 92, 246))
    src.paste(region, box[:2])
    f = _load_font(22)
    d.text((box[0] + 12, box[1] + 12), "重绘区: " + (brief or "局部重绘")[:18], font=f, fill=(255, 255, 255))
    fn = "%s.inpaint.png" % _slug(brief)
    path = os.path.join(_out_dir(out_dir), fn)
    src.save(path, "PNG")
    return {
        "domain": "image", "file": path, "mime": "image/png", "real": True,
        "note": "局部重绘 (中心区域重绘%s)" % ("参考图" if used == "provided" else "演示画布"),
        "meta": {"inpaint": True, "source": used, "width": W, "height": H},
    }


def _render_image_upscale(brief, blueprint, ctx, out_dir, llm_call=None, image_path=None):
    """超分放大: 给定参考图(可选) LANCZOS 2x 放大; 无参考图用演示样本。始终真实 PNG (real=True)。"""
    resampler = getattr(getattr(Image, "Resampling", None), "LANCZOS", None) or Image.LANCZOS
    if image_path and os.path.exists(image_path):
        try:
            src = Image.open(image_path).convert("RGB")
            used = "provided"
        except Exception:
            src = _make_demo_canvas(label="Upscale 样本")
            used = "demo"
    else:
        src = _make_demo_canvas(label="Upscale 样本")
        used = "demo"
    W, H = src.size
    scale = 2
    out = src.resize((W * scale, H * scale), resampler)
    fn = "%s.upscale.png" % _slug(brief)
    path = os.path.join(_out_dir(out_dir), fn)
    out.save(path, "PNG")
    return {
        "domain": "image", "file": path, "mime": "image/png", "real": True,
        "note": "超分放大 (LANCZOS %dx%s)" % (scale, " 参考图" if used == "provided" else " 演示样本"),
        "meta": {"upscale": True, "scale": scale, "source": used,
                 "width": W * scale, "height": H * scale, "src_width": W, "src_height": H},
    }


def _render_image(brief, blueprint, ctx, out_dir, llm_call=None, mode="gen", image_path=None):
    """图片域三模式分发: gen(文生图) / inpaint(局部重绘) / upscale(超分放大)。"""
    if mode == "inpaint":
        return _render_image_inpaint(brief, blueprint, ctx, out_dir, llm_call, image_path=image_path)
    if mode == "upscale":
        return _render_image_upscale(brief, blueprint, ctx, out_dir, llm_call, image_path=image_path)
    return _render_image_gen(brief, blueprint, ctx, out_dir, llm_call)


# ----------------------------------------------------------------------------
# 音频域: LLM 提炼文稿 -> edge_tts 真实 MP3 (降级占位)
# ----------------------------------------------------------------------------

def _render_audio(brief, blueprint, ctx, out_dir, llm_call=None, mode="tts", voice="", rate="", pitch=""):
    if mode == "music":
        return _render_music(brief, blueprint, ctx, out_dir, llm_call)
    if mode == "clone":
        return _render_clone(brief, blueprint, ctx, out_dir, llm_call)
    designed = _design_audio_script(brief, blueprint, ctx, llm_call) if llm_call else None
    script = designed or _extract_script(blueprint or brief)
    fn = "%s.mp3" % _slug(brief)
    path = os.path.join(_out_dir(out_dir), fn)

    # 优先真实 TTS
    try:
        import asyncio
        import edge_tts
        voice = voice or "zh-CN-XiaoxiaoNeural"

        async def _speak(txt, out):
            comm = edge_tts.Communicate(txt, voice, rate=rate or "+0%", pitch=pitch or "+0Hz")
            await comm.save(out)

        asyncio.run(_speak(script, path))
        if os.path.exists(path) and os.path.getsize(path) > 100:
            dur = max(1, len(script) / 12.0)
            return {
                "domain": "audio", "file": path, "mime": "audio/mpeg", "real": True,
                "note": "edge_tts 真实语音合成 (zh-CN-XiaoxiaoNeural)" + (" [LLM 文稿]" if designed else ""),
                "meta": {"chars": len(script), "est_sec": round(dur, 1), "voice": voice,
                         "llm_scripted": bool(designed)},
            }
    except Exception:
        pass

    # 降级: 文字稿 + 声波占位图 (PNG)
    W, H = 1280, 360
    img = _grad_bg(W, H, (10, 30, 24), (16, 50, 40))
    d = ImageDraw.Draw(img)
    theme = _DOM_THEME["audio"]
    d.rectangle([0, 0, W, 8], fill=theme)
    f = _load_font(26)
    d.text((60, 50), "AUDIO · 音频资产 (降级占位)", font=f, fill=(167, 243, 208))
    mid = H // 2 + 40
    for x in range(80, W - 80, 4):
        amp = (math.sin(x / 18.0) * 0.5 + 0.5) * 70 * (0.5 + 0.5 * abs(math.sin(x / 60.0)))
        d.line([(x, mid - amp), (x, mid + amp)], fill=theme, width=2)
    f2 = _load_font(20)
    lines = [script[i:i + 38] for i in range(0, min(len(script), 190), 38)]
    yy = H - 90
    for ln in lines:
        d.text((60, yy), ln, font=f2, fill=(209, 250, 229))
        yy += 26
    png = path[:-4] + ".png"
    img.save(png, "PNG")
    return {
        "domain": "audio", "file": png, "mime": "image/png", "real": False,
        "note": "edge_tts 不可用 (缺依赖/无网络), 降级为文字稿+声波占位图; 接入 TTS API 后即真实 MP3"
                + (" [LLM 文稿]" if designed else ""),
        "meta": {"chars": len(script), "fallback": True, "llm_scripted": bool(designed)},
    }


# ----------------------------------------------------------------------------
# 音频域 · 本地配乐合成 (Phase 22): 零依赖 wave 合成真实可播放 WAV
# ----------------------------------------------------------------------------

def _render_music(brief, blueprint, ctx, out_dir, llm_call=None):
    """本地配乐合成 (零依赖): wave 合成 I-V-vi-IV 和弦进行 + 低频鼓点, 输出真实 WAV。

    与 TTS 不同 —— 不依赖 edge_tts / 网络 / key, 在任何环境都能产出可播放真实音频。
    brief/blueprint 可含 'bpm=120' / '小节=8' 调节节奏与长度, 失败用默认 (90bpm/4 小节)。
    """
    fn = "%s.wav" % _slug(brief)
    path = os.path.join(_out_dir(out_dir), fn)
    sr = 44100
    text = "%s %s" % (brief or "", blueprint or "")
    bpm, bars = 90, 4
    m = re.search(r"bpm[=: ]?(\d+)", text, re.I)
    if m:
        try:
            bpm = max(40, min(200, int(m.group(1))))
        except ValueError:
            pass
    m = re.search(r"(?:bars|小节)[=: ]?(\d+)", text, re.I)
    if m:
        try:
            bars = max(1, min(16, int(m.group(1))))
        except ValueError:
            pass
    beat = 60.0 / bpm
    chord_dur = beat * 2                      # 每个和弦持续 2 拍
    chords = [                                # C 大调 I-V-vi-IV
        [261.63, 329.63, 392.00],              # C  E  G
        [392.00, 493.88, 587.33],              # G  B  D
        [220.00, 261.63, 329.63],              # A  C  E (Am)
        [349.23, 440.00, 523.25],              # F  A  C
    ]
    seq = (chords * ((bars + 3) // 4))[:bars]
    total = int(sr * chord_dur * bars)
    frames = []

    def _s(freq, t):
        return math.sin(2 * math.pi * freq * t)

    for i in range(total):
        t = i / float(sr)
        bar = min(bars - 1, int(t // chord_dur))
        ch = seq[bar]
        env = 0.18
        s = sum(_s(f, t) for f in ch) / len(ch)
        bp = (t % beat) / beat
        kick = (math.sin(2 * math.pi * 60 * t) * max(0, 1 - bp * 6) * 0.5) if bp < 0.15 else 0
        v = max(-1.0, min(1.0, (s * env + kick) * 0.6))
        frames.append(struct.pack("<hh", int(v * 32767), int(v * 32767)))
    pcm = b"".join(frames)
    dur = round(total / float(sr), 1)
    try:
        # 手写 WAV (PCM 16bit): 绕开标准库 wave (本环境 wave.py 损坏)
        byte_rate = sr * 2 * 2
        hdr = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
               + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, sr, byte_rate, 4, 16)
               + b"data" + struct.pack("<I", len(pcm)))
        with open(path, "wb") as f:
            f.write(hdr)
            f.write(pcm)
    except Exception:
        return None
    return {
        "domain": "audio", "file": path, "mime": "audio/wav", "real": True,
        "note": "本地合成配乐 (零依赖 wave 合成, I-V-vi-IV 进行 + 节拍鼓点)",
        "meta": {"bpm": bpm, "bars": bars, "key": "C", "duration": dur,
                 "synth": "local", "voice": "synth"},
    }


def _render_clone(brief, blueprint, ctx, out_dir, llm_call=None):
    """语音克隆占位 (Phase 22): 需 reference_audio + 克隆 API (付费)。无 key 降级为声波占位图 + 结构说明。"""
    W, H = 1280, 360
    img = _grad_bg(W, H, (10, 30, 24), (16, 50, 40))
    d = ImageDraw.Draw(img)
    theme = _DOM_THEME["audio"]
    d.rectangle([0, 0, W, 8], fill=theme)
    f = _load_font(26)
    d.text((60, 50), "AUDIO · 语音克隆 (降级占位)", font=f, fill=(167, 243, 208))
    script = _extract_script(blueprint or brief)
    lines = [script[i:i + 38] for i in range(0, min(len(script), 152), 38)]
    yy = 120
    f2 = _load_font(20)
    for ln in lines:
        d.text((60, yy), ln, font=f2, fill=(209, 250, 229))
        yy += 26
    f3 = _load_font(18)
    d.text((60, H - 70), "接入真实克隆: 提供 reference_audio + 克隆服务商 (Azure Custom Voice / 国内 TTS 克隆)",
           font=f3, fill=(148, 163, 184))
    png = os.path.join(_out_dir(out_dir), "%s.clone.png" % _slug(brief))
    img.save(png, "PNG")
    return {
        "domain": "audio", "file": png, "mime": "image/png", "real": False,
        "note": "语音克隆需参考音频+克隆 API (降级为声波占位图+结构说明), 接入后即真实克隆语音",
        "meta": {"clone": True, "fallback": True},
    }


# ----------------------------------------------------------------------------
# 视频域: LLM 规划分镜 -> Pillow 真实 GIF 动图
# ----------------------------------------------------------------------------

def _render_video(brief, blueprint, ctx, out_dir, llm_call=None, mode="gen", image_path=None):
    """视频域三模式分发: gen(文生视频) / img2video(图生视频) / clips(剪辑配音合成)。

    每个模式优先尝试「远程真实 MP4 (密钥门控)」, 失败/无 key 自动回退本地 Pillow 真实 GIF 动图,
    始终 real=True 不崩, 与 image/audio 域降级链一致。
    """
    if mode == "img2video":
        return _render_video_img2video(brief, blueprint, ctx, out_dir, llm_call, image_path=image_path)
    if mode == "clips":
        return _render_video_clips(brief, blueprint, ctx, out_dir, llm_call, image_path=image_path)
    return _render_video_gen(brief, blueprint, ctx, out_dir, llm_call)


def _render_video_gen(brief, blueprint, ctx, out_dir, llm_call=None):
    """文生视频: 有视频生成 key 走远程真实 MP4; 否则本地 Pillow 真实 GIF 分镜动图。"""
    # 远程真实生成 (密钥门控, 失败自动回退本地)
    remote = _remote_text_to_video((brief or "")[:1000])
    if remote:
        fn = "%s.mp4" % _slug(brief)
        path = os.path.join(_out_dir(out_dir), fn)
        try:
            with open(path, "wb") as f:
                f.write(remote)
            if os.path.exists(path) and os.path.getsize(path) > 100:
                return {
                    "domain": "video", "file": path, "mime": "video/mp4", "real": True,
                    "note": "远程文生视频 API 真实生成 (有 key)",
                    "meta": {"gen": "remote", "width": 1024, "height": 1024},
                }
        except Exception:
            pass

    # 本地真实 GIF 分镜 (LLM 设计 / 模板回退)
    design = _design_video_shots(brief, blueprint, ctx, llm_call) if llm_call else None
    W, H = 960, 540
    theme = _DOM_THEME["video"]
    title = (design or {}).get("title") or (brief or "灵梦work 视频资产").split("\n")[0][:26]
    pts = (design or {}).get("shots") or _extract_points(blueprint or brief, 4)
    frames = []
    N = 28
    per = max(1, N // max(1, len(pts)))
    for i in range(N):
        t = i / (N - 1)
        top = (int(18 + 6 * t), 12, int(42 + 10 * t))
        bot = (int(31 + 8 * t), 17, int(71 + 12 * t))
        img = _grad_bg(W, H, top, bot)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 8], fill=theme)
        f_t = _load_font(40)
        f_s = _load_font(22)
        f_p = _load_font(24)
        dy = int(20 * (1 - t))
        d.text((60, 120 + dy), title, font=f_t, fill=(238, 238, 255))
        d.text((60, 180 + dy), "VIDEO · 视频资产 · 灵梦work", font=f_s, fill=(199, 196, 232))
        for k, p in enumerate(pts):
            appear = (k * per) / float(N)
            if t >= appear - 0.001:
                alpha = min(1.0, (t - appear) * 6 + 0.2)
                col = tuple(int(228 + (255 - 228) * alpha) for _ in range(3))
                d.text((90, 260 + k * 50), "▶ %s" % p, font=f_p, fill=col)
        d.rectangle([60, H - 50, 60 + int((W - 120) * t), H - 42], fill=theme)
        frames.append(img)
    fn = "%s.gif" % _slug(brief)
    path = os.path.join(_out_dir(out_dir), fn)
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=110, loop=0, optimize=False)
    return {
        "domain": "video", "file": path, "mime": "image/gif", "real": True,
        "note": "ffmpeg 缺失, 以 Pillow 真实 GIF 分镜动图交付视频资产 (接入文生视频/ffmpeg 后可升级 MP4)"
                + (" [LLM 分镜]" if design else ""),
        "meta": {"width": W, "height": H, "frames": N, "shots": len(pts),
                 "gen": "local", "llm_designed": bool(design)},
    }


def _render_video_img2video(brief, blueprint, ctx, out_dir, llm_call=None, image_path=None):
    """图生视频: 给定参考图做 Ken Burns 运动镜头 (缩放+平移) 合成真实 GIF 动图; 无参考图用演示画布。"""
    W, H = 960, 540
    base = None
    if image_path and os.path.exists(image_path):
        try:
            base = Image.open(image_path).convert("RGB").resize((W, H))
        except Exception:
            base = None
    if base is None:
        base = _make_demo_canvas(W, H, "IMG2VIDEO · 图生视频演示")
    frames = []
    N = 30
    has_ref = bool(image_path and os.path.exists(image_path))
    for i in range(N):
        t = i / (N - 1)
        scale = 1.0 + 0.18 * t
        dx = int(40 * math.sin(t * math.pi))     # 轻微左右平移
        dy = int(18 * (1 - t))                    # 轻微上下平移
        nw, nh = int(W * scale), int(H * scale)
        crop = base.resize((nw, nh))
        left = (nw - W) // 2 - dx
        top = (nh - H) // 2 - dy
        frame = crop.crop((max(0, left), max(0, top), max(0, left) + W, max(0, top) + H))
        d = ImageDraw.Draw(frame)
        d.rectangle([0, 0, W, 8], fill=_DOM_THEME["video"])
        f = _load_font(18)
        d.text((24, H - 36), "IMG2VIDEO · 图生视频 (Ken Burns)", font=f, fill=(199, 196, 232))
        frames.append(frame)
    fn = "%s.img2video.gif" % _slug(brief)
    path = os.path.join(_out_dir(out_dir), fn)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=90, loop=0)
    return {
        "domain": "video", "file": path, "mime": "image/gif", "real": True,
        "note": ("参考图 %s 的 Ken Burns 运动镜头 (真实 GIF)" % os.path.basename(image_path)) if has_ref
                else "无参考图, 演示画布 Ken Burns 运动镜头 (真实 GIF)",
        "meta": {"width": W, "height": H, "frames": N, "img2video": True,
                 "has_ref": has_ref},
    }


def _render_video_clips(brief, blueprint, ctx, out_dir, llm_call=None, image_path=None):
    """剪辑配音合成: 多参考图(逗号分隔)序列幻灯片 → 真实 GIF 动图; 无图用演示画布。

    注: 配音封装需 ffmpeg/视频 key, 本地降级为静帧 GIF 幻灯片 (可演示、可入库)。
    """
    W, H = 960, 540
    refs = []
    if image_path:
        for p in image_path.split(","):
            p = p.strip()
            if p and os.path.exists(p):
                try:
                    refs.append(Image.open(p).convert("RGB").resize((W, H)))
                except Exception:
                    pass
    if not refs:
        refs = [_make_demo_canvas(W, H, "CLIPS · 剪辑幻灯片演示")]
    design = _design_video_shots(brief, blueprint, ctx, llm_call) if llm_call else None
    title = (design or {}).get("title") or (brief or "灵梦work 视频资产").split("\n")[0][:26]
    per_img = 12
    frames = []
    for idx, img in enumerate(refs):
        for k in range(per_img):
            frame = img.copy()
            d = ImageDraw.Draw(frame)
            d.rectangle([0, 0, W, 8], fill=_DOM_THEME["video"])
            f_t = _load_font(34)
            f_s = _load_font(20)
            d.text((40, 40), title, font=f_t, fill=(238, 238, 255))
            d.text((40, 92), "CLIPS · 剪辑配音合成 #%d/%d" % (idx + 1, len(refs)),
                   font=f_s, fill=(199, 196, 232))
            frames.append(frame)
    fn = "%s.clips.gif" % _slug(brief)
    path = os.path.join(_out_dir(out_dir), fn)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=110, loop=0)
    return {
        "domain": "video", "file": path, "mime": "image/gif", "real": True,
        "note": "图片序列幻灯片合成 (clips 模式; 配音封装需 ffmpeg/视频 key, 本地降级静帧 GIF)",
        "meta": {"width": W, "height": H, "frames": len(frames), "clips": True,
                 "slides": len(refs)},
    }


# ----------------------------------------------------------------------------
# 统一分发
# ----------------------------------------------------------------------------

_ADAPTERS = {
    "image": _render_image,
    "audio": _render_audio,
    "video": _render_video,
}


def available_domains():
    return list(_ADAPTERS.keys())


def render(domain, brief, blueprint="", ctx="", out_dir=None, llm_call=None, mode="tts", voice="", rate="", pitch="", image_path=""):
    """为指定域真实产出媒体文件。返回 dict 或 None(不支持的域)。

    llm_call: 可选 llm_call(prompt, system=None)->str|None; 提供时三域先调 LLM 生成
              结构化设计再真实绘制; 为 None / LLM 失败时自动回退确定性模板。
    mode/voice/rate/pitch: 音频域模式与语音参数; mode/image_path: 图像域模式与参考图路径。
    """
    if domain == "audio":
        return _render_audio(brief or "", blueprint or "", ctx or "", out_dir,
                             llm_call, mode, voice, rate, pitch)
    if domain == "image":
        return _render_image(brief or "", blueprint or "", ctx or "", out_dir,
                             llm_call, mode=mode or "gen", image_path=image_path or None)
    if domain == "video":
        return _render_video(brief or "", blueprint or "", ctx or "", out_dir,
                             llm_call, mode=mode or "gen", image_path=image_path or None)
    fn = _ADAPTERS.get(domain)
    if not fn:
        return None
    try:
        return fn(brief or "", blueprint or "", ctx or "", out_dir, llm_call)
    except Exception as e:
        return {
            "domain": domain, "file": None, "mime": None, "real": False,
            "note": "适配失败: %s" % e, "meta": {}, "error": str(e),
        }

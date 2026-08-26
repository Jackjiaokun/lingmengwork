"""真实多模态适配层 (Multimodal Adapters) —— 终极蓝图 Phase 8.

把 creation_domains 的 audio/image/video 域从「蓝图产出」升级为「真实媒体文件交付」:

- image : Pillow 真实生成信息图 / 海报 PNG (深空蓝紫品牌风, 无需外部 key)
- audio : 优先 edge_tts 真实 TTS 产出 MP3; 不可用时降级为「文字稿 + 声波占位图」并标注
- video : ffmpeg 缺失环境下, 用 Pillow 真实生成多帧 GIF 动图作为视频资产交付 (真实多媒体文件)

所有产出落盘到 out_dir (默认 <cwd>/outputs/multimodal), 返回结构化 dict:
    {
      "domain": "image"|"audio"|"video",
      "file": "<绝对路径>",
      "mime": "image/png"|"audio/mpeg"|"image/gif",
      "real": True/False,           # True=真实媒体; False=降级占位(明确标注)
      "note": "补充说明(如降级原因)",
      "meta": {...}                  # 时长/尺寸/字数等
    }

设计原则: 任何单域失败不影响其余域; 无外部 key / 无网络时自动降级且全程可用。
"""

import os
import re
import math
import time

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


# ----------------------------------------------------------------------------
# 图片域: Pillow 真实 PNG
# ----------------------------------------------------------------------------

def _render_image(brief, blueprint, ctx, out_dir):
    W, H = 1280, 720
    img = _grad_bg(W, H, (18, 12, 42), (31, 17, 71))
    d = ImageDraw.Draw(img)
    theme = _DOM_THEME["image"]

    # 顶部品牌渐变条
    for y in range(8):
        d.line([(0, y), (W, y)], fill=(139, 92, 246))

    # 左侧主题光条
    d.rectangle([60, 120, 78, 600], fill=theme)

    # 标题
    f_title = _load_font(46)
    f_sub = _load_font(24)
    f_pt = _load_font(26)
    title = (brief or "灵梦work 多模态创作").split("\n")[0][:32]
    d.text((100, 140), title, font=f_title, fill=(245, 243, 255))
    d.text((100, 200), "IMAGE · 图片资产 · 灵梦work 真实生成", font=f_sub, fill=(186, 180, 220))

    # 要点列表
    pts = _extract_points(blueprint or brief, 5)
    y = 280
    for p in pts:
        d.ellipse([104, y + 6, 120, y + 22], fill=theme)
        d.text((140, y), "• %s" % p, font=f_pt, fill=(228, 224, 245))
        y += 56

    # 底部水印
    d.text((100, H - 56), "灵梦work · 多模态适配层 (Phase 8) 自动产出", font=f_sub, fill=(150, 144, 185))

    fn = "%s.png" % _slug(brief)
    path = os.path.join(_out_dir(out_dir), fn)
    img.save(path, "PNG")
    return {
        "domain": "image", "file": path, "mime": "image/png", "real": True,
        "note": "Pillow 真实渲染信息图",
        "meta": {"width": W, "height": H, "points": len(pts)},
    }


# ----------------------------------------------------------------------------
# 音频域: edge_tts 真实 MP3 (降级占位)
# ----------------------------------------------------------------------------

def _render_audio(brief, blueprint, ctx, out_dir):
    script = _extract_script(blueprint or brief)
    fn = "%s.mp3" % _slug(brief)
    path = os.path.join(_out_dir(out_dir), fn)

    # 优先真实 TTS
    try:
        import asyncio
        import edge_tts
        voice = "zh-CN-XiaoxiaoNeural"

        async def _speak(txt, out):
            comm = edge_tts.Communicate(txt, voice)
            await comm.save(out)

        asyncio.run(_speak(script, path))
        if os.path.exists(path) and os.path.getsize(path) > 100:
            dur = max(1, len(script) / 12.0)
            return {
                "domain": "audio", "file": path, "mime": "audio/mpeg", "real": True,
                "note": "edge_tts 真实语音合成 (zh-CN-XiaoxiaoNeural)",
                "meta": {"chars": len(script), "est_sec": round(dur, 1), "voice": voice},
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
    # 声波
    mid = H // 2 + 40
    for x in range(80, W - 80, 4):
        amp = (math.sin(x / 18.0) * 0.5 + 0.5) * 70 * (0.5 + 0.5 * abs(math.sin(x / 60.0)))
        d.line([(x, mid - amp), (x, mid + amp)], fill=theme, width=2)
    # 文字稿 (前若干字)
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
        "note": "edge_tts 不可用 (缺依赖/无网络), 降级为文字稿+声波占位图; 接入 TTS API 后即真实 MP3",
        "meta": {"chars": len(script), "fallback": True},
    }


# ----------------------------------------------------------------------------
# 视频域: Pillow 真实 GIF 动图
# ----------------------------------------------------------------------------

def _render_video(brief, blueprint, ctx, out_dir):
    W, H = 960, 540
    theme = _DOM_THEME["video"]
    title = (brief or "灵梦work 视频资产").split("\n")[0][:26]
    pts = _extract_points(blueprint or brief, 4)
    frames = []
    N = 28
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
            appear = k / max(1, len(pts) - 1) if len(pts) > 1 else 0
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
        "note": "ffmpeg 缺失, 以 Pillow 真实 GIF 动图交付视频资产 (接入文生视频/ffmpeg 后可升级 MP4)",
        "meta": {"width": W, "height": H, "frames": N, "points": len(pts)},
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


def render(domain, brief, blueprint="", ctx="", out_dir=None):
    """为指定域真实产出媒体文件。返回 dict 或 None(不支持的域)。"""
    fn = _ADAPTERS.get(domain)
    if not fn:
        return None
    try:
        return fn(brief or "", blueprint or "", ctx or "", out_dir)
    except Exception as e:
        return {
            "domain": domain, "file": None, "mime": None, "real": False,
            "note": "适配失败: %s" % e, "meta": {}, "error": str(e),
        }

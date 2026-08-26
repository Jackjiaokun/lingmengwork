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
# 图片域: LLM 设计 -> Pillow 真实 PNG
# ----------------------------------------------------------------------------

def _render_image(brief, blueprint, ctx, out_dir, llm_call=None):
    design = _design_image(brief, blueprint, ctx, llm_call) if llm_call else None
    pal = (design or {}).get("palette") or "nebula"
    top, bottom, theme = _PALETTES.get(pal, _PALETTES["nebula"])
    W, H = 1280, 720
    img = _grad_bg(W, H, top, bottom)
    d = ImageDraw.Draw(img)

    # 顶部品牌渐变条
    for y in range(8):
        d.line([(0, y), (W, y)], fill=theme)

    # 左侧主题光条
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
        "meta": {"width": W, "height": H, "points": len(pts),
                 "llm_designed": bool(design), "palette": pal},
    }


# ----------------------------------------------------------------------------
# 音频域: LLM 提炼文稿 -> edge_tts 真实 MP3 (降级占位)
# ----------------------------------------------------------------------------

def _render_audio(brief, blueprint, ctx, out_dir, llm_call=None):
    designed = _design_audio_script(brief, blueprint, ctx, llm_call) if llm_call else None
    script = designed or _extract_script(blueprint or brief)
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
# 视频域: LLM 规划分镜 -> Pillow 真实 GIF 动图
# ----------------------------------------------------------------------------

def _render_video(brief, blueprint, ctx, out_dir, llm_call=None):
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
        "note": "ffmpeg 缺失, 以 Pillow 真实 GIF 动图交付视频资产 (接入文生视频/ffmpeg 后可升级 MP4)"
                + (" [LLM 分镜]" if design else ""),
        "meta": {"width": W, "height": H, "frames": N, "shots": len(pts),
                 "llm_designed": bool(design)},
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


def render(domain, brief, blueprint="", ctx="", out_dir=None, llm_call=None):
    """为指定域真实产出媒体文件。返回 dict 或 None(不支持的域)。

    llm_call: 可选 llm_call(prompt, system=None)->str|None; 提供时三域先调 LLM 生成
              结构化设计再真实绘制; 为 None / LLM 失败时自动回退确定性模板。
    """
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

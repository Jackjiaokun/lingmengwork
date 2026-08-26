"""Phase 12 测试: 多模态适配层 LLM 智能设计 + 容错回退。

不触发任何真实 LLM / 网络: 用确定性 fake_llm, audio 降级路径用 sys.modules 禁掉 edge_tts。
"""

import os
import sys
import tempfile

from lingmengwork import multimodal_adapters as ma


# ---------------------------------------------------------------------------
# 确定性 fake LLM
# ---------------------------------------------------------------------------

def _fake_image_llm(prompt, system=None):
    # 仅当 system 含「信息图设计师」时返回设计 JSON
    if "信息图设计师" in (system or ""):
        return ('```json\n'
                '{"title":"定制标题","subtitle":"副标题文案","points":["要点一","要点二","要点三"],'
                '"palette":"ocean","style":"minimal"}\n'
                '```')
    return ""


def _fake_bad_llm(prompt, system=None):
    return "这明显不是 json 的乱码文本 abc123"


def _fake_video_llm(prompt, system=None):
    if "分镜师" in (system or ""):
        return '{"title":"短片标题","shots":["开场","核心论点","案例","收尾"]}'
    return ""


def _fake_audio_llm(prompt, system=None):
    if "播报文稿编辑" in (system or ""):
        return "这是一段由语言模型提炼的连贯朗读文稿。"
    return ""


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------

def test_available_domains():
    assert ma.available_domains() == ["image", "audio", "video"]


def test_unknown_domain_returns_none():
    assert ma.render("not_a_domain", "x") is None


# ---------------------------------------------------------------------------
# 图片域
# ---------------------------------------------------------------------------

def test_image_no_llm_template_fallback():
    out = tempfile.mkdtemp()
    art = ma.render("image", "测试主题卡片", "蓝图内容", "", out_dir=out)
    assert art["domain"] == "image"
    assert art["mime"] == "image/png"
    assert art["real"] is True
    assert os.path.exists(art["file"])
    assert art["meta"]["llm_designed"] is False
    assert art["meta"]["palette"] == "nebula"


def test_image_llm_design_applied():
    out = tempfile.mkdtemp()
    art = ma.render("image", "测试主题", "蓝图", "", out_dir=out, llm_call=_fake_image_llm)
    assert art["real"] is True
    assert art["mime"] == "image/png"
    assert os.path.exists(art["file"])
    assert art["meta"]["llm_designed"] is True
    assert art["meta"]["palette"] == "ocean"


def test_image_bad_json_falls_back():
    out = tempfile.mkdtemp()
    art = ma.render("image", "主题", "蓝图", "", out_dir=out, llm_call=_fake_bad_llm)
    assert art["real"] is True
    assert art["meta"]["llm_designed"] is False
    assert art["meta"]["palette"] == "nebula"


# ---------------------------------------------------------------------------
# 视频域
# ---------------------------------------------------------------------------

def test_video_gif_no_llm():
    out = tempfile.mkdtemp()
    art = ma.render("video", "演示视频", "蓝图", "", out_dir=out)
    assert art["domain"] == "video"
    assert art["mime"] == "image/gif"
    assert art["real"] is True
    assert os.path.exists(art["file"])
    assert art["meta"]["llm_designed"] is False


def test_video_llm_shots_applied():
    out = tempfile.mkdtemp()
    art = ma.render("video", "主题", "蓝图", "", out_dir=out, llm_call=_fake_video_llm)
    assert art["real"] is True
    assert art["meta"]["llm_designed"] is True
    assert art["meta"]["shots"] == 4


# ---------------------------------------------------------------------------
# 音频域 (强制禁用 edge_tts, 走降级占位, 避免网络依赖)
# ---------------------------------------------------------------------------

def test_audio_fallback_structure():
    saved = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = None  # 强制 import edge_tts 失败 -> 降级
    try:
        out = tempfile.mkdtemp()
        art = ma.render("audio", "测试音频主题", "蓝图内容")
        assert art["domain"] == "audio"
        assert set(["file", "mime", "real", "note", "meta"]).issubset(art.keys())
        assert art["meta"]["fallback"] is True
        assert os.path.exists(art["file"])
    finally:
        if saved is None:
            sys.modules.pop("edge_tts", None)
        else:
            sys.modules["edge_tts"] = saved


def test_audio_llm_scripted_flag():
    saved = sys.modules.get("edge_tts")
    sys.modules["edge_tts"] = None
    try:
        out = tempfile.mkdtemp()
        art = ma.render("audio", "主题", "蓝图", "", out_dir=out, llm_call=_fake_audio_llm)
        assert art["meta"]["llm_scripted"] is True
        assert "[LLM 文稿]" in art["note"]
    finally:
        if saved is None:
            sys.modules.pop("edge_tts", None)
        else:
            sys.modules["edge_tts"] = saved


# ---------------------------------------------------------------------------
# 解析器容错
# ---------------------------------------------------------------------------

def test_parse_json_block_fenced_and_noisy():
    raw = "好的, 这是设计:\n```json\n{\"a\": 1}\n```\n以上。"
    assert ma._parse_json_block(raw) == {"a": 1}
    noisy = "前言后语 { \"b\": 2 } 结尾"
    assert ma._parse_json_block(noisy) == {"b": 2}
    assert ma._parse_json_block("完全无关") is None

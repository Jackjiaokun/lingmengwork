"""灵梦work (LingMeng Work) — 一款次时代 AI 全能工具，强大的编程 · 音频 · 图片 · 视频 全面 AI Agent 能力。

双模: CLI 内核 (lingmengwork) + Web 控制台 (lingmengwork web)。
LLM 后端可切: 本地 Ollama / 云端 OpenAI 兼容 / Mock 离线。
"""
import os
import sys
from pathlib import Path

_candidates = [
    Path(__file__).resolve().parent.parent / "VERSION",  # 源码/包内
    Path(__file__).resolve().parent / "VERSION",          # 同级
]
if getattr(sys, "frozen", False):
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        _candidates.append(Path(_meipass) / "VERSION")
        _candidates.append(Path(_meipass) / "lingmengwork" / "VERSION")

__version__ = "0.0.0"
for _p in _candidates:
    if _p.exists():
        __version__ = _p.read_text(encoding="utf-8").strip()
        break

__all__ = ["__version__"]

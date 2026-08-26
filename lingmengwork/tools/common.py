"""工具系统公共件: 错误类型 + 路径越界防护。"""
import os
from pathlib import Path


class ToolError(Exception):
    """工具执行失败 (会被回灌给模型, 让其自我修复, 而非崩溃)。"""


def resolve_path(roots, path):
    """把 path 解析为绝对路径, 并强制落在 allowed_roots 内。

    相对路径以 allowed_roots[0] (即启动目录) 为基准解析。
    roots 与解析结果均经 normpath 归一 (展开 Windows 8.3 短路径如 ADMINI~1),
    否则 short/long 混用会导致前缀判定失效而误报越界。
    """
    if not roots:
        raise ToolError("未配置允许根目录 allowed_roots。")
    norm_roots = [Path(str(r)).resolve() for r in roots]
    p = Path(path)
    if not p.is_absolute():
        p = (norm_roots[0] / p).resolve()
    p = Path(str(p.resolve()))
    for r in norm_roots:
        try:
            p.relative_to(r)
            return p
        except Exception:
            continue
    raise ToolError(f"路径越界, 不允许操作允许根目录之外的路径: {path}")

"""工具系统公共件: 错误类型 + 路径越界防护。"""
from pathlib import Path


class ToolError(Exception):
    """工具执行失败 (会被回灌给模型, 让其自我修复, 而非崩溃)。"""


def resolve_path(roots, path):
    """把 path 解析为绝对路径, 并强制落在 allowed_roots 内。

    相对路径以 allowed_roots[0] (即启动目录) 为基准解析。
    """
    if not roots:
        raise ToolError("未配置允许根目录 allowed_roots。")
    p = Path(path)
    if not p.is_absolute():
        p = (roots[0] / p).resolve()
    p = p.resolve()
    for r in roots:
        try:
            p.relative_to(r)
            return p
        except Exception:
            continue
    raise ToolError(f"路径越界, 不允许操作允许根目录之外的路径: {path}")

"""文件工具函数"""

import os
from pathlib import Path
from typing import Iterator


def read_text(path: str | Path, encoding: str = "utf-8") -> str:
    """读取文本文件内容。"""
    with open(path, "r", encoding=encoding) as f:
        return f.read()


def write_text(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """写入文本文件（自动创建父目录）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding=encoding) as f:
        f.write(content)


def file_size(path: str | Path) -> int:
    """返回文件大小（字节）。"""
    return Path(path).stat().st_size


def find_files(
    root: str | Path,
    pattern: str = "*",
    recursive: bool = True,
) -> list[Path]:
    """在目录中查找匹配 glob pattern 的文件。"""
    root_path = Path(root)
    if not root_path.is_dir():
        raise NotADirectoryError(f"不是目录: {root_path}")
    method = root_path.rglob if recursive else root_path.glob
    return sorted(p for p in method(pattern) if p.is_file())


def safe_join(base: str | Path, *parts: str) -> Path:
    """安全拼接路径，防止路径穿越（.. 会被丢弃）。"""
    base_path = Path(base).resolve()
    result = base_path
    for part in parts:
        candidate = (result / part).resolve()
        # 确保拼接后仍在 base 内
        try:
            candidate.relative_to(base_path)
        except ValueError:
            raise ValueError(f"路径穿越检测失败: {part}")
        result = candidate
    return result
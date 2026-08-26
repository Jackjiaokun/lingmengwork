"""字符串工具函数"""

import re


def truncate(text: str, max_length: int, suffix: str = "...") -> str:
    """截断字符串到指定长度，超出部分追加 suffix。"""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def slugify(text: str) -> str:
    """将文本转为 URL 友好的 slug（小写、空格替换为连字符、移除特殊字符）。"""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "-", text)
    return text


def word_wrap(text: str, width: int = 80) -> str:
    """按指定宽度自动换行（不拆分单词）。"""
    if width <= 0:
        raise ValueError("width 必须大于 0")
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current_line = ""
        for word in paragraph.split():
            if len(current_line) + len(word) + (1 if current_line else 0) <= width:
                current_line = (current_line + " " + word).strip()
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
    return "\n".join(lines)


def is_palindrome(text: str) -> bool:
    """判断是否为回文（忽略大小写和非字母数字字符）。"""
    cleaned = re.sub(r"[^\w]", "", text).lower()
    return cleaned == cleaned[::-1]


def count_words(text: str) -> int:
    """统计单词数（按空白分割）。"""
    return len(text.split())


def title_case(text: str) -> str:
    """转为标题格式（每个单词首字母大写）。"""
    return text.title()
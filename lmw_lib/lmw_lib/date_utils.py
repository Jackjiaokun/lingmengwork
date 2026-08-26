"""日期时间工具函数"""

from datetime import datetime, timedelta


def format_timestamp(ts: float | None = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """将时间戳格式化为字符串。ts 为 None 时使用当前时间。"""
    dt = datetime.fromtimestamp(ts) if ts is not None else datetime.now()
    return dt.strftime(fmt)


def parse_date(text: str, fmt: str = "%Y-%m-%d") -> datetime:
    """按指定格式解析日期字符串。"""
    return datetime.strptime(text, fmt)


def days_between(start: str, end: str, fmt: str = "%Y-%m-%d") -> int:
    """计算两个日期之间的天数差。"""
    d1 = parse_date(start, fmt)
    d2 = parse_date(end, fmt)
    return abs((d2 - d1).days)


def add_days(date_str: str, days: int, fmt: str = "%Y-%m-%d") -> str:
    """在日期上增加指定天数，返回格式化字符串。"""
    dt = parse_date(date_str, fmt) + timedelta(days=days)
    return dt.strftime(fmt)


def is_weekend(date_str: str, fmt: str = "%Y-%m-%d") -> bool:
    """判断日期是否为周末（周六或周日）。"""
    dt = parse_date(date_str, fmt)
    return dt.weekday() >= 5  # 5=周六, 6=周日
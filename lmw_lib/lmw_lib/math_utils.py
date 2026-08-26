"""数学工具函数"""

import math
from typing import Iterable


def clamp(value: float, min_val: float, max_val: float) -> float:
    """将值限制在 [min_val, max_val] 范围内。"""
    return max(min_val, min(value, max_val))


def lerp(start: float, end: float, t: float) -> float:
    """线性插值：t=0 返回 start，t=1 返回 end。"""
    return start + (end - start) * t


def mean(values: Iterable[float]) -> float:
    """计算算术平均值。"""
    vals = list(values)
    if not vals:
        raise ValueError("mean() 需要非空序列")
    return sum(vals) / len(vals)


def median(values: Iterable[float]) -> float:
    """计算中位数。"""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 0:
        raise ValueError("median() 需要非空序列")
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    return sorted_vals[mid]


def stddev(values: Iterable[float]) -> float:
    """计算总体标准差。"""
    vals = list(values)
    if len(vals) < 2:
        raise ValueError("stddev() 至少需要 2 个值")
    m = mean(vals)
    variance = sum((x - m) ** 2 for x in vals) / len(vals)
    return math.sqrt(variance)


def normalize(values: Iterable[float]) -> list[float]:
    """将序列归一化到 [0, 1] 范围。"""
    vals = list(values)
    if not vals:
        return []
    min_v, max_v = min(vals), max(vals)
    if max_v == min_v:
        return [0.0] * len(vals)
    return [(v - min_v) / (max_v - min_v) for v in vals]


def to_degrees(radians: float) -> float:
    """弧度转角度。"""
    return math.degrees(radians)


def to_radians(degrees: float) -> float:
    """角度转弧度。"""
    return math.radians(degrees)
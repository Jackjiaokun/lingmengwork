"""math_utils 测试"""

import math
import pytest
from lmw_lib.math_utils import (
    clamp, lerp, mean, median, stddev, normalize, to_degrees, to_radians,
)


class TestClamp:
    def test_within_range(self):
        assert clamp(5, 0, 10) == 5

    def test_below_min(self):
        assert clamp(-1, 0, 10) == 0

    def test_above_max(self):
        assert clamp(15, 0, 10) == 10


class TestLerp:
    def test_t_zero(self):
        assert lerp(0, 10, 0) == 0

    def test_t_one(self):
        assert lerp(0, 10, 1) == 10

    def test_t_half(self):
        assert lerp(0, 10, 0.5) == 5


class TestMean:
    def test_basic(self):
        assert mean([1, 2, 3, 4, 5]) == 3.0

    def test_single(self):
        assert mean([42]) == 42.0

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            mean([])


class TestMedian:
    def test_odd(self):
        assert median([3, 1, 2]) == 2

    def test_even(self):
        assert median([1, 2, 3, 4]) == 2.5

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            median([])


class TestStddev:
    def test_basic(self):
        assert abs(stddev([2, 4, 4, 4, 5, 5, 7, 9]) - 2.0) < 1e-9

    def test_too_few_raises(self):
        with pytest.raises(ValueError):
            stddev([1])


class TestNormalize:
    def test_basic(self):
        result = normalize([0, 5, 10])
        assert result[0] == 0.0
        assert result[2] == 1.0

    def test_all_same(self):
        assert normalize([3, 3, 3]) == [0.0, 0.0, 0.0]

    def test_empty(self):
        assert normalize([]) == []


class TestAngleConversion:
    def test_radians_to_degrees(self):
        assert abs(to_degrees(math.pi) - 180.0) < 1e-9

    def test_degrees_to_radians(self):
        assert abs(to_radians(180) - math.pi) < 1e-9
"""date_utils 测试"""

import pytest
from datetime import datetime
from lmw_lib.date_utils import (
    format_timestamp, parse_date, days_between, add_days, is_weekend,
)


class TestFormatTimestamp:
    def test_custom(self):
        result = format_timestamp(0, "%Y-%m-%d")
        assert result == "1970-01-01"

    def test_now(self):
        result = format_timestamp()
        assert len(result) > 0


class TestParseDate:
    def test_basic(self):
        dt = parse_date("2024-01-15")
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15


class TestDaysBetween:
    def test_basic(self):
        assert days_between("2024-01-01", "2024-01-15") == 14

    def test_reversed(self):
        assert days_between("2024-01-15", "2024-01-01") == 14


class TestAddDays:
    def test_positive(self):
        assert add_days("2024-01-01", 10) == "2024-01-11"

    def test_negative(self):
        assert add_days("2024-01-15", -5) == "2024-01-10"


class TestIsWeekend:
    def test_saturday(self):
        # 2024-01-13 是周六
        assert is_weekend("2024-01-13") is True

    def test_sunday(self):
        # 2024-01-14 是周日
        assert is_weekend("2024-01-14") is True

    def test_weekday(self):
        # 2024-01-15 是周一
        assert is_weekend("2024-01-15") is False
"""string_utils 测试"""

import pytest
from lmw_lib.string_utils import (
    truncate, slugify, word_wrap, is_palindrome, count_words, title_case,
)


class TestTruncate:
    def test_no_truncation(self):
        assert truncate("hello", 10) == "hello"

    def test_truncates(self):
        assert truncate("hello world", 8) == "hello..."

    def test_custom_suffix(self):
        assert truncate("hello world", 8, "~") == "hello w~"


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_removes_special(self):
        assert slugify("foo & bar!") == "foo--bar"

    def test_collapse_spaces(self):
        assert slugify("a  b   c") == "a-b-c"


class TestWordWrap:
    def test_no_wrap_needed(self):
        assert word_wrap("hello", 80) == "hello"

    def test_wraps(self):
        result = word_wrap("one two three four", 10)
        assert "\n" in result

    def test_width_zero_raises(self):
        with pytest.raises(ValueError):
            word_wrap("hello", 0)


class TestIsPalindrome:
    def test_yes(self):
        assert is_palindrome("racecar") is True

    def test_no(self):
        assert is_palindrome("hello") is False

    def test_with_spaces(self):
        assert is_palindrome("A man a plan a canal Panama") is True


class TestCountWords:
    def test_basic(self):
        assert count_words("hello world foo") == 3

    def test_empty(self):
        assert count_words("") == 0


class TestTitleCase:
    def test_basic(self):
        assert title_case("hello world") == "Hello World"
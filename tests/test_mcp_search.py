"""search MCP 服务器单测 (mock urllib, 不真联网)。"""
from unittest import mock

import pytest

from lingmengwork.tools import mcp_search_server as m

_HTML = (
    '<a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Example A</a>'
    '<a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.org%2Fb">Example B</a>'
)


class _FakeResp:
    def __init__(self, body):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_web_search_parse():
    with mock.patch.object(m.urllib.request, "urlopen", return_value=_FakeResp(_HTML.encode())):
        out = m._web_search({"query": "test", "max_results": 5})
    assert "Example A" in out
    assert "example.com" in out
    assert "Example B" in out
    assert "找到" in out


def test_web_search_missing_query():
    assert "缺少 query" in m._web_search({})


def test_web_search_failure_fallback():
    # DDG 抛错 -> 回退 Bing 也抛错 -> 返回失败串 (不崩溃)
    with mock.patch.object(m.urllib.request, "urlopen", side_effect=Exception("net down")):
        out = m._web_search({"query": "x"})
    assert ("失败" in out) or ("网络错误" in out)

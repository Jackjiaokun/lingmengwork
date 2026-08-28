# -*- coding: utf-8 -*-
"""Phase 72 · 编排历史分页测试."""

import os
import re

from lingmengwork.web import server as _srv

STATIC = os.path.join(os.path.dirname(_srv.__file__), "static")


def _js():
    with open(os.path.join(STATIC, "superagent.html"), encoding="utf-8") as f:
        return f.read()


def test_pagination_ui_tokens():
    js = _js()
    for tok in ('id="runPager"', "RUN_PAGE_SIZE", "runPageGo", "runLastKw",
                "上一页", "下一页", "runPage = Math.max(1, runPage + d)"):
        assert tok in js, "缺: " + tok


def test_pagination_logic_simulation():
    """用 Python 模拟 JS 分页逻辑验证边界: 翻页/搜索重置/末页收敛."""
    RUN_PAGE_SIZE = 50
    runs = list(range(123))          # 123 条 -> 3 页
    runPage, runLastKw = 1, ""

    def render(kw):
        nonlocal runPage, runLastKw
        if kw != runLastKw:
            runPage, runLastKw = 1, kw
        filtered = runs if not kw else [x for x in runs if str(x).find(kw) >= 0]
        pages = max(1, -(-len(filtered) // RUN_PAGE_SIZE))
        runPage = min(runPage, pages)
        return filtered[(runPage-1)*RUN_PAGE_SIZE: runPage*RUN_PAGE_SIZE], pages, len(filtered)

    page, pages, total = render("")
    assert (runPage, pages, total) == (1, 3, 123)
    assert page == list(range(50)), "第1页=前50条"

    runPage = 3
    page, pages, _ = render("")
    assert page == list(range(100, 123)), "第3页=尾23条"

    runPage = 9                       # 越界 -> 收敛到末页
    page, pages, _ = render("")
    assert runPage == 3 and page[0] == 100

    page, pages, total = render("12")  # 搜索 -> 页码重置
    assert runPage == 1 and total == 5

    runPage = 5                       # 搜索结果不足一页
    page, pages, _ = render("12")
    assert pages == 1 and runPage == 1 and len(page) == 5


def test_pagination_resets_on_search():
    js = _js()
    # 搜索变化必须重置页码
    assert "if(kw !== runLastKw){ runPage = 1; runLastKw = kw; }" in js
    # 单页时隐藏分页条
    assert "if(pages <= 1){ pager.innerHTML" in js

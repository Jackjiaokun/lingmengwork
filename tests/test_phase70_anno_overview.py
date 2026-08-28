# -*- coding: utf-8 -*-
"""Phase 70 · 批注总览测试."""

import os

from lingmengwork.web import server as _srv

STATIC = os.path.join(os.path.dirname(_srv.__file__), "static")


def test_overview_ui_tokens():
    with open(os.path.join(STATIC, "superagent.html"), encoding="utf-8") as f:
        html = f.read()
    for tok in ("批注总览", "annoAllBox", "annoAllFilter", "loadAllAnnos",
                "delAllAnno", "/api/superagent/annotations\"",
                "loadRunDetail", "rated", "low", "unrated"):
        assert tok in html, "缺: " + tok


def test_overview_init_and_filters():
    with open(os.path.join(STATIC, "superagent.html"), encoding="utf-8") as f:
        html = f.read()
    # 初始化调用 + 过滤逻辑三态
    assert "loadAllAnnos();" in html
    assert 'f==="rated"' in html and 'f==="low"' in html and 'f==="unrated"' in html
    # 低分过滤含 <=2 语义
    assert "a.rating<=2" in html
    # 点击时间回看编排
    assert "loadRunDetail(" in html


def test_overview_all_annotations_api_supports_no_ts():
    """内核 list_annotations 无 ts 应返回全部(总览数据源)。"""
    from lingmengwork import superagent as sa_mod
    orig = sa_mod._ANNOS
    sa_mod._ANNOS = {
        "a_1": {"id": "a_1", "ts": "t1", "text": "x", "created_at": "2026-08-29 01:00:00"},
        "a_2": {"id": "a_2", "ts": "t2", "text": "y", "created_at": "2026-08-29 02:00:00"},
        sa_mod._ANNOS_SEQ_KEY: {"id": sa_mod._ANNOS_SEQ_KEY, "seq": 2},
    }
    try:
        all_annos = sa_mod.list_annotations(None)  # 无 ts -> 全部
        assert len(all_annos) == 2, "无 ts 应返回全部(排除序号键)"
        only_t1 = sa_mod.list_annotations("t1")
        assert len(only_t1) == 1 and only_t1[0]["id"] == "a_1"
    finally:
        sa_mod._ANNOS = orig

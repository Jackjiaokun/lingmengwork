# -*- coding: utf-8 -*-
"""Phase 71 · 编排历史搜索 + 成本看板质量基线测试."""

import os

from lingmengwork.web import server as _srv

STATIC = os.path.join(os.path.dirname(_srv.__file__), "static")


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def test_run_search_ui():
    html = _read("superagent.html")
    for tok in ('id="runSearch"', 'oninput="renderRuns()"', 'id="runCount"',
                "无匹配编排", "条命中"):
        assert tok in html, "缺: " + tok


def test_run_search_filters_goal_ts_routed():
    html = _read("superagent.html")
    # 过滤维度: goal / ts / routed 三者
    for dim in ('(x.goal||"").toLowerCase().includes(kw)',
                '(x.ts||"").includes(kw)',
                '(x.routed||[]).join("/").toLowerCase().includes(kw)'):
        assert dim in html, "缺过滤维度: " + dim
    # KPI 在过滤前计算(全量统计) — kRuns 赋值应在 kw 计算之前
    assert html.index('kRuns").textContent') < html.index('const kw =')


def test_cost_quality_section():
    html = _read("cost.html")
    for tok in ("编排质量基线", "q-runs", "q-score", "q-elapsed", "q-alerts",
                "q-goals", "loadQualityBase",
                "/api/superagent/quality/baseline", "/api/superagent/quality/alerts"):
        assert tok in html, "cost 页缺: " + tok
    # 位置: 质量基线在预算护栏之后、价目参考之前
    assert html.index('>🧠 编排质量基线') > html.index('>🛡️ 预算护栏')
    assert html.index('>🧠 编排质量基线') < html.index('>价目参考 (元')


def test_cost_quality_refresh_wired():
    html = _read("cost.html")
    # start() 应同时刷新 loadBudget 与 loadQualityBase
    seg = html.split("function start()")[1].split("}")[0]
    assert "loadBudget()" in seg and "loadQualityBase()" in seg
    # 5s 轮询也应带质量刷新
    assert "loadQualityBase()" in html.split("setInterval")[1]

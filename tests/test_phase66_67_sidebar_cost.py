# -*- coding: utf-8 -*-
"""Phase 66/67 · 侧栏入口 + 成本看板预算护栏测试."""

import os

from lingmengwork.web import server as _srv

STATIC = os.path.join(os.path.dirname(_srv.__file__), "static")


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def test_index_sidebar_has_new_entries():
    html = _read("index.html")
    for tok in ('href="/superagent"', "超级 AGENT 工作台",
                'href="/memory-graph"', "记忆图谱",
                'href="/plugins"', "插件中枢"):
        assert tok in html, "index 侧栏缺: " + tok
    # 工作台应在「智能工作区」分组内且是第一个入口
    g1 = html[html.index('data-group="workspace"'):html.index('data-group="security"')]
    assert 'href="/superagent"' in g1
    assert g1.index('href="/superagent"') < g1.index('href="/notes"'), "工作台应置顶"


def test_cost_page_has_budget_section():
    html = _read("cost.html")
    for tok in ("预算护栏", "b-today", "b-limit", "b-bar", "b-input",
                "saveBudget", "loadBudget", "/api/superagent/budget", "次日自动恢复"):
        assert tok in html, "cost 页缺: " + tok
    # 预算区应在编排用量之后、价目参考之前
    assert html.index("预算护栏") < html.index("价目参考")
    assert html.index("预算护栏") > html.index("超级 AGENT 编排用量")


def test_cost_budget_js_wiring():
    html = _read("cost.html")
    # 自动刷新应同时刷新预算
    assert "loadBudget();" in html.split("function start()")[1].split("}")[0] or \
           "load()" in html and "loadBudget()" in html
    # POST body 带 daily_limit
    assert "daily_limit" in html
    # 保存时输入框未被占用才回填
    assert "activeElement" in html

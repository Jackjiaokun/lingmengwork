# -*- coding: utf-8 -*-
"""Phase 69 · 导航路由一致性测试（防死链）.

后端页面路由(server.py 中 _serve_file 的 if p == 分支)是唯一事实源:
- nav.js 的 ROUTES 必须全部存在于后端路由
- index.html 侧栏与 subnav.js 分组里的 href 必须全部存在于后端路由
- 前端不得出现已知死链路径
"""

import os
import re

from lingmengwork.web import server as _srv

SERVER_PY = os.path.join(os.path.dirname(_srv.__file__), "server.py")
STATIC = os.path.join(os.path.dirname(_srv.__file__), "static")


def backend_page_routes():
    with open(SERVER_PY, encoding="utf-8") as f:
        src = f.read()
    # 页面路由: _serve_file("<file>.html") 的分发分支上一行附近有 if p == "<path>"
    # 直接收集所有 if p == "<path>" 中非 /api /static 的路径, 再与 _serve_file 调用交叉验证
    paths = set(re.findall(r'if p == "([^"]+)"', src))
    api_or_static = {p for p in paths if p.startswith("/api/") or p.startswith("/static")}
    return paths - api_or_static


def test_nav_routes_exist_in_backend():
    routes = backend_page_routes()
    with open(os.path.join(STATIC, "nav.js"), encoding="utf-8") as f:
        js = f.read()
    nav_routes = set(re.findall(r'\[\s*"(/[^"]+)"\s*,\s*"', js))
    assert nav_routes, "应能从 nav.js 解析出 ROUTES"
    missing = nav_routes - routes
    assert not missing, "nav.js 指向后端不存在的路由: %s" % sorted(missing)


def test_index_sidebar_links_exist_in_backend():
    routes = backend_page_routes()
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read()
    hrefs = set(re.findall(r'class="nav-item" href="(/[^"]+)"', html))
    assert hrefs, "应能解析出侧栏链接"
    missing = hrefs - routes
    assert not missing, "index 侧栏死链: %s" % sorted(missing)


def test_subnav_links_exist_in_backend():
    routes = backend_page_routes()
    with open(os.path.join(STATIC, "subnav.js"), encoding="utf-8") as f:
        js = f.read()
    hrefs = set(re.findall(r'href: "(/[^"]+)"', js))
    assert hrefs, "应能解析出 subnav 链接"
    missing = hrefs - routes
    assert not missing, "subnav 死链: %s" % sorted(missing)


def test_quick_annotation_ui_present():
    with open(os.path.join(STATIC, "superagent.html"), encoding="utf-8") as f:
        html = f.read()
    for tok in ("toggleQuickAnno", "submitQuickAnno", "✍️ 批注",
                "/api/superagent/annotations/create"):
        assert tok in html, "洞察页缺快速批注: " + tok


def test_no_known_dead_links_in_html():
    routes = backend_page_routes()
    bad = ("/plugin_hub", "/memory_graph")
    for name in os.listdir(STATIC):
        if not name.endswith(".html"):
            continue
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            html = f.read()
        for b in bad:
            assert 'href="%s"' % b not in html, "%s 含死链 %s" % (name, b)
            assert '"%s"' % b not in html or name == "superagent.html", \
                "%s 含死链字符串 %s" % (name, b)

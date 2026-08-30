# -*- coding: utf-8 -*-
"""Phase 79 · 全站前端统一化测试.

覆盖:
- ds.css 单一事实源存在, 含统一变量/左侧栏 shell/按钮三态/滚动条 + 旧命名别名兼容
- sidebar.js 左侧全局导航存在, #lmw-sidebar 渲染 + 当前页高亮 + 链接零死链
- 除核心 4 页(superagent/cost/observability/index)外全部二级页面套用统一 shell
  (含 lmw-shell + #lmw-sidebar + ds.css + sidebar.js + data-lmw-nonav)
- 核心 4 页未被误改(不含 lmw-shell)
- 线上 /static/ds.css、/static/sidebar.js 可达; 改造页 /settings 返回 200 且含 lmw-shell
"""

import http.client
import os
import re
import threading
import time

from lingmengwork.web import server as _srv

STATIC = os.path.join(os.path.dirname(_srv.__file__), "static")
SERVER_PY = os.path.join(os.path.dirname(_srv.__file__), "server.py")
CORE = {"superagent.html", "cost.html", "observability.html", "index.html"}


def backend_page_routes():
    with open(SERVER_PY, encoding="utf-8") as f:
        src = f.read()
    paths = set(re.findall(r'if p == "([^"]+)"', src))
    return {p for p in paths if not p.startswith("/api/") and not p.startswith("/static")}


def test_ds_css_is_single_source():
    with open(os.path.join(STATIC, "ds.css"), encoding="utf-8") as f:
        css = f.read()
    for tok in ("--bg0:", "--grad:", ".lmw-shell", ".sidebar", ".btn2",
                "::-webkit-scrollbar", ".card", ".sb-item"):
        assert tok in css, "ds.css 缺: " + tok
    # 旧批次变量别名兼容 (让旧页无需改结构即统一配色)
    assert "--card:var(--panel)" in css, "ds.css 缺旧命名别名兼容"


def test_sidebar_js_renders_and_no_dead_links():
    routes = backend_page_routes()
    with open(os.path.join(STATIC, "sidebar.js"), encoding="utf-8") as f:
        js = f.read()
    assert "lmw-sidebar" in js and "getElementById" in js
    # 回归护栏: 脚本注入在 </head> 前(同步), 必须等 DOMContentLoaded 才能拿到 #lmw-sidebar,
    # 否则 getElementById 拿到 null 直接 return, 侧栏永不渲染 (Phase79 实测 bug)。
    assert "DOMContentLoaded" in js, "必须在 DOMContentLoaded 后渲染侧栏"
    assert "addEventListener" in js, "应注册 DOMContentLoaded 监听"
    assert '" on"' in js, "应有当前页高亮"
    links = set(re.findall(r'href:\s*"([^"]+)"', js))
    assert links, "应能从 sidebar.js 解析出链接"
    dead = links - routes
    assert not dead, "sidebar.js 死链: %s" % sorted(dead)


def test_all_secondary_pages_unified():
    pages = [f for f in os.listdir(STATIC) if f.endswith(".html")]
    secondary = [p for p in pages if p not in CORE]
    assert len(secondary) >= 25, "二级页面应 >=25, 实际 %d" % len(secondary)
    for name in secondary:
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            html = f.read()
        assert 'class="lmw-shell"' in html, "缺 lmw-shell: " + name
        assert 'id="lmw-sidebar"' in html, "缺侧栏节点: " + name
        assert "/static/ds.css" in html, "缺 ds.css: " + name
        assert "/static/sidebar.js" in html, "缺 sidebar.js: " + name
        assert "data-lmw-nonav" in html, "缺 data-lmw-nonav: " + name


def test_core_pages_not_tampered():
    for name in CORE:
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            html = f.read()
        assert 'class="lmw-shell"' not in html, "核心页被误改: " + name


def test_served_assets_and_unified_page_live(tmp_path):
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9131), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9131, timeout=15)
        for asset in ("/static/ds.css", "/static/sidebar.js"):
            c.request("GET", asset)
            r = c.getresponse()
            assert r.status == 200, asset
            assert r.read(1), asset + " 为空"
        c.request("GET", "/settings")
        r = c.getresponse()
        html = r.read().decode("utf-8")
        assert r.status == 200 and 'class="lmw-shell"' in html, "/settings 未套用统一 shell"
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()

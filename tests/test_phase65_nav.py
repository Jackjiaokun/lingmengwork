# -*- coding: utf-8 -*-
"""Phase 65 · 全站统一导航测试.

覆盖:
- static/nav.js 存在且含 8 个主要路由 + 当前页高亮逻辑 + 幂等保护
- 除 index.html(自带侧栏) 外全部 30 页注入 nav.js, 且在 theme.js 之前
- index.html 不注入
- 线上 /cost 可达且已带 nav.js 引用(经本地起服验证)
"""

import http.client
import os
import threading
import time

from lingmengwork.web import server as _srv

STATIC = os.path.join(os.path.dirname(_srv.__file__), "static")
ROUTES = ["/", "/superagent", "/cost", "/observability", "/planboard",
          "/sandbox", "/plugins", "/settings"]


def test_nav_js_content():
    with open(os.path.join(STATIC, "nav.js"), encoding="utf-8") as f:
        js = f.read()
    for r in ROUTES:
        assert '"%s"' % r in js, "nav.js 缺路由: " + r
    assert "lmwNav" in js and "lmw-link" in js
    assert "getElementById" in js and "insertBefore" in js, "应有幂等保护与顶部挂载"
    assert '" on" : ""' in js, "应有当前页高亮(class 字符串)"


def test_all_pages_injected_except_index():
    pages = [f for f in os.listdir(STATIC) if f.endswith(".html")]
    assert len(pages) >= 30
    for name in pages:
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            html = f.read()
        if name == "index.html":
            assert "/static/nav.js" not in html, "index 有侧栏, 不应注入"
            continue
        assert "/static/nav.js" in html, "缺 nav.js: " + name
        # nav.js 应在 theme.js 之前(样式先于主题层, 顺序稳定)
        assert html.index("/static/nav.js") < html.index("/static/theme.js"), name


def test_nav_served_and_live_page_has_it(tmp_path):
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9129), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9129, timeout=15)
        c.request("GET", "/static/nav.js")
        r = c.getresponse()
        body = r.read().decode("utf-8")
        assert r.status == 200 and "lmwNav" in body

        c.request("GET", "/cost")
        r = c.getresponse()
        html = r.read().decode("utf-8")
        assert r.status == 200 and "/static/nav.js" in html
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()

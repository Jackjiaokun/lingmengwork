"""Phase 51 · 全局主题切换测试.

覆盖:
- theme.js 存在且含浅色变量覆盖/切换按钮/localStorage 记忆
- 全部静态页面均注入 <script src="/static/theme.js">
- 无重复注入
- 静态服务可返回 /static/theme.js (200, application/javascript)
"""

import http.client
import json
import os
import tempfile
import threading
import time

from lingmengwork.web import server as _srv

STATIC = os.path.join(os.path.dirname(_srv.__file__), "static")


def test_theme_js_exists_with_light_overrides():
    path = os.path.join(STATIC, "theme.js")
    assert os.path.isfile(path), "theme.js 应存在"
    src = open(path, encoding="utf-8").read()
    assert 'data-theme="light"' in src
    assert "lmw_theme" in src and "lmwThemeBtn" in src
    assert "--txt:#1a2337" in src and "--bg:#f2f5fb" in src, "浅色变量覆盖应存在"


def test_all_pages_inject_theme_script():
    pages = [f for f in os.listdir(STATIC) if f.endswith(".html")]
    assert len(pages) >= 25, "静态页面数量合理"
    missing = [f for f in pages
               if "/static/theme.js" not in open(os.path.join(STATIC, f), encoding="utf-8").read()]
    assert not missing, "未注入主题脚本: %s" % missing
    # 无重复注入
    dup = [f for f in pages
           if open(os.path.join(STATIC, f), encoding="utf-8").read().count("/static/theme.js") > 1]
    assert not dup, "重复注入: %s" % dup


def test_static_serves_theme_js():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9112), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9112, timeout=15)
        c.request("GET", "/static/theme.js")
        r = c.getresponse()
        body = r.read().decode("utf-8", "replace")
        assert r.status == 200
        assert "lmwThemeBtn" in body
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()

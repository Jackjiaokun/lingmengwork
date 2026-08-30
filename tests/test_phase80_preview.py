# -*- coding: utf-8 -*-
"""Phase 80 · 右侧产物预览容器测试.

覆盖:
- superagent.html 注入右侧 dock 容器(#previewDock/#pdBody/#pdFab 及头/操作按钮) + preview.js 引用
- dock 设计系统 CSS(.preview-dock / .pd-fab / .pd-body .md) 已内联到 <style>
- 旧的 #artPreview 文本框已移除(被 dock 取代)
- 产物中心 previewArtifact 已路由到 LMWW.preview; 执行产物 artChip 可点击(data-prev 委托)
- preview.js 暴露 window.LMWW.preview / togglePreview, 含 md/html 渲染与图片走 download 二进制
- (可选) 子进程跑 outputs/verify_preview.js, 真实执行渲染逻辑
"""
import os
import shutil
import subprocess

from lingmengwork.web import server as _srv

STATIC = os.path.join(os.path.dirname(_srv.__file__), "static")
SA = os.path.join(STATIC, "superagent.html")
PV = os.path.join(STATIC, "preview.js")
VERIFY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "outputs", "verify_preview.js")


def test_dock_containers_injected():
    html = open(SA, encoding="utf-8").read()
    for tok in ('id="previewDock"', 'id="pdFab"', 'id="pdBody"',
                'id="pdName"', 'id="pdType"', 'id="pdNew"', 'id="pdClose"',
                '/static/preview.js'):
        assert tok in html, "superagent.html 缺: " + tok


def test_dock_css_inline():
    html = open(SA, encoding="utf-8").read()
    for tok in (".preview-dock{", ".pd-fab{", ".pd-body .md{"):
        assert tok in html, "dock CSS 缺: " + tok


def test_old_artpreview_removed():
    html = open(SA, encoding="utf-8").read()
    assert 'id="artPreview"' not in html, "旧 #artPreview 文本框应已移除"


def test_preview_router_wired():
    html = open(SA, encoding="utf-8").read()
    assert "LMWW.preview" in html, "previewArtifact 应路由到 LMWW.preview"
    assert "data-prev" in html, "执行产物 artChip 应可点击预览(data-prev)"


def test_preview_js_exposes_api():
    js = open(PV, encoding="utf-8").read()
    assert "window.LMWW.preview = function" in js, "preview.js 应暴露 LMWW.preview"
    assert "window.LMWW.togglePreview" in js, "preview.js 应暴露 togglePreview"
    assert "renderMd" in js, "应含 Markdown 渲染"
    assert "srcdoc" in js, "HTML 应走 iframe srcdoc"
    assert "mode=download" in js, "图片应走 download 二进制"


def test_preview_render_logic_executes():
    node = shutil.which("node") or r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\node.exe"
    if not os.path.exists(node) or not os.path.exists(VERIFY):
        import pytest
        pytest.skip("node 或 verify 脚本不可用, 跳过执行级校验")
    r = subprocess.run([node, VERIFY], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, "verify_preview.js 失败:\n" + (r.stdout or "") + (r.stderr or "")


def test_inline_script_balanced_and_complete():
    """回归护栏: superagent.html 的 <script> 开/闭必须平衡, 且主工作台 tab 切换函数
    (showTab / showInsightTab) 必须落在内联脚本块内, 防止 '提前 </script>' 把关键
    JS 泄为可见文本(实测 Phase 80 截图暴露过此 bug)。"""
    html = open(SA, encoding="utf-8").read()
    opens = html.count("<script")
    closes = html.count("</script>")
    assert opens == closes, "<script (%d) 与 </script> (%d) 数量不平衡" % (opens, closes)
    # 注意: head 里现在有外部脚本引用(sidebar.js / common.js), 首个 <script> 已不再是内联块。
    # 必须定位"无 src 属性"的内联 script, 否则会拿到外部引用的空块做断言而误报。
    s, i = -1, 0
    while True:
        j = html.find("<script", i)
        if j < 0:
            break
        gt = html.find(">", j)
        if gt < 0:
            break
        if "src=" not in html[j:gt]:
            s = j
            break
        i = gt + 1
    e = html.find("</script>", s) if s >= 0 else -1
    assert s != -1 and e != -1, "找不到内联 script 边界"
    block = html[s:e]
    for fn in ("function showTab(", "function showInsightTab(", "setInterval(loadSchedules"):
        assert fn in block, "内联脚本缺关键片段 %s, 可能有提前 </script> 切断了主工作台逻辑" % fn

"""Phase 81: 右侧预览容器升级为多产物分页签堆叠 + 自动展开.

回归护栏:
- pdTabs 容器已注入 superagent.html
- preview.js 暴露 closePreview, 且 preview 入栈去重逻辑存在
- 内联脚本仍平衡(无游离 </script>)
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "lingmengwork", "web", "static", "superagent.html")
JS = os.path.join(ROOT, "lingmengwork", "web", "static", "preview.js")
VERIFY = os.path.join(ROOT, "outputs", "verify_preview.js")
NODE = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\node.exe"


def test_pd_tabs_container_injected():
    html = open(HTML, encoding="utf-8").read()
    assert 'id="pdTabs"' in html, "superagent.html 缺少 #pdTabs 页签栏容器"
    assert ".pd-tabs{" in html, "缺少 .pd-tabs CSS"
    assert ".pd-tab.active{" in html, "缺少 .pd-tab.active 高亮样式"


def test_preview_js_exposes_close_and_stack():
    js = open(JS, encoding="utf-8").read()
    assert "window.LMWW.closePreview" in js, "未暴露 closePreview"
    # 入栈去重: 相同 path 计算同一 id
    assert 'id = opts.path ? "p:" + opts.path' in js, "文件产物 id 去重逻辑缺失"
    # 页签渲染 + 点击委托
    assert "function renderTabs" in js
    assert "data-x=" in js, "缺少单页签关闭按钮 data-x"
    assert "tabsEl.addEventListener" in js, "页签栏缺少点击委托"


def test_preview_stub_passes():
    assert os.path.exists(VERIFY), "verify_preview.js 缺失"
    r = subprocess.run([NODE, VERIFY], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"verify_preview.js 失败:\n{r.stdout}\n{r.stderr}"
    assert "25 passed" in r.stdout, "应 25 passed"

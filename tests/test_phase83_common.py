"""Phase 83: 前端共享工具模块 common.js 契约测试。

回归护栏:
- common.js 暴露完整工具集, 且 esc 必须转义引号
  (旧 cost.html 的自实现只转义 &<> , 用在 HTML 属性里会破损/注入 —— 统一后该缺陷消失)
- common.js 必须在所有页面的 </head> 之前 (页面内联脚本位于 body, 依赖它先就绪)
- 各页面不得再保留自己的 esc 自实现 (重复已消除)
- ds.css 含 toast 兜底样式
- 内联 <script>/</script> 仍平衡 (Phase 80 游离标签教训)
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lingmengwork", "web", "static")

TOOLS = ["$", "$$", "esc", "toast", "api", "fmtTime", "fmtDate",
         "fmtDateTime", "fmtSize", "fmtInt", "fmtCny", "inlineMd"]


def _read(name):
    with io.open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def _html_pages():
    return sorted(n for n in os.listdir(STATIC) if n.endswith(".html"))


def test_common_js_exposes_full_toolset():
    js = _read("common.js")
    for t in TOOLS:
        assert ("L." + t + " =") in js, "common.js 应暴露 LMW.%s" % t


def test_esc_escapes_quotes():
    """回归护栏: 旧实现 /[&<>]/g 漏掉引号, 结果放进 HTML 属性会破损甚至注入。"""
    js = _read("common.js")
    m = re.search(r"function esc\(s\)[\s\S]{0,320}?replace\(/(\[[^\]]*\])/g", js)
    assert m, "未找到 esc 的 replace 字符类"
    char_class = m.group(1)
    assert '"' in char_class, "esc 必须转义双引号(否则 HTML 属性场景会破损)"
    assert "'" in char_class, "esc 必须转义单引号"
    for ch in ("&", "<", ">"):
        assert ch in char_class, "esc 必须转义 %s" % ch


def test_common_js_does_not_touch_dom_at_load():
    """common.js 位于 </head>, 加载时 DOM 尚未解析 —— 顶层立即执行的代码绝不能操作 DOM。

    sidebar.js 曾因在 head 里同步取 #lmw-sidebar 拿到 null 而完全不渲染。
    本模块前面都只是"函数定义"(不执行), 唯一立即执行的是末尾那一段 L.xxx 赋值。
    """
    js = _read("common.js")
    i = js.find("window.LMW = window.LMW")
    assert i >= 0, "common.js 应以 window.LMW 赋值收尾"
    tail = js[i:]  # 唯一会立即执行的顶层代码区
    assert "document." not in tail, "common.js 顶层赋值区不得触碰 DOM(加载时 DOM 未解析)"
    assert "getElementById" not in tail


def test_all_pages_load_common_js_before_head_end():
    missing, wrong_pos = [], []
    for name in _html_pages():
        src = _read(name)
        if "/static/common.js" not in src:
            missing.append(name)
            continue
        i_tag = src.rfind("/static/common.js")
        i_head = src.rfind("</head>")
        i_body_script = src.find("<body")
        if not (0 <= i_tag < i_head):
            wrong_pos.append(name)
    assert not missing, "未引入 common.js 的页面: %s" % missing
    assert not wrong_pos, "common.js 必须在 </head> 之前: %s" % wrong_pos


def test_pages_delegate_esc_to_shared_module():
    """重复已消除: 页面里不应再保留自己的 esc 自实现。"""
    left = []
    for name in _html_pages():
        src = _read(name)
        if re.search(r"function\s+esc\s*\(\s*s\s*\)\s*\{(?!\s*return\s+LMW\.)", src):
            left.append(name)
    assert not left, "仍保留自实现 esc 的页面: %s" % left


def test_delegating_pages_have_common_js():
    """凡是委托给 LMW.xxx 的页面, 必须先引入 common.js(否则运行时 undefined)。"""
    bad = []
    for name in _html_pages():
        src = _read(name)
        if re.search(r"\bLMW\.(esc|\$|toast|api|fmt\w+|inlineMd)\b", src) and "/static/common.js" not in src:
            bad.append(name)
    assert not bad, "使用了 LMW 却未引入 common.js 的页面: %s" % bad


def test_ds_css_has_toast_style():
    css = _read("ds.css")
    assert ".lmw-toast" in css, "缺少 toast 兜底样式"
    assert ".lmw-toast.show" in css, "缺少 toast 显示态样式"


def test_inline_script_tags_balanced():
    bad = []
    for name in _html_pages():
        src = _read(name)
        if src.count("<script") != src.count("</script>"):
            bad.append(name)
    assert not bad, "内联脚本标签不平衡: %s" % bad

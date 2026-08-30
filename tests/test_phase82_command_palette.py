"""Phase 82: 全局命令面板 (Cmd+K / Ctrl+K) 契约测试。

回归护栏:
- commands.js 暴露 window.LMW.cmd, 支持 Cmd/Ctrl+K 唤起与 ↑↓/Enter/Esc 键盘操作
- sidebar.js 暴露 window.LMW_NAV, 命令面板复用它(导航单一事实源, 避免两处硬编码)
- ds.css 含命令面板样式
- 全部静态页注入 commands.js, 且必须在 </body> 之前
  (注在 </head> 会因 DOM 未解析拿不到元素 —— sidebar.js 曾踩过这个坑)
- theme.js 必须定义 apply() (曾被调用却从未定义, 导致整脚本 ReferenceError)
- 注入后内联 <script>/</script> 仍平衡 (Phase 80 游离标签导致 JS 泄漏成文本)
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lingmengwork", "web", "static")


def _read(name):
    with io.open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def _html_pages():
    return sorted(n for n in os.listdir(STATIC) if n.endswith(".html"))


def test_commands_js_exists_and_exposes_api():
    js = _read("commands.js")
    assert "window.LMW.cmd" in js, "应挂载 window.LMW.cmd"
    for fn in ("open", "close", "toggle", "register", "all"):
        assert fn + ":" in js, "LMW.cmd 应暴露 %s" % fn


def test_commands_js_keyboard_contract():
    js = _read("commands.js")
    assert "metaKey" in js and "ctrlKey" in js, "应同时支持 Cmd(Mac) 与 Ctrl(Win)"
    assert '=== "k"' in js or "=== 'k'" in js, "应监听 K 键"
    assert "ArrowDown" in js and "ArrowUp" in js, "↑↓ 选择"
    assert "Enter" in js, "Enter 执行"
    assert "Escape" in js, "Esc 关闭"
    assert "preventDefault" in js, "必须阻止浏览器默认行为(否则被浏览器抢走快捷键)"


def test_commands_js_search_contract():
    js = _read("commands.js")
    assert "<mark>" in js, "命中片段应高亮"
    assert "function match" in js, "应实现模糊匹配(连续子串 + 子序列)"
    assert "无匹配命令" in js, "空结果应有空态提示"


def test_sidebar_exposes_nav_single_source_of_truth():
    js = _read("sidebar.js")
    assert "window.LMW_NAV" in js, "sidebar.js 必须暴露导航表供命令面板复用"


def test_commands_reuses_nav_table():
    js = _read("commands.js")
    assert "LMW_NAV" in js, "命令面板应复用 sidebar 导航表而非自己硬编码一份"


def test_ds_css_has_palette_styles():
    css = _read("ds.css")
    for cls in (".lmw-cmdk", ".lmw-cmdk-panel", ".lmw-cmdk-item",
                ".lmw-cmdk-input", ".lmw-cmdk-list", ".lmw-cmdk-hint"):
        assert cls in css, "ds.css 缺少命令面板样式 %s" % cls
    assert "lmw-cmdk-item.on" in css, "选中项高亮样式缺失"
    assert "var(--panel)" in css, "面板底色应复用设计变量以自动适配深浅主题"


def test_all_pages_inject_commands_js_before_body_end():
    missing, wrong_pos = [], []
    for name in _html_pages():
        src = _read(name)
        if "/static/commands.js" not in src:
            missing.append(name)
            continue
        i_tag = src.rfind("/static/commands.js")
        i_body = src.rfind("</body>")
        if not (0 <= i_tag < i_body):
            wrong_pos.append(name)
    assert not missing, "未注入命令面板的页面: %s" % missing
    assert not wrong_pos, "commands.js 必须在 </body> 之前: %s" % wrong_pos


def test_theme_js_defines_apply():
    """回归护栏: apply() 曾被调用(line 60)却从未定义 -> 每次执行抛 ReferenceError,
    导致 DOMContentLoaded 注册与主题浮球注入全部不生效, 深浅色切换一直失效。
    """
    js = _read("theme.js")
    assert re.search(r"function\s+apply\s*\(", js), "theme.js 必须定义 apply()"
    # 且必须在调用之前定义(同文件内顺序)
    i_def = js.find("function apply")
    i_call = js.find("apply(cur())")
    assert i_def >= 0 and i_call >= 0 and i_def < i_call, "apply() 必须先定义后调用"


def test_inline_script_tags_balanced():
    """Phase 80 教训: 游离 </script> 会让内联 JS 泄漏成页面可见文本。"""
    bad = []
    for name in _html_pages():
        src = _read(name)
        if src.count("<script") != src.count("</script>"):
            bad.append((name, src.count("<script"), src.count("</script>")))
    assert not bad, "内联脚本标签不平衡: %s" % bad

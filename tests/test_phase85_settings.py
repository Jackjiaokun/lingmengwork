"""Phase 85 · 设置中心 dsh 化整合契约测试。

护栏:
- settings.html 含 dsh 范式关键结构(左栏导航 / 主区 section / 关闭返回 / 打开配置切换 / 主题三选)
- 后端 _SETTINGS_SCHEMA 增了 ui.theme(暗/亮/跟随系统)与 ui.language
- 内联 <script> 平衡(防 P80 教训的游离 </script> 泄漏)
"""
import io
import os
import re


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lingmengwork", "web", "static")
SERVER = os.path.join(ROOT, "lingmengwork", "web", "server.py")


def _read(name):
    with io.open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def _server_src():
    with io.open(SERVER, encoding="utf-8") as f:
        return f.read()


def test_settings_html_has_left_nav():
    html = _read("settings.html")
    assert 'class="lmw-set-nav"' in html, "必须含左栏分组导航(仿 dsh 范式)"
    assert 'id="nav"' in html


def test_settings_html_section_row_alignment():
    html = _read("settings.html")
    assert "lmw-set-section" in html, "主区 section 行: 标题+控件 贴右"
    assert "lmw-set-info" in html and "lmw-set-ctl" in html


def test_settings_html_close_button_returns_to_workbench():
    html = _read("settings.html")
    assert "location.href='/superagent'" in html, "关闭按钮应返回工作台"


def test_settings_html_open_config_file_toggle():
    html = _read("settings.html")
    assert "function showRaw" in html and "function showForm" in html
    assert "showRaw()" in html and "showForm()" in html
    assert "打开配置文件" in html
    # 原始 TOML 视图
    assert 'id="rawView"' in html and 'id="mainView"' in html


def test_settings_html_theme_chooser_widget():
    html = _read("settings.html")
    assert "lmw-choose" in html, "外观主题必须用三选卡片(浅/深/跟随)"
    assert "applyTheme" in html
    assert "isThemeKey" in html
    # 图标与中文标签
    for lab in ("深色", "浅色", "跟随系统"):
        assert lab in html, "三选卡缺标签 %s" % lab


def test_settings_html_inline_script_balanced():
    src = _read("settings.html")
    assert src.count("<script") == src.count("</script>"), \
        "<script> 与 </script> 数量不平衡(可能游离 </script> 泄漏 JS)"


def test_settings_html_no_legacy_tabs():
    """旧版的「表单视图/原始 TOML」tab 已被 dsh 范式替换; 头部不应再含 .tab。"""
    html = _read("settings.html")
    assert "data-view=\"form\"" not in html and "data-view=\"raw\"" not in html
    assert "switchTab" not in html


def test_schema_has_ui_theme_with_three_options():
    """后端 schema 必须含 ui.theme, 且 options 是 [dark, light, auto], 整合才完整。"""
    src = _server_src()
    assert '"ui.theme"' in src, "schema 必须含 ui.theme 字段(让主题设置进 settings 而非游离)"
    m = re.search(r'"ui\.theme"[^}]*?options":\s*\[([^\]]+)\]', src)
    assert m, "ui.theme 必须有 options 列表"
    opts = [o.strip().strip('"\'') for o in m.group(1).split(",")]
    for o in ("dark", "light", "auto"):
        assert o in opts, "ui.theme options 缺 %s" % o


def test_schema_has_ui_language_field():
    src = _server_src()
    assert '"ui.language"' in src, "schema 应预留 ui.language 字段(多语言切换占位)"


def test_settings_html_keeps_core_features():
    """重构不能丢功能: 保存/重载/原始 TOML/侧栏都必须在。"""
    html = _read("settings.html")
    for token in ("saveForm", "saveRaw", "load()", "showRaw", "showForm",
                  "function collectForm", "/static/sidebar.js", "/static/common.js",
                  "/static/commands.js", "/api/settings"):
        assert token in html, "丢失原有功能/资源: %s" % token

"""Phase 88 · LLM 提供方卡片列表(trae 模型页范式)契约测试。

护栏:
- LLM 后端组从单 select 升级为 trae 风纵向 provider 卡片(状态点 + 名称 + 副描述 + 选中态)
- 底部两个虚线添加按钮(添加提供方 / 添加自定义提供方)
- 添加按钮诚实引导到原始 TOML(多路 provider 是 [[llm.providers]] 数组, 标量表单改不了)
- 内联 script 平衡 + 不丢原有功能

注意(踩过坑): settings.html 里的卡片是 JS 用 className 渲染的,
契约要搜 JS 字符串字面量('lmw-provider-list'), 不能搜 HTML 属性(class="...")。
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


# ---------------- 后端 schema ----------------

def test_schema_llm_backend_options_unchanged():
    """改造前端渲染, 不能动后端 options(否则破坏既有配置兼容)。"""
    src = _server_src()
    m = re.search(r'"llm\.backend"[^}]*?options":\s*\[([^\]]+)\]', src)
    assert m, "llm.backend 必须有 options"
    opts = [o.strip().strip('"\'') for o in m.group(1).split(",")]
    for o in ("sensenova", "openai", "ollama", "mock", "auto"):
        assert o in opts, "llm.backend options 缺 %s" % o


# ---------------- 前端渲染 ----------------

def test_settings_renders_provider_cards():
    html = _read("settings.html")
    assert "isBackendKey" in html, "必须含 isBackendKey 识别"
    assert "buildProviderCards" in html, "必须含 buildProviderCards 函数"
    # JS 用 className 渲染, 搜字面量
    assert "'lmw-provider-list'" in html, "必须渲染 provider 卡片列表"
    assert "'lmw-provider-card'" in html


def test_settings_has_five_provider_cards():
    html = _read("settings.html")
    for name in ("SenseNova", "OpenAI 兼容", "Ollama 本地", "Mock 模拟", "自动选择"):
        assert name in html, "provider 卡片缺 %s" % name


def test_settings_has_two_dashed_add_buttons():
    """trae 标志: 底部两个虚线圆角添加按钮。"""
    html = _read("settings.html")
    assert "添加提供方" in html
    assert "添加自定义提供方" in html
    assert "'lmw-provider-add'" in html
    # 两个按钮都走 hintRaw
    assert "hintRaw" in html


def test_add_button_honestly_routes_to_raw_toml():
    """多路 provider 是数组, 表单改不了 —— 必须引导到原始 TOML, 不能做假按钮。"""
    html = _read("settings.html")
    i = html.find("function hintRaw")
    assert i > 0, "必须有 hintRaw 引导函数"
    body = html[i:i + 400]
    assert "showRaw()" in body, "hintRaw 必须切到原始 TOML 视图"
    assert "[[llm.providers]]" in body, "提示文案应说明是数组配置"


def test_provider_grid_in_correct_group():
    """buildProviderCards 必须先定义后调用。"""
    html = _read("settings.html")
    i_def = html.find("function buildProviderCards")
    i_call = html.find("isBackendKey(f.key) && Array.isArray(f.options)")
    assert 0 < i_def < i_call, "buildProviderCards 必须先定义后调用"


# ---------------- CSS ----------------

def test_ds_css_has_provider_classes():
    css = _read("ds.css")
    for cls in (".lmw-provider-list", ".lmw-provider-card", ".lmw-provider-card.on",
                ".lmw-provider-card .dot", ".lmw-provider-card .body", ".lmw-provider-add"):
        assert cls in css, "ds.css 缺少 %s" % cls


# ---------------- 不破坏 ----------------

def test_settings_inline_script_balanced():
    src = _read("settings.html")
    assert src.count("<script") == src.count("</script>")


def test_settings_keeps_existing_features():
    html = _read("settings.html")
    for token in ("saveForm", "saveRaw", "load()", "showRaw", "showForm",
                  "function collectForm", "buildProviderCards", "buildPresetGrid",
                  "isThemeKey", "applyTheme", "/static/commands.js", "/api/settings"):
        assert token in html, "丢失功能: %s" % token

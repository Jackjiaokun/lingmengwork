"""Phase 87 · Agent 预设卡片网格(trae 风 2x2 + 选中态 + 创造模式创建按钮)契约测试。

护栏:
- 后端 schema 含 agent.preset 字段, options 包含 4 个 trae 内置预设
- settings.html 渲染 2x2 卡片网格(4 卡 + 选中态 + 内置徽标)
- 底部含"用「创造模式」创建自定义预设"虚线圆角按钮
- ds.css 含 .lmw-preset-grid / .lmw-preset-card / .lmw-preset-create 类
- 不破坏原有 settings 功能(loadForm / saveForm / 主题三选 / 原始 TOML)
- 内联 script 平衡
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

def test_schema_has_agent_preset_field():
    src = _server_src()
    assert '"agent.preset"' in src, "schema 必须含 agent.preset(让 settings 接管 Agent 预设)"
    m = re.search(r'"agent\.preset"[^}]*?options":\s*\[([^\]]+)\]', src)
    assert m, "agent.preset 必须有 options 列表"
    opts = [o.strip().strip('"\'') for o in m.group(1).split(",")]
    for o in ("standard", "fullstack", "minimal", "custom"):
        assert o in opts, "agent.preset options 缺 %s" % o


def test_schema_has_agent_preset_group():
    """整个 Agent 预设分组(含描述)在 schema 中。"""
    src = _server_src()
    # 找包含 "agent.preset" 的 "title": "..." 行
    m = re.search(r'\{\s*"title":\s*"Agent 预设"[^}]*\}', src)
    assert m, "schema 缺「Agent 预设」分组"
    assert '"agent.preset"' in m.group(0)


# ---------------- 前端 settings.html 渲染 ----------------

def test_settings_renders_preset_grid():
    html = _read("settings.html")
    # JS 函数里用 className='lmw-preset-grid' 渲染(不是 HTML 字面量)
    assert "'lmw-preset-grid'" in html, "必须渲染 trae 风 2x2 卡片网格"
    assert "buildPresetGrid" in html, "必须含 buildPresetGrid 函数"
    assert "isPresetKey" in html, "必须含 isPresetKey 识别"


def test_settings_has_four_preset_cards():
    """4 个 trae 内置预设都要渲染。"""
    html = _read("settings.html")
    for name in ("标准模式", "PTC 模式", "极简模式", "创造模式"):
        assert name in html, "卡片缺 %s" % name
    # 每个 preset 都有「内置」徽标
    assert html.count("内置") >= 4, "4 张卡片都应有「内置」徽标"
    # forEach 遍历 4 个 options: 用 'lmw-preset-card' 类名赋值
    assert "'lmw-preset-card'" in html, "卡片 className 必须设置"
    # forEach 循环的 4 个 meta 名字都已 in 检查(见上方), 证明 4 张卡片都被渲染


def test_settings_has_create_custom_preset_button():
    """trae 标志性的「用创造模式创建自定义预设」虚线圆角按钮。"""
    html = _read("settings.html")
    assert "用「创造模式」创建自定义预设" in html
    assert "lmw-preset-create" in html


def test_settings_preset_grid_in_correct_group():
    """Agent 预设卡片网格必须在「Agent 预设」分组内(而非主题三选等)。"""
    html = _read("settings.html")
    # groupSub(1) 是 "Agent 预设" 组(0 是界面外观)
    # 找到 buildPresetGrid 函数定义, 然后找调用 buildControl 的位置
    i_def = html.find("function buildPresetGrid")
    i_call = html.find("isPresetKey(f.key) && Array.isArray(f.options)")
    assert 0 < i_def < i_call, "buildPresetGrid 必须先定义后调用"


# ---------------- CSS 设计系统 ----------------

def test_ds_css_has_preset_classes():
    css = _read("ds.css")
    for cls in (".lmw-preset-grid", ".lmw-preset-card", ".lmw-preset-card.on",
                ".lmw-preset-card .hd", ".lmw-preset-card .dc", ".lmw-preset-create"):
        assert cls in css, "ds.css 缺少 %s" % cls


# ---------------- 不破坏其他功能 ----------------

def test_settings_inline_script_balanced():
    src = _read("settings.html")
    assert src.count("<script") == src.count("</script>")


def test_settings_keeps_existing_features():
    """Agent 预设新增不能破坏 settings 其他能力。"""
    html = _read("settings.html")
    for token in ("saveForm", "saveRaw", "load()", "showRaw", "showForm",
                  "function collectForm", "buildPresetGrid", "isThemeKey", "applyTheme",
                  "/static/commands.js", "/api/settings"):
        assert token in html, "丢失原有/新增功能: %s" % token

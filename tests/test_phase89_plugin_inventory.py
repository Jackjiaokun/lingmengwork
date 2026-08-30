"""Phase 89 · 插件清单页现代化 + 仿 DSH 插件启停 契约测试。

护栏:
- PluginHub 有 disabled 集合 + set_disabled / set_enabled
- list_connectors 返回 enabled 字段(用户启用) —— 与 available(env 判定) 是相互独立的两层
- wire() 必须跳过已停用的连接器
- server 有 _plugin_toggle 与 /api/plugins/connectors/toggle 路由, 且持久化走 config
- 前端插件页用卡片网格 + 搜索 + 开关, 且不丢原有功能
- ds.css 含插件卡片类

⚠️ 测试卫生(踩过坑): 不要用 hub.register_connector() —— 它会触发全局事件发射(_emit),
把插件事件写进持久化的事件流, 污染后续 review 类测试(表现为"单独跑过、一起跑挂"的
间歇性失败)。这里直接往 hub.connectors 放 Connector 对象, 绕开事件发射。
"""
import io
import os


from lingmengwork import plugin_hub as PH

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lingmengwork", "web", "static")
SERVER = os.path.join(ROOT, "lingmengwork", "web", "server.py")


def _read(name):
    with io.open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def _server_src():
    with io.open(SERVER, encoding="utf-8") as f:
        return f.read()


def _hub_with(*names):
    """构造干净的 Hub: 直接放 Connector, 不走 register_connector(避免 _emit 污染)。"""
    hub = PH.PluginHub()
    for n in names:
        hub.connectors[n] = PH.Connector(n, "test", "desc-" + n)
    return hub


# ---------------- 后端: 启停能力 ----------------

def test_hub_has_disabled_state():
    hub = PH.PluginHub()
    assert hasattr(hub, "disabled"), "PluginHub 必须有 disabled 集合(用户停用)"
    assert isinstance(hub.disabled, set)


def test_hub_set_enabled_roundtrip():
    hub = _hub_with("demoA")
    assert hub.set_enabled("demoA", False) is False
    assert "demoA" in hub.disabled
    assert hub.set_enabled("demoA", True) is True
    assert "demoA" not in hub.disabled


def test_hub_set_disabled_ignores_unknown_names():
    """config 里可能有历史残名, 只保留当前已注册的, 避免集合无限膨胀。"""
    hub = _hub_with("c1", "c2")
    hub.set_disabled(["c2", "ghost"])
    assert hub.disabled == {"c2"}, "未注册的名字应被忽略, 实际: %s" % hub.disabled


def test_list_connectors_exposes_enabled_field():
    hub = _hub_with("c1", "c2")
    hub.set_disabled(["c2"])
    rows = {r["name"]: r for r in hub.list_connectors()}
    assert "enabled" in rows["c1"], "list_connectors 必须返回 enabled 字段"
    assert rows["c1"]["enabled"] is True
    assert rows["c2"]["enabled"] is False
    # enabled(用户) 与 available(环境) 是两个独立维度, 不能互相覆盖
    assert "available" in rows["c2"]


def test_wire_skips_disabled_connectors(monkeypatch):
    """停用的连接器不得参与接入(这是开关"真实生效"的关键)。"""
    # wire() 末尾会 _emit(audit=True) 写审计日志 —— 必须屏蔽, 否则污染后续测试
    monkeypatch.setattr(PH, "_emit", lambda *a, **k: None)
    hub = _hub_with("on1", "off1")
    hub.set_disabled(["off1"])

    class FakeSA:
        plugin_connectors = {}

    sa = FakeSA()
    wired = hub.wire(sa)
    names = [c["name"] for c in wired["connectors"]]
    assert "on1" in names
    assert "off1" not in names, "停用的连接器不应参与接入"
    assert "off1" in [d["name"] for d in wired.get("disabled", [])]
    assert "off1" not in sa.plugin_connectors, "注入 superagent 的也必须排除已停用项"


# ---------------- 后端: API 与持久化 ----------------

def test_server_has_toggle_api_and_route():
    src = _server_src()
    assert "_plugin_toggle" in src, "必须有 _plugin_toggle 处理函数"
    assert "/api/plugins/connectors/toggle" in src, "必须有启停路由"
    assert '"plugins"' in src and "_set_array_in_toml" in src, \
        "应复用 _set_array_in_toml 把状态持久化到 config [plugins].disabled"


def test_plugin_get_syncs_disabled_from_config():
    """/api/plugins 返回前必须同步 config 的 disabled(否则重启后开关状态丢失)。"""
    src = _server_src()
    i = src.find("def _plugin_get")
    assert i > 0
    body = src[i:i + 700]
    assert "set_disabled" in body, "_plugin_get 必须同步停用列表"
    assert "disabled" in body


# ---------------- 前端 ----------------

def test_plugin_page_uses_card_grid():
    html = _read("plugin_hub.html")
    assert "lmw-plugin-grid" in html, "连接器应为卡片网格(不再是表格)"
    assert "lmw-plugin-card" in html


def test_plugin_page_has_search_box():
    html = _read("plugin_hub.html")
    assert 'id="connSearch"' in html
    assert "lmw-plugin-search" in html


def test_plugin_page_has_enable_toggle():
    html = _read("plugin_hub.html")
    assert "toggleConn" in html, "必须有启停处理函数"
    assert "lmw-plugin-toggle" in html
    assert "/api/plugins/connectors/toggle" in html


def test_toggle_rolls_back_on_failure():
    """请求失败必须回滚开关, 不能留下与服务端不一致的假状态。"""
    html = _read("plugin_hub.html")
    i = html.find("async function toggleConn")
    assert i > 0
    body = html[i:i + 900]
    assert "cb.checked = !enabled" in body, "失败时必须回滚 UI 开关"
    assert "cb.disabled = true" in body, "应防连点导致状态错乱"


def test_plugin_page_keeps_original_features():
    html = _read("plugin_hub.html")
    for token in ("loadPlugins", "doWire", "doDiscover", "regConnector", "regExpert",
                  "expList", "/static/commands.js", "/api/plugins"):
        assert token in html, "丢失原有功能: %s" % token


def test_plugin_page_inline_script_balanced():
    src = _read("plugin_hub.html")
    assert src.count("<script") == src.count("</script>")


def test_ds_css_has_plugin_classes():
    css = _read("ds.css")
    for cls in (".lmw-plugin-grid", ".lmw-plugin-card", ".lmw-plugin-search",
                ".lmw-plugin-toggle", ".lmw-plugin-card .pd.ok"):
        assert cls in css, "ds.css 缺少 %s" % cls

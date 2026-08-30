"""Phase 86 · superagent 仿 trae/dsh 范式整合契约测试。

护栏:
- trae 风输入区存在(附件+权限+模型+发送四件套), 且 id="runBtn" / "goalInput" / "elapsed" 保留(功能零回归)
- 顶部 ⌘K 搜索栏存在, 点击唤起命令面板
- 内联 script 平衡(防 P80 教训)
- ds.css 含 trae 风 CSS 类
"""
import io
import os
import re


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lingmengwork", "web", "static")


def _read(name):
    with io.open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


# ---------------- 目标输入区(trae 风四件套) ----------------

def test_superagent_has_trae_compose_widget():
    html = _read("superagent.html")
    assert 'class="trae-compose"' in html, "目标入口必须用 trae 风容器"
    assert "trae-compose-toolbar" in html
    # 四件套: 附件(+)/权限/模型/发送
    assert html.count('class="trae-tool"') >= 3, "应有附件/权限/模型三个工具按钮"
    assert 'class="trae-send"' in html, "应有 trae-send 圆形发送按钮"
    assert "Full access" in html, "权限按钮应显示 Full access(trae 视觉)"
    assert "trae-compose-actions" in html


def test_run_goal_ids_preserved():
    """重构不能丢功能: runBtn / goalInput / elapsed 是被 JS 引用的关键锚点。"""
    html = _read("superagent.html")
    assert 'id="runBtn"' in html, "runBtn id 必须保留(JS onclick 引用)"
    assert 'id="goalInput"' in html, "goalInput id 必须保留(JS 读取输入)"
    assert 'id="elapsed"' in html, "elapsed id 必须保留(显示耗时)"
    assert "onclick=\"runSuper()\"" in html, "runSuper 调用必须保留"


# ---------------- 顶部 ⌘K 搜索栏 ----------------

def test_superagent_has_cmdk_search_bar():
    html = _read("superagent.html")
    assert 'id="traeSearch"' in html, "顶部必须有 ⌘K 搜索栏(仿 trae 顶部)"
    assert "trae-search" in html
    assert "搜索页面或执行命令" in html
    assert "⌘" in html and "K" in html, "搜索栏应显式提示 ⌘K 快捷键"


def test_cmdk_search_triggers_command_palette():
    html = _read("superagent.html")
    # 搜索栏 onclick 应调起命令面板(已存在的 LMW.cmd.open, Phase 82 暴露)
    assert "LMW.cmd" in html and ".open()" in html, "搜索栏点击必须唤起 LMW.cmd.open()"
    # 不能是空 alert / 无意义跳转


# ---------------- 设计系统类落到 ds.css ----------------

def test_ds_css_has_trae_classes():
    css = _read("ds.css")
    for cls in (".trae-search", ".trae-compose", ".trae-compose-toolbar",
                ".trae-tool", ".trae-send", ".trae-compose-actions"):
        assert cls in css, "ds.css 缺少 trae 风类 %s" % cls


# ---------------- 平衡与不破坏 ----------------

def test_superagent_inline_script_balanced():
    src = _read("superagent.html")
    assert src.count("<script") == src.count("</script>"), "内联 <script> 与 </script> 数量不平衡"


def test_superagent_keeps_essential_resources():
    """新增搜索/按钮不能把其他关键资源挤掉。"""
    html = _read("superagent.html")
    # superagent 自包含侧栏与样式(Phase 79 统一化主动跳过它), 这里只断言它实际依赖的资源
    for token in ("/static/common.js", "/static/commands.js",
                  "/static/preview.js", "/static/nav.js", "/static/theme.js"):
        assert token in html, "丢失关键资源: %s" % token


def test_superagent_trae_search_only_in_orch_tab():
    """trae 搜索栏只在「编排」tab 内, 避免重复干扰其他 tab。"""
    html = _read("superagent.html")
    # #traeSearch 应出现在 tab-orch 内(在 #tab-orch 之后、#tab-insight 之前)
    i_orch = html.find('id="tab-orch"')
    i_search = html.find('id="traeSearch"')
    i_insight = html.find('id="tab-insight"')
    assert 0 < i_orch < i_search < i_insight, "traeSearch 应在 tab-orch 内、tab-insight 之前"

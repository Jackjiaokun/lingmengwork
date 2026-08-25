"""TUI 视图与事件路由的单元测试 (不进入真实 alt-screen)。"""
import sys

from lingmengwork.tui.view import TerminalView, _wrap, _strip_ansi
from lingmengwork.tui.app import TuiApp


def _fake_cfg():
    return {
        "llm": {
            "backend": "mock",
            "mock": {"model": "mock-coder"},
        },
        "agent": {"max_iterations": 6},
        "tools": {"allowed_roots": ["."]},
    }


def test_wrap_basic():
    lines = _wrap("hello world this is a test", 10)
    assert all(_strip_ansi(l) <= 10 for l in lines)


def test_wrap_keeps_cjk():
    s = "中文测试中文测试中文测试"
    lines = _wrap(s, 8)
    assert len(lines) >= 2


def test_terminalview_render_no_crash():
    v = TerminalView()
    v.push_chat("你好 \033[36m世界\033[0m")
    v.push_event("事件日志一行")
    v.set_status("通道:1 并发:0")
    v.set_input("输入文字", 4)
    out = v.render()
    assert isinstance(out, str)
    assert "\033[" in out  # 含 ANSI 控制
    assert "灵梦work" in out or "对话" in out


def test_terminalview_pad_truncate():
    v = TerminalView()
    v.cols = 40
    padded = v._pad("ab", 10)
    assert _strip_ansi(padded) == 10
    trunc = v._truncate("x" * 50, 5)
    assert _strip_ansi(trunc) <= 5


def test_app_dispatch_help_and_clear():
    app = TuiApp(_fake_cfg(), {"mock": object()}, _fake_registry(), default_provider="mock")
    app._show_help()
    assert any("命令" in l for l in app.view.chat_buf)
    app.view.chat_buf = []
    app._dispatch("/clear")
    # clear 清空后 push 提示
    assert len(app.view.chat_buf) >= 1


def test_app_chat_fragment_merge():
    app = TuiApp(_fake_cfg(), {"mock": object()}, _fake_registry(), default_provider="mock")
    app.view.chat_buf = []
    app._append_chat_fragment("你好")
    app._append_chat_fragment("世界")
    assert app.view.chat_buf[-1].endswith("你好世界")


def _fake_registry():
    class R:
        def list_tools(self):
            return [{"name": "list_dir", "description": "x", "parameters": {}}]
        def execute(self, name, args):
            return "ok"
    return R()

"""交付报告渲染器的回归测试 (纯函数, 不依赖 MCP/LLM)。"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lingmengwork.web.server import _render_delivery_report


def _sample(ready, verdict, rc, passed, issues):
    return {
        "ok": True,
        "target": "a/b.py",
        "delivery_ready": ready,
        "ts": 1700000000,
        "test": {"command": "pytest -q", "rc": rc, "passed": passed, "raw": "1 passed"},
        "review": {
            "verdict": verdict,
            "score": 90 if ready else 60,
            "source": "静态评审",
            "summary": "ok" if ready else "fix",
            "issues": issues,
            "raw": "VERDICT: %s" % verdict,
        },
    }


def test_render_ready_contains_markers():
    html = _render_delivery_report(_sample(True, "approve", 0, True, [{"sev": "低", "desc": "行超长"}]), note="说明一下")
    assert "灵梦work · 交付报告" in html
    assert "可交付 ✅" in html
    assert "变更说明" in html
    assert "评审问题清单" in html
    assert "测试输出" in html
    assert "评审原始输出" in html
    assert "说明一下" in html


def test_render_escapes_user_note():
    html = _render_delivery_report(_sample(True, "approve", 0, True, []), note="<b>note</b> & co")
    # 用户内容必须被转义, 不得原样注入 HTML
    assert "<b>note</b>" not in html
    assert "&lt;b&gt;note&lt;/b&gt;" in html
    assert "&amp; co" in html


def test_render_not_ready_and_empty_issues():
    html = _render_delivery_report(_sample(False, "revise", 1, False, []))
    assert "暂不可交付 ⛔" in html
    assert "无遗留问题" in html  # issues 为空时显示占位

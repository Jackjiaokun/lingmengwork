"""Phase 94 自动化与本地智能工具扩容检验。

检验点:
  1. 7 个新工具全部进入 registry.TOOL_SCHEMAS / _IMPLS, 且全局名称唯一;
  2. 四创作域 DOMAIN_TOOLS 引用全部为真实工具名(无悬空);
  3. 7 个工具冒烟: flow_runner 编排 / formatter(json 真格式化 + py 降级) /
     deep_review 扫描 / local_llm_route 降级 / screenshot 降级 /
     clipboard 降级 / csv_convert(json+markdown)。

全部为纯本地、不联网、不启动真实服务器的测试, 避开沙箱网络隔离导致的挂起。
"""

import json
import os

import pytest

from lingmengwork.tools import registry as R
from lingmengwork.tools import suite_automation as sa
from lingmengwork import creation_domains as CD

NEW_TOOLS = [
    "flow_runner", "formatter", "deep_review", "local_llm_route",
    "screenshot", "clipboard", "csv_convert",
]


# ---------------------------------------------------------------------------
# 注册一致性
# ---------------------------------------------------------------------------
def test_registration_present_and_unique():
    names = [s["name"] for s in R.TOOL_SCHEMAS]
    assert len(names) == len(set(names)), "TOOL_SCHEMAS 存在重名"
    for t in NEW_TOOLS:
        assert t in names, "未进入 TOOL_SCHEMAS: %s" % t
        assert t in R._IMPLS, "未进入 _IMPLS: %s" % t


def test_domains_reference_real_tools():
    valid = {s["name"] for s in R.TOOL_SCHEMAS}
    for dom, tools in CD.DOMAIN_TOOLS.items():
        bad = [t for t in tools if t not in valid]
        assert not bad, "域 %s 含悬空引用: %s" % (dom, bad)


def test_permission_and_cache_layers():
    for t in ("flow_runner", "local_llm_route"):
        assert t in R._EXEC_TOOLS, "%s 应入 _EXEC_TOOLS" % t
    for t in ("formatter", "screenshot", "csv_convert"):
        assert t in R._WRITE_TOOLS, "%s 应入 _WRITE_TOOLS" % t
    for t in ("deep_review", "clipboard"):
        assert t in R._READONLY_TOOLS, "%s 应入 _READONLY_TOOLS" % t
    assert "deep_review" in R._CACHEABLE_TOOLS


# ---------------------------------------------------------------------------
# 工具冒烟
# ---------------------------------------------------------------------------
def test_flow_runner_executes(tmp_path):
    ctx = {"roots": [str(tmp_path)], "cwd": str(tmp_path)}
    spec = json.dumps({
        "vars": {"x": "1"},
        "steps": [
            {"name": "hi", "echo": "x=${x}"},
            {"set": {"y": "done"}},
            {"if": "x == '1'", "then": [{"run": "echo branch_ok"}]},
            {"write": {"file": "out.txt", "text": "var=${y}"}},
        ],
    })
    out = sa.flow_runner({"spec": spec}, ctx)
    assert "[flow_runner]" not in out, out[:300]
    assert "branch_ok" in out, out[:500]
    written = (tmp_path / "out.txt").read_text(encoding="utf-8")
    assert "var=done" in written


def test_formatter_json_real(tmp_path):
    ctx = {"roots": [str(tmp_path)], "cwd": str(tmp_path)}
    p = tmp_path / "a.json"
    p.write_text('{"b":2,"a":1}', encoding="utf-8")
    out = sa.formatter({"path": "a.json"}, ctx)
    assert "[formatter]" in out and "已格式化" in out, out[:200]
    assert "\n" in p.read_text(encoding="utf-8")


def test_formatter_py_fallback(tmp_path):
    ctx = {"roots": [str(tmp_path)], "cwd": str(tmp_path)}
    p = tmp_path / "b.py"
    p.write_text("x=1\ny=2\n", encoding="utf-8")
    out = sa.formatter({"path": "b.py"}, ctx)
    assert "[formatter]" in out, out[:200]
    # 无 black/autopep8 环境应优雅降级提示, 不抛异常
    assert ("black" in out or "autopep8" in out), out[:200]


def test_deep_review_smoke(tmp_path):
    ctx = {"roots": [str(tmp_path)], "cwd": str(tmp_path)}
    p = tmp_path / "m.py"
    p.write_text("def f():\n    return eval('1')\nclass A:\n    pass\n", encoding="utf-8")
    out = sa.deep_review({"path": "m.py"}, ctx)
    assert "深度评审" in out and "eval/exec" in out, out[:400]
    assert (tmp_path / "deep_review.md").exists()


def test_local_llm_route_no_server():
    ctx = {"roots": ["."], "cwd": "."}
    out = sa.local_llm_route({"prompt": "hi"}, ctx)
    assert "[local_llm_route]" in out and "失败" in out, out[:200]


def test_screenshot_no_engine():
    ctx = {"roots": ["."], "cwd": "."}
    out = sa.screenshot({"url": "http://example.invalid"}, ctx)
    assert "[screenshot]" in out, out[:200]


def test_clipboard_read_no_crash():
    ctx = {"roots": ["."], "cwd": "."}
    out = sa.clipboard({"action": "read"}, ctx)
    assert isinstance(out, str) and out.startswith("[clipboard]"), out[:200]


def test_csv_convert_json_and_markdown(tmp_path):
    ctx = {"roots": [str(tmp_path)], "cwd": str(tmp_path)}
    p = tmp_path / "t.csv"
    p.write_text("name,score\nalice,90\nbob,80\n", encoding="utf-8-sig")
    out = sa.csv_convert({"path": "t.csv", "to": "json"}, ctx)
    assert "[csv_convert]" in out and (tmp_path / "t.json").exists(), out[:200]
    out2 = sa.csv_convert({"path": "t.csv", "to": "markdown"}, ctx)
    assert "[csv_convert]" in out2 and (tmp_path / "t.md").exists(), out2[:200]

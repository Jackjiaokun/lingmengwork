"""Phase 93: 生产力工具集(可视化/办公自动化/知识检索)一致性与冒烟测试。

覆盖:
- 7 个新工具全部进入 TOOL_SCHEMAS / _IMPLS
- 工具名全局唯一
- 四创作域 DOMAIN_TOOLS 全部指向真实存在的工具
- 各新工具冒烟: 不崩溃, 文件产出类工具真实落盘且产物可解析
"""
import json
import os

import pytest

from lingmengwork.tools import registry as reg
from lingmengwork import creation_domains as cd


NEW_TOOLS = [
    "diagram", "chart", "api_test", "email_compose",
    "calendar_event", "knowledge_search", "pdf_make",
]


def test_new_tools_in_schemas():
    names = {t["name"] for t in reg.TOOL_SCHEMAS}
    for n in NEW_TOOLS:
        assert n in names, f"工具 {n} 未进入 TOOL_SCHEMAS"
    all_names = [t["name"] for t in reg.TOOL_SCHEMAS]
    assert len(all_names) == len(set(all_names)), "TOOL_SCHEMAS 存在重名工具"


def test_new_tools_in_impls():
    for n in NEW_TOOLS:
        assert n in reg._IMPLS, f"工具 {n} 未在 _IMPLS 注册"
        assert callable(reg._IMPLS[n]), f"工具 {n} 的实现不可调用"


def test_domain_tools_all_real():
    schema_names = {t["name"] for t in reg.TOOL_SCHEMAS}
    for dom, tools in cd.DOMAIN_TOOLS.items():
        for t in tools:
            assert t in schema_names, f"域 {dom} 引用了不存在的工具: {t}"


def _reg(tmp):
    return reg.Registry(roots=[str(tmp)], permission_mode="bypassPermissions", cfg={})


def test_diagram_from_nodes(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("diagram", {"kind": "flowchart", "title": "登录",
                                "nodes": {"A": "用户", "B": "校验", "C": "主页"},
                                "edges": ["A-->B: 输入密码", "B-->C: 成功"],
                                "out": "d.mmd"})
    assert "[diagram]" in out and "flowchart" in out, out[:300]
    fp = tmp_path / "d.mmd"
    assert fp.exists() and "flowchart" in fp.read_text(encoding="utf-8")


def test_diagram_spec_direct(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("diagram", {"spec": "sequenceDiagram\n  A->>B: 你好", "out": "s.mmd"})
    assert "[diagram]" in out
    fp = tmp_path / "s.mmd"
    assert fp.exists() and "sequenceDiagram" in fp.read_text(encoding="utf-8")


def test_chart_bar(tmp_path):
    r = _reg(tmp_path)
    data = json.dumps({"labels": ["Q1", "Q2", "Q3"],
                       "series": [{"name": "收入", "values": [10, 20, 15]}]})
    out = r.execute("chart", {"type": "bar", "title": "季度", "data": data, "out": "c.svg"})
    assert "[chart]" in out and "bar" in out, out[:300]
    fp = tmp_path / "c.svg"
    assert fp.exists()
    svg = fp.read_text(encoding="utf-8")
    assert "<svg" in svg and "polyline" in svg or "rect" in svg, "SVG 内容异常"


def test_chart_pie(tmp_path):
    r = _reg(tmp_path)
    data = json.dumps({"labels": ["A", "B", "C"], "values": [3, 5, 2]})
    out = r.execute("chart", {"type": "pie", "title": "占比", "data": data, "out": "p.svg"})
    assert "[chart]" in out
    fp = tmp_path / "p.svg"
    assert fp.exists() and "<path" in fp.read_text(encoding="utf-8")


def test_api_test_no_cases(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("api_test", {"cases": []})
    assert out.startswith("[api_test]") and "cases" in out, out[:200]


def test_api_test_bad_json(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("api_test", {"cases": "not-json"})
    assert out.startswith("[api_test]") and "JSON" in out, out[:200]


def test_email_compose_eml(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("email_compose", {"to": "a@b.com", "subject": "hi",
                                      "body": "hello 中文", "out": "draft.eml"})
    assert "[email_compose]" in out and "草稿" in out, out[:200]
    fp = tmp_path / "draft.eml"
    assert fp.exists()
    content = fp.read_text(encoding="utf-8")
    assert "Subject: hi" in content and "To: a@b.com" in content


def test_calendar_ics(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("calendar_event", {"title": "会议", "start": "2026-09-01T10:00:00",
                                       "end": "2026-09-01T11:00:00", "location": "线上",
                                       "alarm": 10, "out": "e.ics"})
    assert "[calendar_event]" in out, out[:200]
    fp = tmp_path / "e.ics"
    assert fp.exists()
    content = fp.read_text(encoding="utf-8")
    assert "BEGIN:VCALENDAR" in content and "SUMMARY:会议" in content and "BEGIN:VALARM" in content


def test_knowledge_index_and_query(tmp_path):
    r = _reg(tmp_path)
    (tmp_path / "doc1.txt").write_text("人工智能 机器学习 神经网络 深度学习", encoding="utf-8")
    (tmp_path / "doc2.txt").write_text("股票 基金 投资 风险控制", encoding="utf-8")
    out = r.execute("knowledge_search", {"action": "index", "path": "."})
    assert "[knowledge_search]" in out and "已索引" in out, out[:200]
    assert (tmp_path / ".lmw_kb_index.json").exists()
    out2 = r.execute("knowledge_search", {"query": "机器学习 神经网络", "limit": 3})
    assert "doc1.txt" in out2, out2
    out3 = r.execute("knowledge_search", {"query": "足球 篮球", "limit": 3})
    assert "未检索到" in out3, out3


def test_pdf_make_minimal(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("pdf_make", {"text": "# 标题\n这是正文第一行\n第二行", "out": "o.pdf"})
    assert "[pdf_make]" in out, out[:200]
    fp = tmp_path / "o.pdf"
    assert fp.exists()
    assert fp.read_bytes()[:5] == b"%PDF-", "PDF 头异常"

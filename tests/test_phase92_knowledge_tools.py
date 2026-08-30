"""Phase 92: 知识办公工具集一致性与冒烟测试。

覆盖:
- 7 个新工具全部进入 TOOL_SCHEMAS / _IMPLS
- 工具名全局唯一
- 四创作域 DOMAIN_TOOLS 全部指向真实存在的工具
- 各新工具冒烟: 不崩溃, 文件产出类工具真实落盘且产物可解析
"""
import os
import csv
import zipfile
import xml.etree.ElementTree as ET

from lingmengwork.tools import registry as reg
from lingmengwork import creation_domains as cd


NEW_TOOLS = [
    "mindmap", "translate", "summarize", "pdf_extract",
    "markdown_to_docx", "data_analysis", "db_query",
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


def test_mindmap_produces_mermaid(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("mindmap", {"topic": "灵梦work", "items": [["编码", ["读", "写"]], ["办公", ["文档", "脑图"]]],
                                "path": "m.mmd"})
    assert "[mindmap]" in out and "mindmap" in out and "root((" in out, out[:300]
    fp = tmp_path / "m.mmd"
    assert fp.exists() and "root((" in fp.read_text(encoding="utf-8")


def test_summarize_extractive(tmp_path):
    r = _reg(tmp_path)
    text = ("人工智能正在改变软件工程。自动化测试可以提升质量。代码评审能发现潜在缺陷。"
            "人工智能正在改变软件工程。持续集成缩短反馈周期。自动化测试可以提升质量。")
    out = r.execute("summarize", {"text": text, "sentences": 3})
    assert "[summarize]" in out and "关键词" in out, out[:300]


def test_translate_no_crash(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("translate", {"text": "Hello world", "to": "zh-CN", "from": "en"})
    assert isinstance(out, str) and out.startswith("[translate]"), out[:200]


def test_pdf_extract_missing_file(tmp_path):
    r = _reg(tmp_path)
    out = r.execute("pdf_extract", {"path": "nope.pdf"})
    assert out.startswith("[pdf_extract]") and "不存在" in out, out[:200]


def test_markdown_to_docx_valid(tmp_path):
    r = _reg(tmp_path)
    md = "# 标题一\n正文一段 **加粗** 与 *斜体*。\n## 小节\n- 项目甲\n- 项目乙\n"
    out = r.execute("markdown_to_docx", {"path": "out.docx", "md": md, "title": "测试文档"})
    assert "[markdown_to_docx]" in out, out[:200]
    fp = tmp_path / "out.docx"
    assert fp.exists() and zipfile.is_zipfile(str(fp))
    with zipfile.ZipFile(str(fp)) as z:
        ET.fromstring(z.read("word/document.xml"))
        ET.fromstring(z.read("word/styles.xml"))
        ET.fromstring(z.read("[Content_Types].xml"))


def test_data_analysis_csv(tmp_path):
    r = _reg(tmp_path)
    csvp = tmp_path / "sample.csv"
    rows = [["name", "score", "level"],
            ["a", "90", "A"], ["b", "82", "B"], ["c", "95", "A"],
            ["d", "70", "C"], ["e", "88", "B"], ["f", "91", "A"]]
    with open(str(csvp), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    out = r.execute("data_analysis", {"path": "sample.csv"})
    assert "[data_analysis]" in out, out[:200]
    assert (tmp_path / "sample.analysis.md").exists()
    assert (tmp_path / "sample.analysis.html").exists()
    rep = (tmp_path / "sample.analysis.md").read_text(encoding="utf-8")
    assert "score" in rep and "均值" in rep, rep[:400]


def test_db_query_create_and_select(tmp_path):
    import sqlite3
    r = _reg(tmp_path)
    dbp = tmp_path / "demo.db"
    con = sqlite3.connect(str(dbp))
    con.execute("CREATE TABLE t(id INTEGER, name TEXT)")
    con.execute("INSERT INTO t VALUES (1,'x'),(2,'y')")
    con.commit()
    con.close()
    # 列出表
    out1 = r.execute("db_query", {"db": "demo.db"})
    assert "[db_query]" in out1 and "t" in out1, out1[:200]
    # 查询
    out2 = r.execute("db_query", {"db": "demo.db", "sql": "SELECT * FROM t ORDER BY id"})
    assert "[db_query]" in out2 and "x" in out2 and "y" in out2, out2[:300]

"""Phase 95 研发效能/文档/协作工具测试 (零依赖, 纯本地)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingmengwork.tools import registry as R
from lingmengwork.tools import suite_rnd as sr
from lingmengwork import creation_domains as CD


NEW = ["code_metrics", "agent_team", "db_migrate", "pdf_merge",
       "pdf_split", "form_to_pdf", "text_compare"]


def _ctx(tmp_path):
    d = str(tmp_path)
    return {"roots": [d], "cwd": d}


def test_new_tools_registered():
    names = [s["name"] for s in R.TOOL_SCHEMAS]
    assert len(names) == len(set(names)), "TOOL_SCHEMAS 出现重名"
    for n in NEW:
        assert n in names, "缺失 schema: %s" % n
        assert n in R._IMPLS, "缺失 impl: %s" % n


def test_permission_classification():
    assert "code_metrics" in R._READONLY_TOOLS
    assert "text_compare" in R._READONLY_TOOLS
    assert "agent_team" in R._WRITE_TOOLS
    assert "db_migrate" in R._WRITE_TOOLS
    assert "pdf_merge" in R._WRITE_TOOLS
    assert "pdf_split" in R._WRITE_TOOLS
    assert "form_to_pdf" in R._WRITE_TOOLS
    assert "code_metrics" in R._CACHEABLE_TOOLS
    assert "text_compare" in R._CACHEABLE_TOOLS


def test_domains_reference_real_tools():
    names = set(s["name"] for s in R.TOOL_SCHEMAS)
    for dom, tools in CD.DOMAIN_TOOLS.items():
        bad = [t for t in tools if t not in names]
        assert not bad, "域 %s 引用了不存在的工具: %s" % (dom, bad)


def test_code_metrics(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "m.py").write_text(
        "def f(x):\n    if x>0:\n        return x\n    return 0\n\nclass A:\n    def m(self):\n        pass\n",
        encoding="utf-8")
    out = sr.code_metrics({"path": "m.py"}, ctx)
    assert "[code_metrics]" in out and "SLOC" in out and "圈复杂度" in out, out[:300]
    assert (tmp_path / "code_metrics.json").exists()


def test_agent_team(tmp_path):
    ctx = _ctx(tmp_path)
    out = sr.agent_team({"spec": '{"strategy":"debate","agents":[{"role":"coder","task":"写代码"},{"role":"reviewer","task":"评审"}]}'}, ctx)
    assert "[agent_team]" in out and "debate" in out, out[:300]
    assert (tmp_path / ".lmw_team").is_dir()
    files = list((tmp_path / ".lmw_team").glob("team_*.json"))
    assert files, "团队清单未写入"
    import json
    manifest = json.loads(files[0].read_text(encoding="utf-8"))
    assert manifest["agent_count"] == 2


def test_db_migrate_lifecycle(tmp_path):
    ctx = _ctx(tmp_path)
    assert "初始化" in sr.db_migrate({"action": "init", "db": "app.db", "dir": "mg"}, ctx)
    assert "已创建" in sr.db_migrate({"action": "create", "db": "app.db", "dir": "mg", "name": "m001"}, ctx)
    mp = tmp_path / "mg" / "m001.sql"
    mp.write_text("-- up\nCREATE TABLE t1 (id INTEGER PRIMARY KEY, v TEXT);\n-- down\nDROP TABLE t1;\n", encoding="utf-8")
    assert "已应用" in sr.db_migrate({"action": "up", "db": "app.db", "dir": "mg"}, ctx)
    st = sr.db_migrate({"action": "status", "db": "app.db", "dir": "mg"}, ctx)
    assert "OK " in st and "m001.sql" in st, st[:300]
    assert "已回滚" in sr.db_migrate({"action": "down", "db": "app.db", "dir": "mg"}, ctx)


def test_form_to_pdf_chinese(tmp_path):
    ctx = _ctx(tmp_path)
    out = sr.form_to_pdf({
        "title": "入职登记表",
        "fields": [{"label": "姓名", "type": "text"},
                   {"label": "部门", "value": "研发部"},
                   {"label": "备注", "value": "尽快到岗"}],
        "out": "form.pdf"}, ctx)
    assert "form.pdf" in out, out[:200]
    data = (tmp_path / "form.pdf").read_bytes()
    assert data[:5] == b"%PDF-", "不是合法 PDF"
    assert b"%%EOF" in data[-32:], "PDF 未正确结束"
    # 中文环境应嵌入字体(CIDFontType2); 无字体时退化为 ASCII 最小写入器(仍合法)
    assert b"CIDFontType2" in data or b"/Type /Font" in data


def test_text_compare(tmp_path):
    ctx = _ctx(tmp_path)
    out = sr.text_compare({"a": "苹果 香蕉 橙子", "b": "苹果 葡萄 橙子"}, ctx)
    assert "相似度" in out and "%" in out, out[:300]


def test_pdf_merge_graceful_without_lib(tmp_path):
    ctx = _ctx(tmp_path)
    out = sr.pdf_merge({"files": ["nope.pdf"]}, ctx)
    assert "[pdf_merge]" in out, out[:200]


def test_pdf_split_graceful_without_lib(tmp_path):
    ctx = _ctx(tmp_path)
    out = sr.pdf_split({"file": "nope.pdf"}, ctx)
    assert "[pdf_split]" in out, out[:200]

"""Phase 96 协作/运维/文档增强工具集隔离测试 (零依赖, 不触发全量 pytest)。"""
import os
import json
import sqlite3

from lingmengwork.tools import suite_phase96 as m


def _ctx(tmp_path):
    p = str(tmp_path)
    return {"roots": [p], "cwd": p}


def test_agent_team_run_spec(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.agent_team_run({
        "spec": {"agents": [{"role": "r1", "task": "t1"}, {"role": "r2", "task": "t2"}],
                 "strategy": "parallel"}}, ctx)
    assert "[agent_team_run]" in out
    assert (tmp_path / ".lmw_team").exists()
    prompts = list((tmp_path / ".lmw_team").glob("*_prompt.md"))
    assert len(prompts) == 2


def test_agent_team_run_missing(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.agent_team_run({}, ctx)
    assert "未找到团队清单" in out


def test_agent_team_run_debate(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.agent_team_run({
        "spec": {"agents": [{"role": "r1", "task": "t1"}], "strategy": "debate", "rounds": 3}}, ctx)
    assert "辩论回合" in out and "3 轮" in out


def test_pdf_redact_missing_file(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.pdf_redact({"terms": ["secret"]}, ctx)
    assert "缺 file" in out


def test_pdf_redact_missing_terms(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "x.pdf").write_text("dummy", encoding="utf-8")
    out = m.pdf_redact({"file": "x.pdf"}, ctx)
    assert "缺 terms" in out


def test_db_schema_doc_md(tmp_path):
    ctx = _ctx(tmp_path)
    dbp = tmp_path / "t.db"
    conn = sqlite3.connect(dbp)
    conn.execute("CREATE TABLE u (id INTEGER PRIMARY KEY, name TEXT NOT NULL, age INTEGER)")
    conn.execute("CREATE INDEX ix_u_name ON u(name)")
    conn.commit()
    conn.close()
    out = m.db_schema_doc({"db": "t.db"}, ctx)
    assert "表 `u`" in out
    assert "name" in out
    assert "索引" in out


def test_db_schema_doc_json(tmp_path):
    ctx = _ctx(tmp_path)
    dbp = tmp_path / "t.db"
    conn = sqlite3.connect(dbp)
    conn.execute("CREATE TABLE u (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.commit()
    conn.close()
    out = m.db_schema_doc({"db": "t.db", "format": "json"}, ctx)
    doc = json.loads(out)
    assert doc["tables"][0]["name"] == "u"
    assert doc["tables"][0]["columns"][1]["notnull"] is True


def test_form_validate_pass(tmp_path):
    ctx = _ctx(tmp_path)
    data = {"name": "Bob", "age": 30}
    schema = {"required": ["name"],
              "fields": {"name": {"type": "str"}, "age": {"type": "int", "min": 0, "max": 120}}}
    out = m.form_validate({"data": data, "schema": schema}, ctx)
    assert "通过" in out


def test_form_validate_fail(tmp_path):
    ctx = _ctx(tmp_path)
    data = {"age": "x"}
    schema = {"required": ["name"], "fields": {"name": {"type": "str"}, "age": {"type": "int"}}}
    out = m.form_validate({"data": data, "schema": schema}, ctx)
    assert "失败" in out
    assert "name" in out


def test_form_validate_enum_pattern(tmp_path):
    ctx = _ctx(tmp_path)
    data = {"status": "x", "email": "bad"}
    schema = {"fields": {"status": {"type": "str", "enum": ["on", "off"]},
                         "email": {"type": "str", "pattern": r"^[^@]+@[^@]+$"}}}
    out = m.form_validate({"data": data, "schema": schema}, ctx)
    assert "失败" in out


def test_release_notes_classify(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.release_notes({
        "version": "1.0.0",
        "changes": ["新增登录功能", "修复崩溃 bug", "优化性能 提速"]}, ctx)
    assert "✨ 新特性" in out
    assert "🐛 修复" in out
    assert "⚡ 性能" in out
    assert "v1.0.0" in out


def test_release_notes_from_changelog(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("新增导出\n修复导入错误\n", encoding="utf-8")
    out = m.release_notes({}, ctx)
    assert "新增" in out and "修复" in out


def test_code_search_semantic(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "a.py").write_text("def render_template(t):\n    return t\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def compute_sum(x, y):\n    return x + y\n", encoding="utf-8")
    out = m.code_search_semantic({"query": "render template", "path": "."}, ctx)
    assert "a.py" in out


def test_template_render_var(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.template_render({"template": "Hi {{name}}", "vars": {"name": "Bob"}}, ctx)
    assert "Hi Bob" in out


def test_template_render_for(tmp_path):
    ctx = _ctx(tmp_path)
    tpl = "items:\n{% for x in items %}- {{x}}{% endfor %}"
    out = m.template_render({"template": tpl, "vars": {"items": ["a", "b"]}}, ctx)
    assert "- a" in out and "- b" in out


def test_template_render_dotted(tmp_path):
    ctx = _ctx(tmp_path)
    tpl = "{{user.name}} <{{user.email}}>"
    out = m.template_render({"template": tpl, "vars": {"user": {"name": "Bo", "email": "b@x"}}}, ctx)
    assert "Bo <b@x>" in out

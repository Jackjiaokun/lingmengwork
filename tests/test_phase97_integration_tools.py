# -*- coding: utf-8 -*-
"""Phase 97 集成/运维/数据工具隔离测试 (不触发全量 pytest 挂死)."""
import os
import json
import csv
import sqlite3

from lingmengwork.tools import suite_phase97 as m
from lingmengwork.tools import registry as R

NEW7 = ["webhook_sign", "db_diff", "changelog_update", "code_search_ast",
        "csv_merge", "json_query", "env_check"]


def _ctx(tmp_path):
    return {"roots": [str(tmp_path)], "cwd": str(tmp_path)}


def test_webhook_sign_basic(tmp_path):
    out = m.webhook_sign({"secret": "k", "payload": "hi"}, _ctx(tmp_path))
    assert "X-Signature" in out and "sha256=" in out and "timestamp" in out


def test_webhook_sign_missing_secret(tmp_path):
    out = m.webhook_sign({"payload": "x"}, _ctx(tmp_path))
    assert "缺 secret" in out


def test_db_diff_equal(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    for p in (a, b):
        c = sqlite3.connect(str(p))
        c.execute("CREATE TABLE t(id INTEGER, name TEXT)")
        c.execute("INSERT INTO t VALUES(1,'x')")
        c.commit()
        c.close()
    out = m.db_diff({"a": str(a), "b": str(b)}, _ctx(tmp_path))
    assert "一致" in out


def test_db_diff_diff(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    ca = sqlite3.connect(str(a))
    ca.execute("CREATE TABLE t(id INTEGER, name TEXT)")
    ca.execute("INSERT INTO t VALUES(1,'x')")
    ca.commit()
    ca.close()
    cb = sqlite3.connect(str(b))
    cb.execute("CREATE TABLE t(id INTEGER, name TEXT)")
    cb.execute("INSERT INTO t VALUES(2,'y')")
    cb.commit()
    cb.close()
    out = m.db_diff({"a": str(a), "b": str(b)}, _ctx(tmp_path))
    assert "行差异" in out


def test_changelog_new(tmp_path):
    p = tmp_path / "CHANGELOG.md"
    out = m.changelog_update({"file": str(p), "version": "1.1.0",
                              "changes": ["新增X"]}, _ctx(tmp_path))
    assert p.exists() and "1.1.0" in p.read_text(encoding="utf-8")


def test_changelog_append(tmp_path):
    p = tmp_path / "CHANGELOG.md"
    p.write_text("# Changelog\n\n## [1.0.0] - 2026-01-01\n\n### Added\n\n- 初始\n",
                 encoding="utf-8")
    m.changelog_update({"file": str(p), "version": "1.1.0",
                        "changes": ["新增Y"]}, _ctx(tmp_path))
    txt = p.read_text(encoding="utf-8")
    assert txt.index("1.1.0") < txt.index("1.0.0")


def test_code_search_ast_def(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def foo():\n    pass\nclass Bar:\n    pass\n", encoding="utf-8")
    out = m.code_search_ast({"path": str(f), "kind": "def", "name": "foo"}, _ctx(tmp_path))
    assert "def foo" in out
    out2 = m.code_search_ast({"path": str(f), "kind": "class"}, _ctx(tmp_path))
    assert "class Bar" in out2


def test_code_search_ast_dir(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")
    out = m.code_search_ast({"path": str(tmp_path), "kind": "def"}, _ctx(tmp_path))
    assert "def hello" in out


def test_csv_merge_concat(tmp_path):
    f1, f2 = tmp_path / "a.csv", tmp_path / "b.csv"
    f1.write_text("id,name\n1,a\n", encoding="utf-8")
    f2.write_text("id,name\n2,b\n", encoding="utf-8")
    out = m.csv_merge({"files": [str(f1), str(f2)],
                       "out": str(tmp_path / "out.csv")}, _ctx(tmp_path))
    assert "已合并" in out
    rows = list(csv.reader(open(str(tmp_path / "out.csv"), encoding="utf-8")))
    assert len(rows) == 3


def test_csv_merge_join(tmp_path):
    f1, f2 = tmp_path / "a.csv", tmp_path / "b.csv"
    f1.write_text("id,name\n1,a\n", encoding="utf-8")
    f2.write_text("id,age\n1,20\n", encoding="utf-8")
    out = m.csv_merge({"files": [str(f1), str(f2)], "out": str(tmp_path / "j.csv"),
                       "how": "join", "keys": ["id"]}, _ctx(tmp_path))
    assert "已合并" in out
    rows = list(csv.reader(open(str(tmp_path / "j.csv"), encoding="utf-8")))
    assert len(rows[0]) == 3  # id,name,age


def test_json_query_file(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"a": {"b": [10, 20]}}), encoding="utf-8")
    out = m.json_query({"path": str(p), "jsonpath": "$.a.b[1]"}, _ctx(tmp_path))
    assert "20" in out


def test_json_query_inline_wildcard(tmp_path):
    out = m.json_query({"data": {"x": [{"y": 5}, {"y": 6}]},
                        "jsonpath": "$.x[*].y"}, _ctx(tmp_path))
    assert "5" in out and "6" in out


def test_env_check_missing(tmp_path):
    out = m.env_check({"required": ["NO_SUCH_VAR_XYZ_97"]}, _ctx(tmp_path))
    assert "缺失" in out and "NO_SUCH_VAR_XYZ_97" in out


def test_env_check_template(tmp_path):
    t = tmp_path / "tpl.env"
    t.write_text("PATH=xxx\nZZZ_MISSING_97=1\n", encoding="utf-8")
    out = m.env_check({"template": str(t)}, _ctx(tmp_path))
    assert "PATH" in out and "ZZZ_MISSING_97" in out


def test_registry_counts():
    names = [s["name"] for s in R.TOOL_SCHEMAS]
    assert len(names) == 124
    assert len(set(names)) == 124
    for n in NEW7:
        assert n in R._IMPLS

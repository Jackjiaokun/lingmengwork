# -*- coding: utf-8 -*-
"""Phase 101 隔离测试: 安全合规 + 数据工程 7 工具 (零回归)."""
import os
import json
import csv
import xml.etree.ElementTree as ET
import pytest

from lingmengwork.tools import suite_phase101 as m


def _ctx(tmp_path):
    return {"roots": [str(tmp_path)]}


# ---------------- secret_audit ----------------
def test_secret_audit_finds_key(tmp_path):
    f = tmp_path / "cfg.py"
    f.write_text('API_KEY = "sk-abcd1234567890abcdef1234567890ab"\nok\n', encoding="utf-8")
    out = m.secret_audit({"path": str(tmp_path)}, _ctx(tmp_path))
    assert "openai_key" in out or "generic_api_key" in out
    assert str(f) in out


def test_secret_audit_none(tmp_path):
    f = tmp_path / "clean.txt"
    f.write_text("hello world\nno secrets here\n", encoding="utf-8")
    out = m.secret_audit({"path": str(tmp_path)}, _ctx(tmp_path))
    assert "未发现" in out


def test_secret_audit_missing_path():
    out = m.secret_audit({"path": "C:/nope/xyz_404"}, {"roots": []})
    assert "不存在" in out


# ---------------- dep_check ----------------
def test_dep_check_requirements(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("flask==2.0.0\nrequests\n# comment\n", encoding="utf-8")
    out = m.dep_check({"path": str(f)}, _ctx(tmp_path))
    assert "2 个依赖" in out
    assert "未钉固" in out


def test_dep_check_dir(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"lodash": "^4.17.0"}}), encoding="utf-8")
    out = m.dep_check({"path": str(tmp_path)}, _ctx(tmp_path))
    assert "lodash" in out


def test_dep_check_no_manifest(tmp_path):
    out = m.dep_check({"path": str(tmp_path)}, _ctx(tmp_path))
    assert "未找到" in out


# ---------------- license_check ----------------
def test_license_check_mit(tmp_path):
    (tmp_path / "LICENSE").write_text(
        "MIT License\n\nCopyright (c) 2026\n\nPermission is hereby granted...", encoding="utf-8")
    out = m.license_check({"path": str(tmp_path)}, _ctx(tmp_path))
    assert "MIT" in out


def test_license_check_gpl(tmp_path):
    f = tmp_path / "LICENSE.txt"
    f.write_text("GNU General Public License v3.0", encoding="utf-8")
    out = m.license_check({"path": str(f)}, _ctx(tmp_path))
    assert "General Public License" in out


def test_license_check_unknown(tmp_path):
    (tmp_path / "LICENSE").write_text("some custom text", encoding="utf-8")
    out = m.license_check({"path": str(tmp_path)}, _ctx(tmp_path))
    assert "未能识别" in out


# ---------------- perm_diff ----------------
def test_perm_diff(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "common.txt").write_text("x", encoding="utf-8")
    (a / "only_a.txt").write_text("y", encoding="utf-8")
    (b / "common.txt").write_text("zzz", encoding="utf-8")
    (b / "only_b.txt").write_text("w", encoding="utf-8")
    out = m.perm_diff({"a": str(a), "b": str(b)}, _ctx(tmp_path))
    assert "仅存在于 a" in out
    assert "仅存在于 b" in out
    assert "大小不一致" in out


def test_perm_diff_bad_dir():
    out = m.perm_diff({"a": "C:/nope_a", "b": "C:/nope_b"}, {"roots": []})
    assert "不是有效目录" in out


# ---------------- json_to_csv ----------------
def test_json_to_csv_dicts(tmp_path):
    out_file = tmp_path / "o.csv"
    data = [{"name": "a", "age": 1}, {"name": "b", "age": 2}]
    out = m.json_to_csv({"json": json.dumps(data), "out_file": str(out_file)}, _ctx(tmp_path))
    assert "已写出" in out
    rows = list(csv.reader(open(out_file, encoding="utf-8-sig")))
    assert rows[0] == ["name", "age"]
    assert rows[1] == ["a", "1"]


def test_json_to_csv_lists(tmp_path):
    out_file = tmp_path / "o.csv"
    data = [["a", "b"], ["c", "d"]]
    out = m.json_to_csv({"json": json.dumps(data), "out_file": str(out_file)}, _ctx(tmp_path))
    assert "已写出" in out
    rows = list(csv.reader(open(out_file, encoding="utf-8-sig")))
    assert rows[0] == ["col0", "col1"]


def test_json_to_csv_no_out():
    out = m.json_to_csv({"json": "[]"}, {"roots": []})
    assert "缺 out_file" in out


def test_json_to_csv_bad_json(tmp_path):
    out = m.json_to_csv({"json": "{bad", "out_file": str(tmp_path / "x.csv")}, _ctx(tmp_path))
    assert "解析失败" in out


# ---------------- xml_query ----------------
def test_xml_query_text(tmp_path):
    xml = "<root><item><name>foo</name></item><item><name>bar</name></item></root>"
    out = m.xml_query({"xml": xml, "query": "root/item/name"}, _ctx(tmp_path))
    assert "foo" in out and "bar" in out


def test_xml_query_attr(tmp_path):
    xml = '<root><item id="7">x</item></root>'
    out = m.xml_query({"xml": xml, "query": "root/item/@id"}, _ctx(tmp_path))
    assert "7" in out


def test_xml_query_bad():
    out = m.xml_query({"xml": "<root><", "query": "root"}, {"roots": []})
    assert "解析失败" in out


# ---------------- toml_query ----------------
def test_toml_query_nested(tmp_path):
    toml = 'a = { b = { c = 42 } }\nlist = [{x = 1}, {x = 2}]\n'
    out = m.toml_query({"toml": toml, "path": "a.b.c"}, _ctx(tmp_path))
    assert "42" in out
    out2 = m.toml_query({"toml": toml, "path": "list[1].x"}, _ctx(tmp_path))
    assert "2" in out2


def test_toml_query_missing(tmp_path):
    out = m.toml_query({"toml": 'a = 1', "path": "z.y"}, _ctx(tmp_path))
    assert "不存在" in out

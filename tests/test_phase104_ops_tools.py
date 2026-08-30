# -*- coding: utf-8 -*-
"""Phase 104 工具隔离测试 (零网络依赖, 直接调用 suite_phase104)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lingmengwork.tools import suite_phase104 as m

CTX = {}


def test_json_pointer():
    out = m.json_pointer({"json": json.dumps({"a": {"b": [10, 20]}}), "pointer": "/a/b/1"}, CTX)
    assert out.strip() == "20"


def test_json_pointer_missing():
    out = m.json_pointer({"json": "{}", "pointer": "/x"}, CTX)
    assert "键不存在" in out


def test_json_pointer_root():
    out = m.json_pointer({"json": json.dumps({"x": 1})}, CTX)
    assert '"x": 1' in out


def test_csv_to_xml():
    out = m.csv_to_xml({"csv": "name,age\nBob,3\n"}, CTX)
    assert "<root>" in out and "<name>Bob</name>" in out and "<age>3</age>" in out


def test_yaml_to_toml():
    out = m.yaml_to_toml({"yaml": "name: Bob\nage: 3\nlist:\n  - a\n  - b\n"}, CTX)
    assert 'name = "Bob"' in out
    assert 'list = ["a", "b"]' in out


def test_yaml_to_toml_nested():
    out = m.yaml_to_toml({"yaml": "server:\n  host: 127.0.0.1\n  port: 8080\n"}, CTX)
    assert "[server]" in out and 'host = "127.0.0.1"' in out


def test_ini_query():
    out = m.ini_query({"ini": "[sec]\nkey=val\n", "section": "sec", "key": "key"}, CTX)
    assert out == "val"


def test_ini_query_section_list():
    out = m.ini_query({"ini": "[sec]\nkey=val\n"}, CTX)
    data = json.loads(out)
    assert "sec" in data["sections"]


def test_ini_to_json():
    out = m.ini_to_json({"ini": "[sec]\nkey=val\n"}, CTX)
    data = json.loads(out)
    assert data["sec"]["key"] == "val"


def test_license_list():
    out = m.license_list({"format": "json", "query": "mit"}, CTX)
    arr = json.loads(out)
    assert any(x["id"] == "MIT" for x in arr)


def test_license_list_category():
    out = m.license_list({"category": "copyleft"}, CTX)
    assert "GPL-3.0" in out


def test_json_schema_lint_ok():
    out = m.json_schema_lint({"schema": json.dumps(
        {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})}, CTX)
    assert "校验通过" in out


def test_json_schema_lint_bad():
    out = m.json_schema_lint({"schema": json.dumps({"type": "badtype"})}, CTX)
    assert "问题" in out


def test_json_schema_lint_required_type():
    out = m.json_schema_lint({"schema": json.dumps({"required": "notlist"})}, CTX)
    assert "问题" in out

"""Phase 105 工具隔离测试 (零网络, 标准库). 覆盖 7 个格式转换/查询工具的正常与降级路径."""
import pytest

from lingmengwork.tools import suite_phase105 as s


# --- json_to_xml -----------------------------------------------------------
def test_json_to_xml_nested_array():
    out = s.json_to_xml({"json": '{"a":1,"b":[2,3]}', "root": "r"}, {})
    assert out.startswith('<?xml')
    assert "<r>" in out and "<a>1</a>" in out
    assert "<item>2</item>" in out and "<item>3</item>" in out


def test_json_to_xml_bad_json():
    out = s.json_to_xml({"json": "{bad"}, {})
    assert out.startswith("[json_to_xml]")


# --- csv_to_yaml -----------------------------------------------------------
def test_csv_to_yaml_records():
    out = s.csv_to_yaml({"csv": "name,age\nx,1\ny,2"}, {})
    assert "- name: x" in out and "age: 1" in out
    assert "- name: y" in out


def test_csv_to_yaml_empty():
    out = s.csv_to_yaml({"csv": ""}, {})
    assert out == "# 空"


# --- yaml_to_ini -----------------------------------------------------------
def test_yaml_to_ini_sections():
    out = s.yaml_to_ini({"yaml": "sec1:\n  k: v\nsec2: 1"}, {})
    assert "[sec1]" in out and "k = v" in out
    assert "[sec2]" in out and "value = 1" in out


def test_yaml_to_ini_bad_top():
    out = s.yaml_to_ini({"yaml": "- just\n- list"}, {})
    assert out.startswith("[yaml_to_ini]")


# --- toml_to_xml -----------------------------------------------------------
def test_toml_to_xml_table():
    out = s.toml_to_xml({"toml": '[s]\nk="v"\n', "root": "r"}, {})
    assert "<r>" in out and "<s>" in out and "<k>v</k>" in out


def test_toml_to_xml_bad():
    out = s.toml_to_xml({"toml": "= = ="}, {})
    assert out.startswith("[toml_to_xml]")


# --- json_schema_compile ---------------------------------------------------
def test_schema_compile_ref_inline():
    sch = ('{"definitions":{"U":{"type":"string"}},'
           '"properties":{"a":{"$ref":"#/definitions/U"}}}')
    out = s.json_schema_compile({"schema": sch}, {})
    assert '"definitions"' not in out
    assert '"type": "string"' in out


def test_schema_compile_allof_merge():
    sch = ('{"allOf":[{"properties":{"a":{"type":"int"}}},'
           '{"properties":{"b":{"type":"str"}}}]}')
    out = s.json_schema_compile({"schema": sch}, {})
    assert '"a"' in out and '"b"' in out


# --- xml_to_yaml -----------------------------------------------------------
def test_xml_to_yaml_nested():
    out = s.xml_to_yaml({"xml": "<r><a>1</a><b><c>2</c></b></r>"}, {})
    assert "r:" in out and "a: 1" in out and "c: 2" in out


def test_xml_to_yaml_repeat_array():
    out = s.xml_to_yaml({"xml": "<r><x>1</x><x>2</x></r>"}, {})
    assert out.count("x:") == 2 or "- " in out


def test_xml_to_yaml_bad():
    out = s.xml_to_yaml({"xml": "<r><a>"}, {})
    assert out.startswith("[xml_to_yaml]")


# --- json_schema_docs ------------------------------------------------------
def test_schema_docs_fields():
    sch = ('{"title":"T","properties":{"a":{"type":"string","description":"A"}},'
           '"required":["a"]}')
    out = s.json_schema_docs({"schema": sch}, {})
    assert "# T" in out and "a (string, 必填): A" in out


def test_schema_docs_empty():
    out = s.json_schema_docs({"schema": '{"type":"object"}'}, {})
    assert "(无 properties 字段)" in out


def test_schema_docs_bad():
    out = s.json_schema_docs({"schema": "nope"}, {})
    assert out.startswith("[json_schema_docs]")

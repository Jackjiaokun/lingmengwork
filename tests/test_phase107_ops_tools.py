"""Phase 107 工具隔离测试 (零网络, 标准库). 覆盖 7 个互转/代码生成工具的正常与降级路径."""
from lingmengwork.tools import suite_phase107 as s


# --- xml_to_ini ------------------------------------------------------------
def test_xml_to_ini_sections():
    out = s.xml_to_ini({"xml": "<r><s><k>v</k></s><t>1</t></r>"}, {})
    assert "[s]" in out and "k = v" in out
    assert "[t]" in out and "value = 1" in out


def test_xml_to_ini_bad():
    out = s.xml_to_ini({"xml": "<r><a>"}, {})
    assert out.startswith("[xml_to_ini]")


# --- toml_to_ini -----------------------------------------------------------
def test_toml_to_ini_table():
    out = s.toml_to_ini({"toml": '[db]\nhost = "127.0.0.1"\n'}, {})
    assert "[db]" in out and "host = 127.0.0.1" in out


def test_toml_to_ini_bad():
    out = s.toml_to_ini({"toml": "= ="}, {})
    assert out.startswith("[toml_to_ini]")


# --- csv_to_toml -----------------------------------------------------------
def test_csv_to_toml_array_table():
    out = s.csv_to_toml({"csv": "id,name\n1,a\n2,b", "table": "rows"}, {})
    assert out.count("[[rows]]") == 2
    assert 'id = "1"' in out and 'name = "b"' in out


def test_csv_to_toml_no_rows():
    out = s.csv_to_toml({"csv": "id,name"}, {})
    assert out.startswith("# 无数据行")


# --- json_schema_to_python -------------------------------------------------
def test_schema_to_python_dataclass():
    sch = ('{"type":"object","properties":{"a":{"type":"string"},'
           '"b":{"type":"integer"}},"required":["a"]}')
    out = s.json_schema_to_python({"schema": sch, "name": "User"}, {})
    assert "from dataclasses import dataclass" in out
    assert "class User:" in out
    assert "a: str" in out and "b: int | None = None" in out


def test_schema_to_python_typed_dict():
    sch = ('{"type":"object","properties":{"a":{"type":"boolean"}}}')
    out = s.json_schema_to_python({"schema": sch, "style": "typed_dict"}, {})
    assert "from typing import Any, NotRequired, TypedDict" in out
    assert "a: NotRequired[bool]" in out


def test_schema_to_python_nested_class_first():
    sch = ('{"type":"object","properties":{"o":{"type":"object",'
           '"properties":{"x":{"type":"number"}},"required":["x"]}},'
           '"required":["o"]}')
    out = s.json_schema_to_python({"schema": sch, "name": "Root"}, {})
    # 嵌套类必须先于主类定义 (Python 要求)
    assert out.index("class O:") < out.index("class Root:")
    assert "x: float" in out and "o: O" in out


# --- yaml_to_csv -----------------------------------------------------------
def test_yaml_to_csv_records():
    out = s.yaml_to_csv({"yaml": "- a: 1\n  b: x\n- a: 2\n  b: y"}, {})
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0] == "a,b"
    assert "1,x" in lines and "2,y" in lines


def test_yaml_to_csv_scalar_list():
    out = s.yaml_to_csv({"yaml": "- p\n- q"}, {})
    assert "value" in out and "p" in out and "q" in out


# --- ini_to_xml ------------------------------------------------------------
def test_ini_to_xml_sections():
    out = s.ini_to_xml({"ini": "[s]\nk=v\n", "root": "cfg"}, {})
    assert "<cfg>" in out and "<s>" in out and "<k>v</k>" in out


def test_ini_to_xml_bad():
    out = s.ini_to_xml({"ini": "[s\nk=v"}, {})
    assert out.startswith("[ini_to_xml]")


# --- toml_to_csv -----------------------------------------------------------
def test_toml_to_csv_array_table():
    out = s.toml_to_csv({"toml": '[[p]]\nid = 1\n[[p]]\nid = 2\n'}, {})
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0] == "id" and "1" in lines and "2" in lines


def test_toml_to_csv_fallback_keyvalue():
    out = s.toml_to_csv({"toml": 'name = "x"\n'}, {})
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0] == "key,value"
    assert "name" in lines[1]


def test_toml_to_csv_bad():
    out = s.toml_to_csv({"toml": "= ="}, {})
    assert out.startswith("[toml_to_csv]")

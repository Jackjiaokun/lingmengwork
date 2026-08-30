"""Phase 106 工具隔离测试 (零网络, 标准库). 覆盖 7 个互转/生成工具的正常与降级路径."""
from lingmengwork.tools import suite_phase106 as s


# --- json_to_ini -----------------------------------------------------------
def test_json_to_ini_sections():
    out = s.json_to_ini({"json": '{"s":{"k":"v"},"t":1}'}, {})
    assert "[s]" in out and "k = v" in out
    assert "[t]" in out and "value = 1" in out


def test_json_to_ini_bad_top():
    out = s.json_to_ini({"json": "[1,2]"}, {})
    assert out.startswith("[json_to_ini]")


# --- csv_to_ini ------------------------------------------------------------
def test_csv_to_ini_key_col():
    out = s.csv_to_ini({"csv": "id,name\n1,a\n2,b", "key": "id"}, {})
    assert "[1]" in out and "name = a" in out
    assert "[2]" in out


def test_csv_to_ini_auto_row():
    out = s.csv_to_ini({"csv": "id,name\n1,a"}, {})
    assert "[row1]" in out and "id = 1" in out


# --- xml_to_toml -----------------------------------------------------------
def test_xml_to_toml_nested():
    out = s.xml_to_toml({"xml": "<r><a>1</a><b><c>2</c></b></r>"}, {})
    assert "[r]" in out and 'a = "1"' in out and "[r.b]" in out


def test_xml_to_toml_bad():
    out = s.xml_to_toml({"xml": "<r><a>"}, {})
    assert out.startswith("[xml_to_toml]")


# --- yaml_to_xml -----------------------------------------------------------
def test_yaml_to_xml_nested():
    out = s.yaml_to_xml({"yaml": "a: 1\nb:\n  - x\n  - y"}, {})
    assert "<a>1</a>" in out and "<item>x</item>" in out and "<item>y</item>" in out


def test_yaml_to_xml_custom_root():
    out = s.yaml_to_xml({"yaml": "k: v", "root": "cfg"}, {})
    assert "<cfg>" in out and "<k>v</k>" in out


# --- json_schema_to_ts -----------------------------------------------------
def test_schema_to_ts_interface():
    sch = ('{"type":"object","properties":{"a":{"type":"string"},'
           '"b":{"type":"array","items":{"type":"integer"}}},"required":["a"]}')
    out = s.json_schema_to_ts({"schema": sch, "name": "User"}, {})
    assert "export interface User {" in out
    assert "a: string;" in out and "b?: number[];" in out


def test_schema_to_ts_nested_object():
    sch = ('{"type":"object","properties":{"o":{"type":"object",'
           '"properties":{"x":{"type":"boolean"}},"required":["x"]}}}')
    out = s.json_schema_to_ts({"schema": sch}, {})
    assert "o?:" in out and "x: boolean;" in out


# --- ini_to_yaml -----------------------------------------------------------
def test_ini_to_yaml_sections():
    out = s.ini_to_yaml({"ini": "[s]\nK=v\n"}, {})
    assert "s:" in out and "K: v" in out  # 保留键大小写


def test_ini_to_yaml_bad():
    out = s.ini_to_yaml({"ini": "[s\nk=v"}, {})
    assert out.startswith("[ini_to_yaml]")


# --- json_to_toml ----------------------------------------------------------
def test_json_to_toml_scalar_array():
    out = s.json_to_toml({"json": '{"a":1,"b":["x","y"]}'}, {})
    assert "a = 1" in out and 'b = ["x", "y"]' in out


def test_json_to_toml_nested():
    out = s.json_to_toml({"json": '{"s":{"k":true}}'}, {})
    assert "[s]" in out and "k = true" in out


def test_json_to_toml_bad_top():
    out = s.json_to_toml({"json": "[1]"}, {})
    assert out.startswith("[json_to_toml]")

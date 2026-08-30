"""Phase 107 工具套件: 互转补齐 / Schema→Python 代码生成 (零依赖).

全部工具仅用标准库, 输入为文本, 失败以 [tool] 前缀优雅降级回灌模型。
- xml_to_ini          : XML -> INI (子元素为段, 孙元素文本为键)
- toml_to_ini         : TOML -> INI (表为段)
- csv_to_toml         : CSV -> TOML (每行为一个数组表项)
- json_schema_to_python: Schema -> Python dataclass / TypedDict (嵌套类前置定义)
- yaml_to_csv         : YAML -> CSV (映射列表, 键并集为列)
- ini_to_xml          : INI -> XML (段为元素, 键为子元素)
- toml_to_csv         : TOML -> CSV (数组表为行, 键并集为列)

复用: suite_phase104._to_toml / suite_phase105._yaml_dump·_build_xml·_yaml_scalar·_xml_escape
      suite_phase106._to_ini·_xml_to_obj / suite_phase103._yaml_load
标准库: json / csv / io / configparser / tomllib / xml.etree
"""

import json
import csv
import io
import configparser

from . import suite_phase103 as _phase103
from . import suite_phase104 as _phase104
from . import suite_phase105 as _phase105
from . import suite_phase106 as _phase106


# ---------------------------------------------------------------------------
# 共享辅助
# ---------------------------------------------------------------------------
def _rows_to_csv(rows, cols=None):
    """映射列表 -> CSV 文本 (列=键并集)。"""
    if cols is None:
        cols = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([_phase105._yaml_scalar(r.get(c, "")) for c in cols])
    return buf.getvalue().rstrip("\r\n")


def _py_scalar(t):
    return {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "null": "None",
        "object": "dict[str, Any]",
    }.get(t, "Any")


def _gen_class(name, spec, style, out):
    """生成类定义文本; 嵌套类递归 append 到 out (内层靠前, 保证先定义)。"""
    props = spec.get("properties") or {}
    req = set(spec.get("required") or [])
    keys = [k for k in props if k in req] + [k for k in props if k not in req]
    lines = []
    if style == "typed_dict":
        lines.append("class %s(TypedDict):" % name)
        for k in keys:
            t = _py_type(props[k], k, style, out)
            if k in req:
                lines.append("    %s: %s" % (k, t))
            else:
                lines.append("    %s: NotRequired[%s]" % (k, t))
    else:
        lines.append("@dataclass")
        lines.append("class %s:" % name)
        for k in keys:
            t = _py_type(props[k], k, style, out)
            if k in req:
                lines.append("    %s: %s" % (k, t))
            else:
                lines.append("    %s: %s | None = None" % (k, t))
    if not keys:
        lines.append("    pass")
    return "\n".join(lines)


def _py_type(spec, name_hint, style, out):
    if not isinstance(spec, dict):
        return "Any"
    t = spec.get("type")
    if isinstance(t, list):
        return " | ".join(_py_scalar(x) for x in t)
    if t == "array":
        items = spec.get("items")
        if isinstance(items, dict):
            return "list[%s]" % _py_type(items, name_hint + "Item", style, out)
        return "list[Any]"
    if t == "object" or "properties" in spec:
        props = spec.get("properties")
        if isinstance(props, dict) and props:
            cls = (name_hint[:1].upper() + name_hint[1:]) or "Nested"
            out.append(_gen_class(cls, spec, style, out))
            return cls
        return "dict[str, Any]"
    return _py_scalar(t)


# ---------------------------------------------------------------------------
# xml_to_ini
# ---------------------------------------------------------------------------
def xml_to_ini(args, ctx):
    raw = args.get("xml") or ""
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw)
        obj = _phase106._xml_to_obj(root)
    except Exception as e:
        return "[xml_to_ini] XML 解析失败: %s" % e
    data = obj if isinstance(obj, dict) else {"value": obj}
    out = _phase106._to_ini(data)
    if out is None:
        return "[xml_to_ini] 无法转换为 段/键 结构."
    return out


# ---------------------------------------------------------------------------
# toml_to_ini
# ---------------------------------------------------------------------------
def toml_to_ini(args, ctx):
    raw = args.get("toml") or ""
    try:
        import tomllib
        data = tomllib.loads(raw)
    except Exception as e:
        return "[toml_to_ini] TOML 解析失败: %s" % e
    out = _phase106._to_ini(data)
    if out is None:
        return "[toml_to_ini] 顶层必须是表."
    return out


# ---------------------------------------------------------------------------
# csv_to_toml
# ---------------------------------------------------------------------------
def csv_to_toml(args, ctx):
    raw = args.get("csv") or ""
    delim = args.get("delimiter") or ","
    table = (args.get("table") or "rows").strip() or "rows"
    try:
        rows = [r for r in csv.reader(io.StringIO(raw), delimiter=delim)]
    except Exception as e:
        return "[csv_to_toml] CSV 解析失败: %s" % e
    if not rows:
        return "# 空"
    header = rows[0]
    records = []
    for r in rows[1:]:
        padded = list(r) + [""] * (len(header) - len(r))
        records.append({h: padded[i] for i, h in enumerate(header)})
    if not records:
        return "# 无数据行"
    try:
        return _phase104._to_toml({table: records})
    except Exception as e:
        return "[csv_to_toml] 转换失败: %s" % e


# ---------------------------------------------------------------------------
# json_schema_to_python
# ---------------------------------------------------------------------------
def json_schema_to_python(args, ctx):
    raw = args.get("schema") or ""
    name = (args.get("name") or "Model").strip() or "Model"
    style = (args.get("style") or "dataclass").strip() or "dataclass"
    if style not in ("dataclass", "typed_dict"):
        style = "dataclass"
    try:
        schema = json.loads(raw)
    except Exception as e:
        return "[json_schema_to_python] Schema 解析失败: %s" % e
    if not isinstance(schema, dict):
        return "[json_schema_to_python] Schema 顶层必须是对象."
    try:
        out = []
        main = _gen_class(name, schema, style, out)
        if style == "typed_dict":
            header = "from typing import Any, NotRequired, TypedDict"
        else:
            header = "from dataclasses import dataclass\nfrom typing import Any"
        # import 后 2 空行, 顶层类之间 2 空行 (PEP8)
        return header + "\n\n\n" + "\n\n\n".join(out + [main])
    except Exception as e:
        return "[json_schema_to_python] 转换失败: %s" % e


# ---------------------------------------------------------------------------
# yaml_to_csv
# ---------------------------------------------------------------------------
def yaml_to_csv(args, ctx):
    raw = args.get("yaml") or ""
    try:
        data = _phase103._yaml_load(raw)
    except Exception as e:
        return "[yaml_to_csv] YAML 解析失败: %s" % e
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        if all(isinstance(x, dict) for x in data) and data:
            rows = data
        else:
            return _rows_to_csv([{"value": x} for x in data], ["value"])
    else:
        return "[yaml_to_csv] 顶层需为映射或映射列表."
    return _rows_to_csv(rows)


# ---------------------------------------------------------------------------
# ini_to_xml
# ---------------------------------------------------------------------------
def ini_to_xml(args, ctx):
    raw = args.get("ini") or ""
    root = (args.get("root") or "root").strip() or "root"
    cp = configparser.ConfigParser()
    cp.optionxform = str  # 保留键大小写
    try:
        cp.read_string(raw)
    except Exception as e:
        return "[ini_to_xml] INI 解析失败: %s" % e
    data = {}
    for sec in cp.sections():
        data[sec] = dict(cp.items(sec))
    if cp.defaults():
        data["DEFAULT"] = dict(cp.defaults())
    try:
        body = _phase105._build_xml(data, root, "item", _phase105._xml_escape)
    except Exception as e:
        return "[ini_to_xml] 转换失败: %s" % e
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


# ---------------------------------------------------------------------------
# toml_to_csv
# ---------------------------------------------------------------------------
def toml_to_csv(args, ctx):
    raw = args.get("toml") or ""
    table = (args.get("table") or "").strip()
    try:
        import tomllib
        data = tomllib.loads(raw)
    except Exception as e:
        return "[toml_to_csv] TOML 解析失败: %s" % e
    rows = None
    if table:
        if isinstance(data.get(table), list):
            rows = [r for r in data[table] if isinstance(r, dict)]
        if rows is None:
            return "[toml_to_csv] 未找到数组表: %s" % table
    else:
        for v in data.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                rows = v
                break
    if not rows:
        # 退化: 顶层键值对 -> key,value 两列
        return _rows_to_csv([{"key": k, "value": _phase105._yaml_scalar(v)}
                             for k, v in data.items()], ["key", "value"])
    return _rows_to_csv(rows)

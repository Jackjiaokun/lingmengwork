"""Phase 106 工具套件: 配置与结构化数据互转 / Schema 代码生成 (零依赖).

全部工具仅用标准库, 输入为文本, 失败以 [tool] 前缀优雅降级回灌模型。
- json_to_ini      : JSON -> INI (顶层映射每键为段)
- csv_to_ini       : CSV -> INI (可指定列作段名, 否则 rowN)
- xml_to_toml      : XML -> TOML (嵌套元素->表, 同名子元素->数组)
- yaml_to_xml      : YAML -> XML (嵌套/数组, 可指定根标签)
- json_schema_to_ts: JSON Schema -> TypeScript interface
- ini_to_yaml      : INI -> YAML (段->映射, 保留键大小写)
- json_to_toml     : JSON -> TOML (嵌套对象/标量数组/数组表)

序列化器复用: suite_phase104._to_toml / suite_phase105._yaml_dump·_build_xml·_ini_val
解析器复用  : suite_phase103._yaml_load (自研 YAML 子集) / tomllib / configparser / xml.etree
"""

import json
import csv
import io
import configparser

from . import suite_phase103 as _phase103
from . import suite_phase104 as _phase104
from . import suite_phase105 as _phase105


# ---------------------------------------------------------------------------
# 共享辅助
# ---------------------------------------------------------------------------
def _to_ini(data):
    """顶层映射 -> INI 文本; 非映射返回 None。"""
    if not isinstance(data, dict):
        return None
    out = []
    for sec, val in data.items():
        out.append("[%s]" % sec)
        if isinstance(val, dict):
            for k, v in val.items():
                out.append("%s = %s" % (k, _phase105._ini_val(v)))
        else:
            out.append("value = %s" % _phase105._ini_val(val))
    return "\n".join(out)


def _xml_to_obj(el):
    """Element -> dict/标量; 同名子元素聚合为数组。"""
    children = list(el)
    if not children:
        txt = (el.text or "").strip()
        return txt if txt else None
    obj = {}
    for c in children:
        v = _xml_to_obj(c)
        if c.tag in obj:
            if not isinstance(obj[c.tag], list):
                obj[c.tag] = [obj[c.tag]]
            obj[c.tag].append(v)
        else:
            obj[c.tag] = v
    return obj


def _ts_scalar(t):
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
        "object": "Record<string, any>",
    }.get(t, "any")


# ---------------------------------------------------------------------------
# json_to_ini
# ---------------------------------------------------------------------------
def json_to_ini(args, ctx):
    raw = args.get("json") or ""
    try:
        data = json.loads(raw)
    except Exception as e:
        return "[json_to_ini] JSON 解析失败: %s" % e
    out = _to_ini(data)
    if out is None:
        return "[json_to_ini] 顶层必须是对象(映射)."
    return out


# ---------------------------------------------------------------------------
# csv_to_ini
# ---------------------------------------------------------------------------
def csv_to_ini(args, ctx):
    raw = args.get("csv") or ""
    delim = args.get("delimiter") or ","
    key_col = (args.get("key") or "").strip()
    try:
        rows = [r for r in csv.reader(io.StringIO(raw), delimiter=delim)]
    except Exception as e:
        return "[csv_to_ini] CSV 解析失败: %s" % e
    if not rows:
        return "# 空"
    header = rows[0]
    ki = None
    if key_col and key_col in header:
        ki = header.index(key_col)
    out = []
    for i, r in enumerate(rows[1:], start=1):
        sec = r[ki] if (ki is not None and ki < len(r) and r[ki]) else ("row%d" % i)
        out.append("[%s]" % sec)
        for j, h in enumerate(header):
            out.append("%s = %s" % (h, r[j] if j < len(r) else ""))
    if not out:
        return "# 无数据行"
    return "\n".join(out)


# ---------------------------------------------------------------------------
# xml_to_toml
# ---------------------------------------------------------------------------
def xml_to_toml(args, ctx):
    raw = args.get("xml") or ""
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw)
        data = {root.tag: _xml_to_obj(root)}
        return _phase104._to_toml(data)
    except Exception as e:
        return "[xml_to_toml] 转换失败: %s" % e


# ---------------------------------------------------------------------------
# yaml_to_xml
# ---------------------------------------------------------------------------
def yaml_to_xml(args, ctx):
    raw = args.get("yaml") or ""
    root = (args.get("root") or "root").strip() or "root"
    try:
        data = _phase103._yaml_load(raw)
        body = _phase105._build_xml(data, root, "item", _phase105._xml_escape)
    except Exception as e:
        return "[yaml_to_xml] 转换失败: %s" % e
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


# ---------------------------------------------------------------------------
# json_schema_to_ts
# ---------------------------------------------------------------------------
def json_schema_to_ts(args, ctx):
    raw = args.get("schema") or ""
    name = (args.get("name") or "Root").strip() or "Root"
    try:
        schema = json.loads(raw)
    except Exception as e:
        return "[json_schema_to_ts] Schema 解析失败: %s" % e

    def ts_type(spec):
        if not isinstance(spec, dict):
            return "any"
        t = spec.get("type")
        if isinstance(t, list):
            return " | ".join(_ts_scalar(x) for x in t)
        if t == "array":
            items = spec.get("items")
            if isinstance(items, dict):
                inner = ts_type(items)
                return "%s[]" % (inner if " " not in inner else "(%s)" % inner)
            return "any[]"
        if t == "object" or "properties" in spec:
            props = spec.get("properties")
            if isinstance(props, dict) and props:
                req = spec.get("required") or []
                lines = ["{"]
                for k, v in props.items():
                    opt = "" if k in req else "?"
                    lines.append("  %s%s: %s;" % (k, opt, ts_type(v)))
                lines.append("}")
                return "\n".join(lines)
            return "Record<string, any>"
        return _ts_scalar(t)

    try:
        if isinstance(schema, dict) and (schema.get("type") == "object" or "properties" in schema):
            props = schema.get("properties") or {}
            req = schema.get("required") or []
            lines = ["export interface %s {" % name]
            for k, v in props.items():
                opt = "" if k in req else "?"
                lines.append("  %s%s: %s;" % (k, opt, ts_type(v)))
            lines.append("}")
            return "\n".join(lines)
        return "export type %s = %s;" % (name, ts_type(schema))
    except Exception as e:
        return "[json_schema_to_ts] 转换失败: %s" % e


# ---------------------------------------------------------------------------
# ini_to_yaml
# ---------------------------------------------------------------------------
def ini_to_yaml(args, ctx):
    raw = args.get("ini") or ""
    cp = configparser.ConfigParser()
    cp.optionxform = str  # 保留键大小写
    try:
        cp.read_string(raw)
    except Exception as e:
        return "[ini_to_yaml] INI 解析失败: %s" % e
    data = {}
    for sec in cp.sections():
        data[sec] = dict(cp.items(sec))
    if cp.defaults():
        data["DEFAULT"] = dict(cp.defaults())
    return _phase105._yaml_dump(data)


# ---------------------------------------------------------------------------
# json_to_toml
# ---------------------------------------------------------------------------
def json_to_toml(args, ctx):
    raw = args.get("json") or ""
    try:
        data = json.loads(raw)
    except Exception as e:
        return "[json_to_toml] JSON 解析失败: %s" % e
    if not isinstance(data, dict):
        return "[json_to_toml] 顶层必须是对象(映射)."
    try:
        return _phase104._to_toml(data)
    except Exception as e:
        return "[json_to_toml] 转换失败: %s" % e

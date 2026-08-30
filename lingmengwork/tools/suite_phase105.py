"""Phase 105 工具套件: 数据格式互转 / Schema 编译与文档 (零依赖).

全部工具仅用标准库, 输入为文本, 失败以 [tool] 前缀优雅降级回灌模型。
- json_to_xml       : JSON -> XML (嵌套/数组/文本, 可指定根与数组元素标签)
- csv_to_yaml       : CSV -> YAML (首行表头, 余下为记录)
- yaml_to_ini       : YAML -> INI (顶层映射, 每键为段)
- toml_to_xml       : TOML -> XML (嵌套表/数组表)
- json_schema_compile: JSON Schema 合并编译 ($ref / definitions 内联, allOf 展开)
- xml_to_yaml       : XML -> YAML (元素/文本, 同名子元素聚合为数组)
- json_schema_docs  : JSON Schema 字段文档生成 (字段/类型/必填/描述)

YAML 解析复用 suite_phase103 自研解析器; TOML 用标准库 tomllib(Python 3.11+).
"""

import json
import re
import csv
import io
import configparser

from . import suite_phase103 as _phase103


# ---------------------------------------------------------------------------
# 共享辅助
# ---------------------------------------------------------------------------
def _xml_escape(v):
    from xml.sax.saxutils import escape
    return escape(str(v))


def _xml_tag(name):
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(name))
    if s and s[0].isdigit():
        s = "_" + s
    return s or "item"


def _build_xml(v, tag, item, esc):
    tag = _xml_tag(tag)
    if isinstance(v, dict):
        inner = "".join(_build_xml(v[k], k, item, esc) for k in v)
        return "<%s>%s</%s>" % (tag, inner, tag)
    if isinstance(v, list):
        return "".join(_build_xml(x, item, item, esc) for x in v)
    if isinstance(v, bool):
        return "<%s>%s</%s>" % (tag, "true" if v else "false", tag)
    if v is None:
        return "<%s/>" % tag
    return "<%s>%s</%s>" % (tag, esc(v), tag)


def _yaml_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, str):
        if (v == "" or v.strip() != v or
                any(c in v for c in ":#{}[],&*?|<>=!%@`\"'")):
            return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
        return v
    return str(v)


def _yaml_dump(obj, indent=0):
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        lines = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                lines.append("%s%s:" % (pad, k))
                lines.append(_yaml_dump(v, indent + 1))
            else:
                lines.append("%s%s: %s" % (pad, k, _yaml_scalar(v)))
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        lines = []
        for v in obj:
            if isinstance(v, (dict, list)):
                s = _yaml_dump(v, indent + 1)
                first = True
                for ln in s.split("\n"):
                    if first:
                        lines.append("%s- %s" % (pad, ln.lstrip()))
                        first = False
                    else:
                        lines.append(ln)
            else:
                lines.append("%s- %s" % (pad, _yaml_scalar(v)))
        return "\n".join(lines)
    return _yaml_scalar(obj)


def _ini_val(v):
    if isinstance(v, (list, tuple)):
        return ", ".join(_yaml_scalar(x) for x in v)
    return _yaml_scalar(v)


# ---------------------------------------------------------------------------
# json_to_xml
# ---------------------------------------------------------------------------
def json_to_xml(args, ctx):
    raw = args.get("json") or ""
    root = (args.get("root") or "root").strip() or "root"
    item = (args.get("item") or "item").strip() or "item"
    try:
        data = json.loads(raw)
    except Exception as e:
        return "[json_to_xml] JSON 解析失败: %s" % e
    try:
        body = _build_xml(data, root, item, _xml_escape)
    except Exception as e:
        return "[json_to_xml] 转换失败: %s" % e
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


# ---------------------------------------------------------------------------
# csv_to_yaml
# ---------------------------------------------------------------------------
def csv_to_yaml(args, ctx):
    raw = args.get("csv") or ""
    delim = args.get("delimiter") or ","
    try:
        reader = csv.reader(io.StringIO(raw), delimiter=delim)
        rows = [r for r in reader]
    except Exception as e:
        return "[csv_to_yaml] CSV 解析失败: %s" % e
    if not rows:
        return "# 空"
    header = rows[0]
    records = []
    for r in rows[1:]:
        rec = {}
        for i, h in enumerate(header):
            rec[h] = r[i] if i < len(r) else ""
        records.append(rec)
    if not records:
        return "[]"
    return _yaml_dump(records)


# ---------------------------------------------------------------------------
# yaml_to_ini
# ---------------------------------------------------------------------------
def yaml_to_ini(args, ctx):
    raw = args.get("yaml") or ""
    try:
        data = _phase103._yaml_load(raw)
    except Exception as e:
        return "[yaml_to_ini] YAML 解析失败: %s" % e
    if not isinstance(data, dict):
        return "[yaml_to_ini] 顶层必须是映射(对象)."
    out = []
    for sec, val in data.items():
        out.append("[%s]" % sec)
        if isinstance(val, dict):
            for k, v in val.items():
                out.append("%s = %s" % (k, _ini_val(v)))
        else:
            out.append("value = %s" % _ini_val(val))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# toml_to_xml
# ---------------------------------------------------------------------------
def toml_to_xml(args, ctx):
    raw = args.get("toml") or ""
    root = (args.get("root") or "root").strip() or "root"
    try:
        import tomllib
        data = tomllib.loads(raw)
    except Exception as e:
        return "[toml_to_xml] TOML 解析失败: %s" % e
    try:
        body = _build_xml(data, root, "item", _xml_escape)
    except Exception as e:
        return "[toml_to_xml] 转换失败: %s" % e
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


# ---------------------------------------------------------------------------
# json_schema_compile
# ---------------------------------------------------------------------------
def json_schema_compile(args, ctx):
    raw = args.get("schema") or ""
    try:
        schema = json.loads(raw)
    except Exception as e:
        return "[json_schema_compile] Schema 解析失败: %s" % e

    defs = schema

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                if ref.startswith("#/"):
                    parts = ref[2:].split("/")
                    tgt = defs
                    for p in parts:
                        if isinstance(tgt, dict) and p in tgt:
                            tgt = tgt[p]
                        else:
                            tgt = None
                            break
                    if isinstance(tgt, dict):
                        merged = dict(tgt)
                        merged.pop("$ref", None)
                        for k, v in node.items():
                            if k != "$ref":
                                merged[k] = v
                        return resolve(merged)
                return node
            if "allOf" in node:
                merged = {k: v for k, v in node.items() if k != "allOf"}
                for sub in node["allOf"]:
                    s = resolve(sub)
                    if isinstance(s, dict):
                        for k, v in s.items():
                            if (k == "properties" and isinstance(v, dict)
                                    and isinstance(merged.get("properties"), dict)):
                                merged["properties"].update(v)
                            elif k not in merged:
                                merged[k] = v
                return merged
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    try:
        out = resolve(schema)
    except Exception as e:
        return "[json_schema_compile] 编译失败: %s" % e
    out.pop("definitions", None)
    out.pop("$defs", None)
    return json.dumps(out, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# xml_to_yaml
# ---------------------------------------------------------------------------
def xml_to_yaml(args, ctx):
    raw = args.get("xml") or ""
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw)
    except Exception as e:
        return "[xml_to_yaml] XML 解析失败: %s" % e

    def to_obj(el):
        children = list(el)
        if not children:
            txt = (el.text or "").strip()
            return txt if txt else None
        obj = {}
        for c in children:
            v = to_obj(c)
            if c.tag in obj:
                if not isinstance(obj[c.tag], list):
                    obj[c.tag] = [obj[c.tag]]
                obj[c.tag].append(v)
            else:
                obj[c.tag] = v
        return obj

    try:
        data = {root.tag: to_obj(root)}
    except Exception as e:
        return "[xml_to_yaml] 转换失败: %s" % e
    return _yaml_dump(data)


# ---------------------------------------------------------------------------
# json_schema_docs
# ---------------------------------------------------------------------------
def json_schema_docs(args, ctx):
    raw = args.get("schema") or ""
    try:
        schema = json.loads(raw)
    except Exception as e:
        return "[json_schema_docs] Schema 解析失败: %s" % e

    def walk(node, prefix, out):
        if not isinstance(node, dict):
            return
        props = node.get("properties")
        required = set(node.get("required") or [])
        if isinstance(props, dict):
            for name, spec in props.items():
                if not isinstance(spec, dict):
                    spec = {}
                typ = spec.get("type", "")
                if isinstance(typ, list):
                    typ = "/".join(typ)
                desc = spec.get("description", "")
                req = "必填" if name in required else "可选"
                out.append("- %s%s (%s, %s): %s" % (
                    prefix, name, typ or "any", req, desc))
                if isinstance(spec.get("properties"), dict):
                    walk(spec, prefix + name + ".", out)

    out = []
    title = schema.get("title")
    if title:
        out.append("# %s" % title)
    walk(schema, "", out)
    if not out:
        out.append("(无 properties 字段)")
    return "\n".join(out)

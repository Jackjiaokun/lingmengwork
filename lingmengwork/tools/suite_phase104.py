"""Phase 104 工具套件: 数据格式转换 / 配置查询 / 许可证 / Schema 校验 (零依赖).

全部工具仅用标准库, 输入为文本, 失败以 [tool] 前缀优雅降级回灌模型。
- json_pointer   : RFC6901 JSON Pointer 取值
- csv_to_xml     : CSV -> XML
- yaml_to_toml   : YAML -> TOML (YAML 解析复用 suite_phase103 自研解析器)
- ini_query      : INI 配置读取/查询 (configparser)
- ini_to_json    : INI -> JSON
- license_list   : 常见开源许可证清单与兼容性速查
- json_schema_lint: JSON Schema 语法基础校验
"""

import json
import re
import csv
import io
import configparser


# ---------------------------------------------------------------------------
# json_pointer
# ---------------------------------------------------------------------------
def json_pointer(args, ctx):
    raw = args.get("json") or ""
    ptr = (args.get("pointer") or "").strip()
    try:
        data = json.loads(raw)
    except Exception as e:
        return "[json_pointer] JSON 解析失败: %s" % e
    if not ptr or ptr == "/":
        return json.dumps(data, ensure_ascii=False, indent=2)
    if not ptr.startswith("/"):
        return "[json_pointer] pointer 必须以 / 开头, 如 /a/b/0"
    parts = ptr.split("/")[1:]

    def unescape(p):
        return p.replace("~1", "/").replace("~0", "~")

    cur = data
    path = []
    for p in parts:
        key = unescape(p)
        if isinstance(cur, list):
            try:
                idx = int(key)
            except Exception:
                return "[json_pointer] 数组索引非整数: %s (路径 %s)" % (key, "/".join(path))
            if idx < 0 or idx >= len(cur):
                return "[json_pointer] 索引越界: %s (长度 %d)" % (idx, len(cur))
            cur = cur[idx]
        elif isinstance(cur, dict):
            if key not in cur:
                return "[json_pointer] 键不存在: %s (路径 %s)" % (key, "/".join(path))
            cur = cur[key]
        else:
            return "[json_pointer] 无法在 %s 上按 %s 索引" % (type(cur).__name__, key)
        path.append(key)
    return json.dumps(cur, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# csv_to_xml
# ---------------------------------------------------------------------------
def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _xml_tag(s):
    s = str(s).strip()
    if not s or not (s[0].isalpha() or s[0] == "_"):
        s = "_" + s
    return "".join(ch if (ch.isalnum() or ch == "_" or ch == "-") else "_" for ch in s)


def csv_to_xml(args, ctx):
    raw = args.get("csv") or ""
    root = (args.get("root") or "root").strip() or "root"
    rowtag = (args.get("row") or "row").strip() or "row"
    try:
        reader = csv.reader(io.StringIO(raw))
        rows = list(reader)
    except Exception as e:
        return "[csv_to_xml] CSV 解析失败: %s" % e
    if not rows:
        return "[csv_to_xml] 空 CSV"
    header = rows[0]
    body = ["<%s>" % _xml_tag(root)]
    for r in rows[1:]:
        body.append("  <%s>" % _xml_tag(rowtag))
        for i, cell in enumerate(r):
            tag = header[i] if i < len(header) and header[i].strip() else ("col%d" % (i + 1))
            body.append("    <%s>%s</%s>" % (_xml_tag(tag), _xml_escape(cell), _xml_tag(tag)))
        body.append("  </%s>" % _xml_tag(rowtag))
    body.append("</%s>" % _xml_tag(root))
    return "\n".join(body)


# ---------------------------------------------------------------------------
# yaml_to_toml
# ---------------------------------------------------------------------------
def _toml_key(k):
    s = str(k)
    if re.fullmatch(r"[A-Za-z0-9_-]+", s):
        return s
    return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')


def _toml_repr(v):
    if v is None:
        return '""'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        if "\n" in v:
            return '"""\n%s"""' % v
        return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
    return '"%s"' % str(v)


def _toml_array(v):
    return "[" + ", ".join(_toml_repr(x) for x in v) + "]"


def _to_toml(data, prefix=""):
    lines = []
    if isinstance(data, dict):
        tables = []
        for k, v in data.items():
            if isinstance(v, dict):
                tables.append((k, v))
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                tables.append((k, v))
            elif isinstance(v, list):
                lines.append("%s = %s" % (_toml_key(k), _toml_array(v)))
            else:
                lines.append("%s = %s" % (_toml_key(k), _toml_repr(v)))
        lines.append("")
        for k, v in tables:
            name = prefix + _toml_key(k)
            if isinstance(v, dict):
                lines.append("[%s]" % name)
                lines.append(_to_toml(v, name + ".").rstrip())
                lines.append("")
            else:
                for item in v:
                    lines.append("[[%s]]" % name)
                    if isinstance(item, dict):
                        lines.append(_to_toml(item, name + ".").rstrip())
                    else:
                        lines.append(_toml_repr(item))
                    lines.append("")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                lines.append("[[]]")
                lines.append(_to_toml(item).rstrip())
                lines.append("")
            else:
                lines.append(_toml_repr(item))
    else:
        lines.append(_toml_repr(data))
    return "\n".join(lines)


def yaml_to_toml(args, ctx):
    raw = args.get("yaml") or ""
    if not raw.strip():
        return "[yaml_to_toml] 空 YAML"
    try:
        from . import suite_phase103
        data = suite_phase103._yaml_load(raw)
    except Exception as e:
        return "[yaml_to_toml] YAML 解析失败: %s" % e
    try:
        return _to_toml(data)
    except Exception as e:
        return "[yaml_to_toml] TOML 序列化失败: %s" % e


# ---------------------------------------------------------------------------
# ini_query / ini_to_json
# ---------------------------------------------------------------------------
def _parse_ini(raw):
    cp = configparser.ConfigParser()
    cp.optionxform = str  # 保留原始键大小写
    cp.read_string(raw)
    return cp


def ini_query(args, ctx):
    raw = args.get("ini") or ""
    section = (args.get("section") or "").strip()
    key = (args.get("key") or "").strip()
    try:
        cp = _parse_ini(raw)
    except Exception as e:
        return "[ini_query] INI 解析失败: %s" % e
    if not section:
        out = {"sections": cp.sections()}
        if cp.defaults():
            out["default"] = dict(cp.defaults())
        return json.dumps(out, ensure_ascii=False, indent=2)
    if not cp.has_section(section):
        return "[ini_query] 无此 section: %s (可选: %s)" % (section, ", ".join(cp.sections()))
    if not key:
        return json.dumps(dict(cp.items(section)), ensure_ascii=False, indent=2)
    if not cp.has_option(section, key):
        avail = ", ".join(dict(cp.items(section)).keys())
        return "[ini_query] 无此 key: %s (可选: %s)" % (key, avail)
    return cp.get(section, key)


def ini_to_json(args, ctx):
    raw = args.get("ini") or ""
    try:
        cp = _parse_ini(raw)
    except Exception as e:
        return "[ini_to_json] INI 解析失败: %s" % e
    out = {}
    if cp.defaults():
        out[""] = dict(cp.defaults())
    for s in cp.sections():
        out[s] = dict(cp.items(s))
    return json.dumps(out, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# license_list
# ---------------------------------------------------------------------------
_LICENSES = [
    {"id": "MIT", "name": "MIT License", "category": "permissive", "osi": True,
     "note": "极简宽松, 只需保留版权与许可声明"},
    {"id": "Apache-2.0", "name": "Apache License 2.0", "category": "permissive", "osi": True,
     "note": "宽松, 含专利授权与声明要求"},
    {"id": "BSD-2-Clause", "name": "BSD 2-Clause", "category": "permissive", "osi": True,
     "note": "宽松, 二条款"},
    {"id": "BSD-3-Clause", "name": "BSD 3-Clause", "category": "permissive", "osi": True,
     "note": "宽松, 三条款含禁止背书"},
    {"id": "ISC", "name": "ISC License", "category": "permissive", "osi": True,
     "note": "类 MIT 的简化宽松许可"},
    {"id": "0BSD", "name": "BSD Zero Clause", "category": "permissive", "osi": True,
     "note": "无署名要求的宽松许可"},
    {"id": "Unlicense", "name": "The Unlicense", "category": "permissive", "osi": True,
     "note": "公共领域奉献"},
    {"id": "CC0-1.0", "name": "Creative Commons Zero", "category": "permissive", "osi": False,
     "note": "公共领域奉献(文档/数据常用)"},
    {"id": "Zlib", "name": "zlib License", "category": "permissive", "osi": True,
     "note": "宽松, 源码改动需注明"},
    {"id": "WTFPL", "name": "Do What The F* You Want", "category": "permissive", "osi": False,
     "note": "极其宽松, 无约束"},
    {"id": "Python-2.0", "name": "Python License 2.0", "category": "permissive", "osi": True,
     "note": "Python 语言许可"},
    {"id": "MPL-2.0", "name": "Mozilla Public License 2.0", "category": "weak-copyleft", "osi": True,
     "note": "文件级弱 copyleft, 可商用"},
    {"id": "EPL-2.0", "name": "Eclipse Public License 2.0", "category": "weak-copyleft", "osi": True,
     "note": "文件级弱 copyleft"},
    {"id": "CDDL-1.0", "name": "Common Development and Distribution License", "category": "weak-copyleft", "osi": True,
     "note": "文件级弱 copyleft (Sun/Oracle)"},
    {"id": "LGPL-2.1", "name": "GNU Lesser GPL 2.1", "category": "copyleft", "osi": True,
     "note": "库级 copyleft, 动态链接较友好"},
    {"id": "LGPL-3.0", "name": "GNU Lesser GPL 3.0", "category": "copyleft", "osi": True,
     "note": "库级 copyleft, 含专利条款"},
    {"id": "GPL-2.0", "name": "GNU General Public License 2.0", "category": "copyleft", "osi": True,
     "note": "强 copyleft, 衍生整体开源"},
    {"id": "GPL-3.0", "name": "GNU General Public License 3.0", "category": "copyleft", "osi": True,
     "note": "强 copyleft, 含专利与硬件限制"},
    {"id": "AGPL-3.0", "name": "GNU Affero GPL 3.0", "category": "copyleft", "osi": True,
     "note": "强 copyleft, 网络服务也算分发"},
    {"id": "EUPL-1.2", "name": "European Union Public Licence", "category": "copyleft", "osi": True,
     "note": "欧盟官方, 多语言等效"},
    {"id": "MS-PL", "name": "Microsoft Public License", "category": "weak-copyleft", "osi": True,
     "note": "微软弱 copyleft"},
    {"id": "Artistic-2.0", "name": "Artistic License 2.0", "category": "copyleft", "osi": True,
     "note": "Perl 社区许可"},
]


def license_list(args, ctx):
    cat = (args.get("category") or "").strip().lower()
    q = (args.get("query") or "").strip().lower()
    items = _LICENSES
    if cat:
        items = [l for l in items if l["category"] == cat]
    if q:
        items = [l for l in items if q in l["id"].lower() or q in l["name"].lower() or q in l["note"].lower()]
    if args.get("format") == "json":
        return json.dumps(items, ensure_ascii=False, indent=2)
    lines = ["| 标识 | 名称 | 类别 | OSI | 说明 |", "|---|---|---|---|---|"]
    for l in items:
        lines.append("| %s | %s | %s | %s | %s |" % (
            l["id"], l["name"], l["category"], "✓" if l["osi"] else "", l["note"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# json_schema_lint
# ---------------------------------------------------------------------------
_VALID_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def _lint_node(node, problems, path):
    if not isinstance(node, dict):
        if path != "$" and not isinstance(node, (list,)):
            pass
        return
    t = node.get("type")
    if t is not None:
        if isinstance(t, list):
            for x in t:
                if x not in _VALID_TYPES:
                    problems.append((path, "type 取值非法: %s" % x))
        elif t not in _VALID_TYPES:
            problems.append((path, "type 取值非法: %s" % t))
    if "properties" in node:
        if not isinstance(node["properties"], dict):
            problems.append((path, "properties 必须是对象"))
        else:
            for k, v in node["properties"].items():
                _lint_node(v, problems, "%s.properties.%s" % (path, k))
    if "required" in node:
        if not isinstance(node["required"], list) or not all(isinstance(x, str) for x in node["required"]):
            problems.append((path, "required 必须是字符串数组"))
        elif node.get("type") not in (None, "object"):
            problems.append((path, "required 仅在 type=object 时有效"))
    if "items" in node:
        if node.get("type") not in (None, "array"):
            problems.append((path, "items 仅在 type=array 时有效"))
        elif isinstance(node["items"], dict):
            _lint_node(node["items"], problems, "%s.items" % path)
    if "enum" in node and not isinstance(node["enum"], list):
        problems.append((path, "enum 必须是数组"))
    if "$ref" in node and not isinstance(node["$ref"], str):
        problems.append((path, "$ref 必须是字符串"))
    if "additionalProperties" in node:
        ap = node["additionalProperties"]
        if not isinstance(ap, (bool, dict)):
            problems.append((path, "additionalProperties 必须是布尔或对象"))


def json_schema_lint(args, ctx):
    raw = args.get("schema") or ""
    try:
        s = json.loads(raw)
    except Exception as e:
        return "[json_schema_lint] Schema JSON 解析失败: %s" % e
    if not isinstance(s, dict):
        return "[json_schema_lint] 顶层 Schema 必须是对象"
    problems = []
    _lint_node(s, problems, "$")
    if not problems:
        return "[json_schema_lint] 校验通过: 未发现语法问题。"
    return ("[json_schema_lint] 发现 %d 处问题:\n" % len(problems) +
            "\n".join("- %s: %s" % (p[0], p[1]) for p in problems))

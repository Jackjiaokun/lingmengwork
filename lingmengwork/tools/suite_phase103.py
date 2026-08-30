# -*- coding: utf-8 -*-
"""Phase 103 工具套件: 数据工程 / 安全合规续集 (零依赖优雅降级).

工具:
- yaml_to_json   : YAML(极简缩进式子集) -> JSON
- json_to_yaml   : JSON -> YAML(缩进式)
- xml_to_csv     : XML -> CSV(按重复同名子元素展平)
- toml_to_yaml   : TOML -> YAML
- license_compat : 许可证兼容矩阵检查
- dep_outdated   : 依赖过时/未固定离线启发式检查
- file_classify  : 文件类型分类(魔数签名)

所有工具纯计算 / 只读, 失败以 [tool] 前缀回灌模型, 不直接写文件.
"""
import os
import re
import json
import io

try:
    import tomllib
except Exception:
    tomllib = None

from lingmengwork.tools import fs


def _resolve(ctx, p):
    if not p:
        return p
    try:
        return fs.resolve_path(p, ctx or {})
    except Exception:
        base = (ctx or {}).get("cwd") or ""
        return p if os.path.isabs(p) else (os.path.join(base, p) if base else p)


def _read_input(ctx, args):
    if args.get("file"):
        rp = _resolve(ctx, args["file"])
        return open(rp, "r", encoding="utf-8", errors="replace").read()
    return args.get("text") or ""


# --------------------------------------------------------------------------
# 极简 YAML 解析 (缩进式子集)
# --------------------------------------------------------------------------
def _yaml_split_top(s, sep):
    depth = 0
    cur = ""
    out = []
    for ch in s:
        if ch in "[{\"":
            depth += 1
            cur += ch
        elif ch in "]}":
            depth -= 1
            cur += ch
        elif ch == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur != "":
        out.append(cur)
    return out


def _strip_inline_comment(s):
    out = []
    in_q = False
    q = ""
    for ch in s:
        if ch in "\"'":
            if in_q and ch == q:
                in_q = False
            elif not in_q:
                in_q = True
                q = ch
            out.append(ch)
        elif ch == "#" and not in_q:
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()

def _yaml_scalar(s):
    s = _strip_inline_comment(s.strip())
    if s == "" or s == "~" or s.lower() == "null":
        return None
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if (len(s) >= 2) and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_yaml_scalar(x) for x in _yaml_split_top(inner, ",")]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        d = {}
        if inner:
            for part in _yaml_split_top(inner, ","):
                k, _, v = part.partition(":")
                d[k.strip()] = _yaml_scalar(v)
        return d
    try:
        return int(s)
    except Exception:
        pass
    try:
        return float(s)
    except Exception:
        pass
    return s


def _yaml_looks_map(s):
    if s.startswith(("[", "{", '"', "'")):
        return False
    if ":" not in s:
        return False
    return True


def _yaml_load(text):
    raw = []
    for ln in text.splitlines():
        st = ln.strip()
        if not st or st.startswith("#"):
            continue
        indent = len(ln) - len(ln.lstrip(" "))
        raw.append((indent, st))
    if not raw:
        return {}

    def parse(i, indent):
        first = raw[i][1]
        if first.startswith("- "):
            seq = []
            while i < len(raw) and raw[i][0] == indent and raw[i][1].startswith("- "):
                item = raw[i][1][2:].strip()
                if item == "":
                    if i + 1 < len(raw) and raw[i + 1][0] > indent:
                        node, i = parse(i + 1, raw[i + 1][0])
                        seq.append(node)
                    else:
                        seq.append(None)
                        i += 1
                elif _yaml_looks_map(item):
                    m, i = _parse_map_item(i, indent, item)
                    seq.append(m)
                else:
                    seq.append(_yaml_scalar(item))
                    i += 1
            return seq, i
        else:
            d = {}
            while i < len(raw) and raw[i][0] == indent:
                content = raw[i][1]
                if content.startswith("- "):
                    break
                k, _, v = content.partition(":")
                k = k.strip()
                v = _strip_inline_comment(v.strip())
                if v == "":
                    if i + 1 < len(raw) and raw[i + 1][0] > indent:
                        node, i = parse(i + 1, raw[i + 1][0])
                        d[k] = node
                    else:
                        d[k] = None
                        i += 1
                else:
                    d[k] = _yaml_scalar(v)
                    i += 1
            return d, i

    def _parse_map_item(i, indent, item):
        k, _, v = item.partition(":")
        k = k.strip()
        v = _strip_inline_comment(v.strip())
        m = {}
        if v == "":
            if i + 1 < len(raw) and raw[i + 1][0] > indent:
                node, i = parse(i + 1, raw[i + 1][0])
                m[k] = node
            else:
                m[k] = None
                i += 1
        else:
            m[k] = _yaml_scalar(v)
            i += 1
        while i < len(raw) and raw[i][0] > indent and not raw[i][1].startswith("- "):
            content = raw[i][1]
            k2, _, v2 = content.partition(":")
            k2 = k2.strip()
            v2 = _strip_inline_comment(v2.strip())
            if v2 == "":
                if i + 1 < len(raw) and raw[i + 1][0] > raw[i][0]:
                    node, i = parse(i + 1, raw[i + 1][0])
                    m[k2] = node
                else:
                    m[k2] = None
                    i += 1
            else:
                m[k2] = _yaml_scalar(v2)
                i += 1
        return m, i

    node, _ = parse(0, raw[0][0])
    return node


# --------------------------------------------------------------------------
# JSON -> YAML 序列化
# --------------------------------------------------------------------------
def _yaml_scalar_str(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "":
        return '""'
    if s[0] in "[{\"'#" or s.endswith(":") or ":" in s or "#" in s or "\n" in s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _yaml_serialize(node, indent):
    pad = "  " * indent
    if isinstance(node, dict):
        if not node:
            return "{}"
        lines = []
        for k, v in node.items():
            if isinstance(v, dict):
                if not v:
                    lines.append("%s%s: {}" % (pad, k))
                else:
                    lines.append("%s%s:" % (pad, k))
                    lines.append(_yaml_serialize(v, indent + 1))
            elif isinstance(v, list):
                if not v:
                    lines.append("%s%s: []" % (pad, k))
                else:
                    lines.append("%s%s:" % (pad, k))
                    lines.append(_yaml_seq(v, indent + 1))
            else:
                lines.append("%s%s: %s" % (pad, k, _yaml_scalar_str(v)))
        return "\n".join(lines)
    elif isinstance(node, list):
        return _yaml_seq(node, indent)
    else:
        return _yaml_scalar_str(node)


def _yaml_seq(seq, indent):
    pad = "  " * indent
    lines = []
    for it in seq:
        if isinstance(it, dict):
            if not it:
                lines.append("%s- {}" % pad)
            else:
                sub = _yaml_serialize(it, indent + 1)
                first, nl, rest = sub.partition("\n")
                if nl:
                    lines.append("%s- %s" % (pad, first))
                    lines.append(rest)
                else:
                    lines.append("%s- %s" % (pad, first))
        elif isinstance(it, list):
            if not it:
                lines.append("%s- []" % pad)
            else:
                lines.append("%s- %s" % (pad, _yaml_seq(it, indent + 1)))
        else:
            lines.append("%s- %s" % (pad, _yaml_scalar_str(it)))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 许可证兼容矩阵
# --------------------------------------------------------------------------
_STRONG = {"AGPL", "GPL"}
_WEAK = {"LGPL", "MPL"}
_PERM = {"Apache-2.0", "MIT", "BSD", "ISC", "PublicDomain", "CC"}
_PROP = {"Proprietary"}


def _norm_license(s):
    u = (s or "").upper()
    if "AGPL" in u:
        return "AGPL"
    if "LGPL" in u:
        return "LGPL"
    if "GPL" in u:
        return "GPL"
    if "MPL" in u or "MOZILLA" in u:
        return "MPL"
    if "APACHE" in u:
        return "Apache-2.0"
    if "MIT" in u:
        return "MIT"
    if "BSD" in u:
        return "BSD"
    if "ISC" in u:
        return "ISC"
    if "UNLICENSE" in u or "PUBLIC DOMAIN" in u:
        return "PublicDomain"
    if "CC0" in u or "CREATIVE COMMONS" in u:
        return "CC"
    if "PROPRIETARY" in u or "COMMERCIAL" in u:
        return "Proprietary"
    return (s or "").strip() or "Unknown"


def _compat(a, b):
    if a == b:
        return "兼容", "同类许可证"
    if a in _STRONG or b in _STRONG:
        strong = a if a in _STRONG else b
        other = b if a in _STRONG else a
        if other in _PROP:
            return "不兼容", "强 Copyleft(%s) 与专有许可冲突" % strong
        return "需谨慎", "强 Copyleft 具传染性, 组合后整体须以 %s 开源" % strong
    if a in _WEAK or b in _WEAK:
        weak = a if a in _WEAK else b
        other = b if a in _WEAK else a
        if other in _PROP:
            return "不兼容", "弱 Copyleft(%s) 与专有许可冲突" % weak
        return "兼容", "弱 Copyleft 仅文件级, 可组合"
    if a in _PROP or b in _PROP:
        other = b if a in _PROP else a
        if other in _PROP:
            return "兼容", "专有间组合需各自商业授权"
        return "需谨慎", "专有许可与 %s 组合需商业授权/隔离" % other
    return "兼容", "均为宽松许可证"


_DEPRECATED = {
    "pip": "pip 旧版",
    "python2": "Python 2 相关",
    "urllib2": "Python 2 仅",
    "mock": "建议用 unittest.mock",
    "nose": "已弃用, 建议 pytest",
    "typing": "3.5+ 已内置",
    "enum34": "3.4+ 已内置 enum",
    "configparser2": "用标准库 configparser",
}


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------
def yaml_to_json(args, ctx):
    try:
        text = _read_input(ctx, args)
        if not text.strip():
            return "[yaml_to_json] 输入为空."
        obj = _yaml_load(text)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception as e:
        return "[yaml_to_json] 解析失败: %s" % e


def json_to_yaml(args, ctx):
    try:
        text = _read_input(ctx, args)
        if not text.strip():
            return "[json_to_yaml] 输入为空."
        obj = json.loads(text)
        return _yaml_serialize(obj, 0)
    except Exception as e:
        return "[json_to_yaml] 转换失败: %s" % e


def xml_to_csv(args, ctx):
    import xml.etree.ElementTree as ET
    try:
        text = _read_input(ctx, args)
        if not text.strip():
            return "[xml_to_csv] 输入为空."
        root = ET.fromstring(text)
        children = list(root)
        if not children:
            return "[xml_to_csv] 根无子元素."
        row_tag = args.get("row_tag")
        if row_tag:
            rows = [c for c in children if c.tag == row_tag]
        else:
            from collections import Counter
            cnt = Counter(c.tag for c in children)
            row_tag = cnt.most_common(1)[0][0]
            rows = [c for c in children if c.tag == row_tag]
        if not rows:
            return "[xml_to_csv] 未找到行元素(%s)." % row_tag
        cols = []
        for r in rows:
            for a in r.attrib:
                if a not in cols:
                    cols.append(a)
            for sub in list(r):
                if sub.tag not in cols:
                    cols.append(sub.tag)
        import csv
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            vals = []
            for c in cols:
                if c in r.attrib:
                    vals.append(r.attrib[c])
                else:
                    sub = r.find(c)
                    vals.append(sub.text if (sub is not None and sub.text is not None) else "")
            w.writerow(vals)
        return buf.getvalue()
    except Exception as e:
        return "[xml_to_csv] 转换失败: %s" % e


def toml_to_yaml(args, ctx):
    try:
        if tomllib is None:
            return "[toml_to_yaml] 当前 Python 不支持 tomllib (需 3.11+)."
        text = _read_input(ctx, args)
        if not text.strip():
            return "[toml_to_yaml] 输入为空."
        data = tomllib.loads(text)
        return _yaml_serialize(data, 0)
    except Exception as e:
        return "[toml_to_yaml] 转换失败: %s" % e


def license_compat(args, ctx):
    try:
        licenses = []
        if args.get("primary"):
            licenses.append(args["primary"])
        if args.get("deps"):
            for x in re.split(r"[\n,]", args["deps"]):
                x = x.strip()
                if x:
                    licenses.append(x)
        if args.get("licenses"):
            for x in re.split(r"[\n,]", args["licenses"]):
                x = x.strip()
                if x:
                    licenses.append(x)
        if len(licenses) < 2:
            return "[license_compat] 需至少 2 个许可证 (primary+deps 或 licenses 列表)."
        norm = [_norm_license(x) for x in licenses]
        lines = ["[license_compat] 归一化: " + " | ".join(norm)]
        verdicts = []
        for i in range(len(norm)):
            for j in range(i + 1, len(norm)):
                st, why = _compat(norm[i], norm[j])
                lines.append("  %s × %s -> %s (%s)" % (norm[i], norm[j], st, why))
                verdicts.append(st)
        if "不兼容" in verdicts:
            summary = "结论: 存在不兼容组合, 不可合并分发"
        elif "需谨慎" in verdicts:
            summary = "结论: 可合并但需谨慎(传染性/授权要求)"
        else:
            summary = "结论: 全部兼容"
        lines.append(summary)
        return "\n".join(lines)
    except Exception as e:
        return "[license_compat] 失败: %s" % e


def _parse_reqs(text):
    deps = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("-"):
            continue
        s = re.split(r"[;#]", s)[0].strip()
        if not s:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(==|>=|<=|~=|!=|>)?\s*([0-9A-Za-z.\-\*]*)", s)
        if not m:
            continue
        deps.append((m.group(1), m.group(2), m.group(3)))
    return deps


def dep_outdated(args, ctx):
    try:
        rp = _resolve(ctx, args.get("path") or "")
        if not rp or not os.path.exists(rp):
            return "[dep_outdated] 文件不存在: %s" % (args.get("path"))
        text = open(rp, "r", encoding="utf-8", errors="replace").read()
        low = text.lower()
        if ("dependencies" in low) and ("[project]" in text or "[tool.poetry]" in text or "package.json" in rp):
            try:
                j = json.loads(text)
                deps = list(j.get("dependencies", {}).items()) + list(j.get("devDependencies", {}).items())
                deps = [(k, "", (v if isinstance(v, str) else "")) for k, v in deps]
            except Exception:
                deps = _parse_reqs(text)
        else:
            deps = _parse_reqs(text)
        if not deps:
            return "[dep_outdated] 未解析到依赖: %s" % rp
        flags = []
        for name, op, ver in deps:
            nm = name.lower()
            if not op:
                flags.append("  ⚠ %s : 未固定版本(建议 == 锁定)" % name)
            elif nm in _DEPRECATED:
                flags.append("  ⚠ %s : %s" % (name, _DEPRECATED[nm]))
            elif ver and re.match(r"^0\.", ver):
                flags.append("  ℹ %s : 0.x 版本(不稳定 API)" % name)
        if not flags:
            return "[dep_outdated] %s : 未发现明显过时/未固定项 (%d 个依赖)." % (rp, len(deps))
        return "[dep_outdated] %s (%d 个依赖, %d 项提示):\n" % (rp, len(deps), len(flags)) + "\n".join(flags)
    except Exception as e:
        return "[dep_outdated] 失败: %s" % e


_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png", "PNG 图像"),
    (b"\xff\xd8\xff", "image/jpeg", "JPEG 图像"),
    (b"GIF87a", "image/gif", "GIF 图像"),
    (b"GIF89a", "image/gif", "GIF 图像"),
    (b"%PDF-", "application/pdf", "PDF 文档"),
    (b"PK\x03\x04", "application/zip", "ZIP/Office 文档"),
    (b"PK\x05\x06", "application/zip", "ZIP(空)"),
    (b"\x1f\x8b", "application/gzip", "GZIP 压缩"),
    (b"7z\xbc\xaf'\x1c", "application/x-7z", "7-Zip 压缩"),
    (b"Rar!\x1a\x07", "application/x-rar", "RAR 压缩"),
    (b"SQLite format 3\x00", "application/x-sqlite3", "SQLite 数据库"),
    (b"\x7fELF", "application/x-elf", "ELF 可执行"),
    (b"\xd0\xcf\x11\xe0", "application/x-ole", "OLE/旧 Office 文档"),
    (b"\xef\xbb\xbf", "text/utf-8-bom", "UTF-8(BOM) 文本"),
    (b"BM", "image/bmp", "BMP 图像"),
    (b"\x00\x00\x01\x00", "image/x-icon", "ICO 图标"),
]


def file_classify(args, ctx):
    try:
        rp = _resolve(ctx, args.get("file") or "")
        if not rp or not os.path.exists(rp):
            return "[file_classify] 文件不存在: %s" % (args.get("file"))
        with open(rp, "rb") as f:
            head = f.read(64)
        for sig, mime, desc in _SIGNATURES:
            if head.startswith(sig):
                return "[file_classify] %s -> %s (%s, %d 字节)" % (rp, mime, desc, os.path.getsize(rp))
        try:
            txt = head.decode("utf-8")
            if txt.lstrip().startswith("<?xml") or txt.lstrip().startswith("<"):
                return "[file_classify] %s -> application/xml (XML 文本)" % rp
            if txt.lstrip().startswith("{") or txt.lstrip().startswith("["):
                return "[file_classify] %s -> application/json (JSON 文本)" % rp
            return "[file_classify] %s -> text/plain (文本, %d 字节)" % (rp, os.path.getsize(rp))
        except Exception:
            return "[file_classify] %s -> application/octet-stream (二进制, %d 字节)" % (rp, os.path.getsize(rp))
    except Exception as e:
        return "[file_classify] 失败: %s" % e

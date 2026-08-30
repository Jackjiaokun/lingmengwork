# -*- coding: utf-8 -*-
"""Phase 102 工具集: 数据工程 / 安全合规续集 (零依赖, 优雅降级).

工具 (7):
  - xml_to_json    : XML -> JSON (读出)
  - json_to_sql    : JSON -> SQL INSERT (写出文件, 写类)
  - toml_to_json   : TOML -> JSON (读出)
  - json_patch     : 应用 RFC6902 风格 JSON Patch (读出)
  - secret_mask    : 敏感信息掩码 (写出文件, 写类)
  - sbom_gen       : 软件物料清单 (SBOM) 生成 (读出)
  - dep_graph      : 模块依赖图 (读出)

所有工具签名 func(args, ctx) -> str, 失败时以 [tool] 前缀回灌模型, 不抛异常。
路径经 ctx 根目录防护 (域约束): 越界返回空路径并提示。
"""
import os
import re
import json
import sys
import time

try:
    import xml.etree.ElementTree as ET
except Exception:  # pragma: no cover
    ET = None

try:
    import tomllib  # Python 3.11+
except Exception:  # pragma: no cover
    tomllib = None


# ----------------------------------------------------------------------------
# 路径解析 (域防护)
# ----------------------------------------------------------------------------
def _resolve(ctx, path):
    p = (path or "").strip()
    if not p:
        return ""
    if os.path.isabs(p):
        rp = os.path.normpath(p)
    else:
        cwd = ((ctx or {}).get("cwd") or "")
        roots = (ctx or {}).get("roots") or []
        if not cwd and roots:
            cwd = roots[0]
        if not cwd:
            cwd = "."
        rp = os.path.normpath(os.path.join(cwd, p))
    roots = (ctx or {}).get("roots") or []
    if roots:
        ok = False
        for r in roots:
            nr = os.path.normpath(r)
            if rp == nr or rp.startswith(nr + os.sep):
                ok = True
                break
        if not ok:
            return ""
    return rp


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _write_text(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ----------------------------------------------------------------------------
# xml_to_json
# ----------------------------------------------------------------------------
def _etree_to_dict(el):
    d = {}
    children = list(el)
    if el.attrib:
        d["@attributes"] = dict(el.attrib)
    if children:
        for ch in children:
            cd = _etree_to_dict(ch)
            tag = ch.tag
            if tag in d:
                if not isinstance(d[tag], list):
                    d[tag] = [d[tag]]
                d[tag].append(cd)
            else:
                d[tag] = cd
    text = (el.text or "").strip()
    if text:
        if children or el.attrib:
            d["#text"] = text
        else:
            return text
    if not d:
        return text or ""
    return d


def xml_to_json(args, ctx):
    if ET is None:
        return "[xml_to_json] 当前环境缺少 xml.etree.ElementTree 支持."
    xml_str = (args.get("xml") or "").strip()
    path = _resolve(ctx, args.get("file") or "")
    if not xml_str and path:
        try:
            xml_str = _read_text(path)
        except Exception as e:
            return "[xml_to_json] 读取文件失败: %s" % e
    if not xml_str:
        return "[xml_to_json] 需要 xml 字符串或 file 路径."
    try:
        root = ET.fromstring(xml_str)
    except Exception as e:
        return "[xml_to_json] XML 解析失败: %s" % e
    data = {root.tag: _etree_to_dict(root)}
    try:
        out = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return "[xml_to_json] 序列化失败: %s" % e
    out_file = _resolve(ctx, args.get("out_file") or "")
    if out_file:
        try:
            _write_text(out_file, out)
            return "[xml_to_json] 已写出 %d 字符 -> %s" % (len(out), out_file)
        except Exception as e:
            return "[xml_to_json] 写出失败: %s" % e
    return out


# ----------------------------------------------------------------------------
# json_to_sql
# ----------------------------------------------------------------------------
def _sql_val(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("'", "''")
    return "'%s'" % s


def json_to_sql(args, ctx):
    js = (args.get("json") or "").strip()
    path = _resolve(ctx, args.get("file") or "")
    if not js and path:
        try:
            js = _read_text(path)
        except Exception as e:
            return "[json_to_sql] 读取文件失败: %s" % e
    if not js:
        return "[json_to_sql] 需要 json 字符串或 file 路径."
    table = (args.get("table") or "").strip() or "t"
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table):
        return "[json_to_sql] 表名非法: %s" % table
    try:
        data = json.loads(js)
    except Exception as e:
        return "[json_to_sql] JSON 解析失败: %s" % e
    rows = data if isinstance(data, list) else [data]
    if not rows:
        return "[json_to_sql] 无数据行."
    cols = []
    for r in rows:
        if isinstance(r, dict):
            for k in r.keys():
                if k not in cols:
                    cols.append(k)
    stmts = []
    for r in rows:
        if not isinstance(r, dict):
            return "[json_to_sql] 仅支持对象/对象数组."
        vals = []
        for c in cols:
            vals.append(_sql_val(r.get(c)))
        stmts.append("INSERT INTO %s (%s) VALUES (%s);" % (
            table, ", ".join(cols), ", ".join(vals)))
    out = "\n".join(stmts)
    out_file = _resolve(ctx, args.get("out_file") or "")
    if out_file:
        try:
            _write_text(out_file, out)
            return "[json_to_sql] 已写出 %d 条 INSERT -> %s" % (len(stmts), out_file)
        except Exception as e:
            return "[json_to_sql] 写出失败: %s" % e
    return out


# ----------------------------------------------------------------------------
# toml_to_json
# ----------------------------------------------------------------------------
def toml_to_json(args, ctx):
    if tomllib is None:
        return "[toml_to_json] 当前 Python 版本缺少 tomllib (需 3.11+)."
    toml_str = (args.get("toml") or "").strip()
    path = _resolve(ctx, args.get("file") or "")
    if not toml_str and path:
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            toml_str = None
        except Exception as e:
            return "[toml_to_json] 读取/解析失败: %s" % e
    if toml_str:
        try:
            data = tomllib.loads(toml_str)
        except Exception as e:
            return "[toml_to_json] TOML 解析失败: %s" % e
    try:
        out = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return "[toml_to_json] 序列化失败: %s" % e
    out_file = _resolve(ctx, args.get("out_file") or "")
    if out_file:
        try:
            _write_text(out_file, out)
            return "[toml_to_json] 已写出 %d 字符 -> %s" % (len(out), out_file)
        except Exception as e:
            return "[toml_to_json] 写出失败: %s" % e
    return out


# ----------------------------------------------------------------------------
# json_patch  (RFC6902 风格子集: add/replace/remove/test/move/copy)
# ----------------------------------------------------------------------------
def _split_path(ptr):
    if ptr == "" or ptr == "/":
        return []
    parts = []
    for seg in ptr.split("/"):
        if seg == "":
            continue
        parts.append(seg.replace("~1", "/").replace("~0", "~"))
    return parts


def _get_path(doc, parts):
    cur = doc
    for p in parts:
        if isinstance(cur, list):
            cur = cur[int(p)]
        else:
            cur = cur[p]
    return cur


def _set_path(doc, parts, value):
    cur = doc
    for p in parts[:-1]:
        cur = cur[p] if isinstance(cur, dict) else cur[int(p)]
    last = parts[-1]
    if isinstance(cur, list):
        idx = int(last)
        if idx < 0:
            idx = len(cur) + idx
        if idx >= len(cur):
            cur.append(value)
        else:
            cur[idx] = value
    else:
        cur[last] = value


def _del_path(doc, parts):
    cur = doc
    for p in parts[:-1]:
        cur = cur[p] if isinstance(cur, dict) else cur[int(p)]
    last = parts[-1]
    if isinstance(cur, list):
        cur.pop(int(last))
    else:
        del cur[last]


def json_patch(args, ctx):
    js = (args.get("json") or "").strip()
    path = _resolve(ctx, args.get("file") or "")
    if not js and path:
        try:
            js = _read_text(path)
        except Exception as e:
            return "[json_patch] 读取文件失败: %s" % e
    if not js:
        return "[json_patch] 需要 json 字符串或 file 路径."
    patch = (args.get("patch") or "").strip()
    if not patch:
        return "[json_patch] 需要 patch (JSON 操作数组)."
    try:
        doc = json.loads(js)
        ops = json.loads(patch)
    except Exception as e:
        return "[json_patch] JSON 解析失败: %s" % e
    if not isinstance(ops, list):
        return "[json_patch] patch 必须是操作数组."
    try:
        for op in ops:
            o = op.get("op")
            p = _split_path(op.get("path", ""))
            if o == "add":
                _set_path(doc, p, op.get("value"))
            elif o == "replace":
                _set_path(doc, p, op.get("value"))
            elif o == "remove":
                _del_path(doc, p)
            elif o == "test":
                cur = _get_path(doc, p)
                if cur != op.get("value"):
                    return "[json_patch] test 失败于路径 %s." % op.get("path")
            elif o == "move":
                frm = _split_path(op.get("from", ""))
                val = _get_path(doc, frm)
                _del_path(doc, frm)
                _set_path(doc, p, val)
            elif o == "copy":
                frm = _split_path(op.get("from", ""))
                val = _get_path(doc, frm)
                _set_path(doc, p, val)
            else:
                return "[json_patch] 不支持的 op: %s" % o
    except Exception as e:
        return "[json_patch] 应用失败: %s" % e
    out = json.dumps(doc, ensure_ascii=False, indent=2)
    out_file = _resolve(ctx, args.get("out_file") or "")
    if out_file:
        try:
            _write_text(out_file, out)
            return "[json_patch] 已写出 %d 字符 -> %s" % (len(out), out_file)
        except Exception as e:
            return "[json_patch] 写出失败: %s" % e
    return out


# ----------------------------------------------------------------------------
# secret_mask
# ----------------------------------------------------------------------------
_SECRET_MASK_PATTERNS = [
    ("AWS_ACCESS_KEY", r"AKIA[0-9A-Z]{16}"),
    ("AWS_SECRET", r"(?:aws_secret_access_key|aws_secret)[\"'\s:=]+[\"']?[A-Za-z0-9/+=]{40}"),
    ("PRIVATE_KEY", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("GITHUB_TOKEN", r"gh[pousr]_[0-9A-Za-z]{36,}"),
    ("GITLAB_TOKEN", r"glpat-[0-9A-Za-z_\-]{20,}"),
    ("GOOGLE_API", r"AIza[0-9A-Za-z_\-]{35}"),
    ("SLACK_TOKEN", r"xox[baprs]-[0-9A-Za-z\-]{10,}"),
    ("STRIPE_KEY", r"sk_live_[0-9a-zA-Z]{16,}"),
    ("JWT", r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    ("PASSWORD_FIELD", r"(?:password|passwd|pwd|secret|token|api_key|apikey|access_token|client_secret)[\"'\s:=]+[\"'][^\"'\n]{6,}[\"']"),
    ("DB_URL_WITH_PWD", r"(?:postgres|postgresql|mysql|mongodb|redis|amqp)://[^\s:/@]+:[^\s:@/]+@"),
]


def _mask_secrets(text):
    spans = []
    for _name, pat in _SECRET_MASK_PATTERNS:
        for m in re.finditer(pat, text):
            spans.append((m.start(), m.end()))
    if not spans:
        return text, 0
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out = []
    last = 0
    for s, e in merged:
        out.append(text[last:s])
        out.append("[REDACTED]")
        last = e
    out.append(text[last:])
    return "".join(out), len(merged)


def secret_mask(args, ctx):
    text = (args.get("text") or "").strip()
    path = _resolve(ctx, args.get("file") or "")
    if not text and path:
        try:
            text = _read_text(path)
        except Exception as e:
            return "[secret_mask] 读取文件失败: %s" % e
    if not text:
        return "[secret_mask] 需要 text 或 file 路径."
    masked, n = _mask_secrets(text)
    out_file = _resolve(ctx, args.get("out_file") or "")
    if out_file:
        try:
            _write_text(out_file, masked)
            return "[secret_mask] 已掩码 %d 处敏感信息 -> %s" % (n, out_file)
        except Exception as e:
            return "[secret_mask] 写出失败: %s" % e
    if n == 0:
        return "[secret_mask] 未发现敏感信息 (原文 %d 字符)." % len(text)
    return "[secret_mask] 已掩码 %d 处:\n%s" % (n, masked)


# ----------------------------------------------------------------------------
# sbom_gen  (软件物料清单)
# ----------------------------------------------------------------------------
_MANIFEST_HANDLERS = {
    "package.json": "_sbom_npm",
    "package-lock.json": "_sbom_npm_lock",
    "requirements.txt": "_sbom_pip_req",
    "pyproject.toml": "_sbom_pyproject",
    "Pipfile": "_sbom_pipfile",
    "go.mod": "_sbom_gomod",
    "pom.xml": "_sbom_maven",
    "build.gradle": "_sbom_gradle",
    "Cargo.toml": "_sbom_cargo",
    "Gemfile": "_sbom_gemfile",
    "composer.json": "_sbom_composer",
}


def _sbom_npm(text):
    try:
        d = json.loads(text)
    except Exception:
        return []
    comps = []
    for sec in ("dependencies", "devDependencies", "peerDependencies"):
        for k, v in (d.get(sec) or {}).items():
            comps.append({"name": k, "version": str(v), "scope": sec})
    return comps


def _sbom_npm_lock(text):
    try:
        d = json.loads(text)
    except Exception:
        return []
    comps = []
    pkgs = (d.get("packages") or {})
    for k, v in pkgs.items():
        if k == "" or not isinstance(k, str):
            continue
        name = k.split("node_modules/")[-1]
        if name:
            comps.append({"name": name, "version": str(v.get("version", "")), "scope": "lock"})
    return comps


def _sbom_pip_req(text):
    comps = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(?:[=<>!~]=?\s*([0-9A-Za-z.\-\*]+))?", line)
        if m and m.group(1).lower() not in ("pip", "setuptools", "wheel"):
            comps.append({"name": m.group(1), "version": m.group(2) or "", "scope": "pip"})
    return comps


def _sbom_pyproject(text):
    comps = []
    if tomllib is None:
        return comps
    try:
        d = tomllib.loads(text)
    except Exception:
        return comps
    for sec in ("project.dependencies", "project.optional-dependencies"):
        parts = sec.split(".")
        cur = d
        for p in parts:
            cur = cur.get(p, {}) if isinstance(cur, dict) else {}
        if isinstance(cur, list):
            for item in cur:
                m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(?:[<>=!~]+\s*([0-9A-Za-z.\-\*]+))?", item)
                if m:
                    comps.append({"name": m.group(1), "version": m.group(2) or "", "scope": "pyproject"})
        elif isinstance(cur, dict):
            for k, v in cur.items():
                comps.append({"name": k, "version": str(v) if v else "", "scope": "pyproject"})
    return comps


def _sbom_pipfile(text):
    comps = []
    try:
        d = json.loads(text)
    except Exception:
        return comps
    for sec in ("packages", "dev-packages"):
        for k, v in (d.get(sec) or {}).items():
            ver = v.get("version", "") if isinstance(v, dict) else str(v)
            comps.append({"name": k, "version": ver, "scope": "pipfile"})
    return comps


def _sbom_gomod(text):
    comps = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^([A-Za-z0-9_.\-/]+)\s+v([0-9A-Za-z.\-]+)", line)
        if m and not line.startswith("//") and m.group(1) not in ("go", "module", "require", "replace"):
            comps.append({"name": m.group(1), "version": m.group(2), "scope": "gomod"})
    return comps


def _sbom_maven(text):
    comps = []
    for m in re.finditer(r"<dependency>(.*?)</dependency>", text, re.S):
        block = m.group(1)
        gid = re.search(r"<groupId>(.*?)</groupId>", block)
        aid = re.search(r"<artifactId>(.*?)</artifactId>", block)
        ver = re.search(r"<version>(.*?)</version>", block)
        if aid:
            name = (gid.group(1) + ":" if gid else "") + aid.group(1)
            comps.append({"name": name, "version": ver.group(1) if ver else "", "scope": "maven"})
    return comps


def _sbom_gradle(text):
    comps = []
    for m in re.finditer(r"(?:implementation|api|compile|testImplementation)\s+[\"']([^\"']+)[\"']", text):
        spec = m.group(1)
        mm = re.match(r"([^:]+):([^:]+):?([^:]*)$", spec)
        if mm:
            comps.append({"name": mm.group(1) + ":" + mm.group(2),
                          "version": mm.group(3) or "", "scope": "gradle"})
    return comps


def _sbom_cargo(text):
    comps = []
    if tomllib is None:
        return comps
    try:
        d = tomllib.loads(text)
    except Exception:
        return comps
    for sec in ("dependencies", "dev-dependencies"):
        for k, v in (d.get(sec) or {}).items():
            ver = v.get("version", "") if isinstance(v, dict) else str(v)
            comps.append({"name": k, "version": ver, "scope": "cargo"})
    return comps


def _sbom_gemfile(text):
    comps = []
    for m in re.finditer(r"gem\s+[\"']([^\"']+)[\"'](?:\s*,\s*[\"']([^\"']*)[\"'])?", text):
        comps.append({"name": m.group(1), "version": m.group(2) or "", "scope": "gem"})
    return comps


def _sbom_composer(text):
    comps = []
    try:
        d = json.loads(text)
    except Exception:
        return comps
    for sec in ("require", "require-dev"):
        for k, v in (d.get(sec) or {}).items():
            comps.append({"name": k, "version": str(v), "scope": "composer"})
    return comps


def sbom_gen(args, ctx):
    root = _resolve(ctx, args.get("path") or "")
    if not root:
        return "[sbom_gen] 需要 path (目录), 且在允许根目录内."
    if not os.path.isdir(root):
        return "[sbom_gen] 路径不是目录: %s" % root
    components = []
    manifests = []
    for name in _MANIFEST_HANDLERS:
        mp = os.path.join(root, name)
        if os.path.isfile(mp):
            manifests.append((name, mp))
    if not manifests:
        # 递归扫描一层子目录
        for dp, _dn, fns in os.walk(root):
            depth = dp[len(root):].count(os.sep)
            if depth > 2:
                continue
            for fn in fns:
                if fn in _MANIFEST_HANDLERS:
                    manifests.append((fn, os.path.join(dp, fn)))
    for name, mp in manifests:
        try:
            text = _read_text(mp)
            handler = getattr(sys.modules[__name__], _MANIFEST_HANDLERS[name])
            comps = handler(text)
        except Exception as e:
            comps = []
        if comps:
            manifests_label = os.path.relpath(mp, root)
            for c in comps:
                c["manifest"] = manifests_label
            components.extend(comps)
    sbom = {
        "tool": "sbom_gen",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "root": root,
        "manifest_count": len(manifests),
        "components": components,
        "component_count": len(components),
    }
    try:
        out = json.dumps(sbom, ensure_ascii=False, indent=2)
    except Exception as e:
        return "[sbom_gen] 序列化失败: %s" % e
    out_file = _resolve(ctx, args.get("out_file") or "")
    if out_file:
        try:
            _write_text(out_file, out)
            return "[sbom_gen] 已写出 %d 组件 -> %s" % (len(components), out_file)
        except Exception as e:
            return "[sbom_gen] 写出失败: %s" % e
    return out


# ----------------------------------------------------------------------------
# dep_graph  (Python 模块依赖图)
# ----------------------------------------------------------------------------
_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)


def dep_graph(args, ctx):
    root = _resolve(ctx, args.get("path") or "")
    if not root:
        return "[dep_graph] 需要 path (目录), 且在允许根目录内."
    py_files = []
    if os.path.isfile(root) and root.endswith(".py"):
        py_files = [root]
    elif os.path.isdir(root):
        for dp, _dn, fns in os.walk(root):
            if "node_modules" in dp or ".git" in dp or "__pycache__" in dp:
                continue
            for fn in fns:
                if fn.endswith(".py"):
                    py_files.append(os.path.join(dp, fn))
    else:
        return "[dep_graph] 路径不存在: %s" % root
    local_mods = set()
    for fp in py_files:
        rel = os.path.splitext(os.path.relpath(fp, root))[0]
        local_mods.add(rel.replace(os.sep, "."))
    edges = []
    mod_of_file = {}
    for fp in py_files:
        rel = os.path.splitext(os.path.relpath(fp, root))[0].replace(os.sep, ".")
        mod_of_file[fp] = rel
        try:
            text = _read_text(fp)
        except Exception:
            continue
        targets = set()
        for m in _IMPORT_RE.finditer(text):
            tgt = m.group(1) or m.group(2)
            top = tgt.split(".")[0]
            if top in local_mods:
                targets.add(top)
            elif tgt in local_mods:
                targets.add(tgt)
        for t in targets:
            edges.append({"from": rel, "to": t})
    graph = {
        "tool": "dep_graph",
        "root": root,
        "modules": sorted(local_mods),
        "module_count": len(local_mods),
        "edges": edges,
        "edge_count": len(edges),
    }
    try:
        out = json.dumps(graph, ensure_ascii=False, indent=2)
    except Exception as e:
        return "[dep_graph] 序列化失败: %s" % e
    out_file = _resolve(ctx, args.get("out_file") or "")
    if out_file:
        try:
            _write_text(out_file, out)
            return "[dep_graph] 已写出依赖图 (%d 模块/%d 边) -> %s" % (
                len(local_mods), len(edges), out_file)
        except Exception as e:
            return "[dep_graph] 写出失败: %s" % e
    return out

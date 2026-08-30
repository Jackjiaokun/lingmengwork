# -*- coding: utf-8 -*-
"""Phase 101 工具套件: 安全合规 + 数据工程增强 (零依赖优雅降级).

新增 7 工具:
  secret_audit       扫描目录/文件中的硬编码密钥 (API key/token/密码)
  dep_check          检查依赖清单 (requirements.txt/package.json) 的版本钉固与风险
  license_check      识别项目许可证类型 (LICENSE 文件关键字匹配)
  perm_diff          比较两个目录树的文件存在性/大小差异
  json_to_csv        JSON 数组 -> CSV (写出文件)
  xml_query          极简 XPath 式 XML 查询 (xml.etree 标准库)
  toml_query         TOML 路径查询 (tomllib 3.11+, 懒加载)

全部走标准库, 失败以 [tool] 前缀 + 可读信息回灌模型。
各函数为自包含实现, 不跨模块依赖私有 helper, 保证 suite 独立可测。
"""

import os
import re
import csv
import json
import codecs

from lingmengwork.tools import fs


def _resolve(ctx, path):
    try:
        return str(fs.resolve_path(ctx.get("roots") or [], path).resolve())
    except Exception:
        return str(path)


# ---------------------------------------------------------------------------
# secret_audit
# ---------------------------------------------------------------------------
# 跳过明显非文本的扩展名
_SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
    ".zip", ".gz", ".tar", ".tgz", ".rar", ".7z", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".pyd", ".bin", ".dat",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".pyc", ".class", ".o", ".a", ".lib", ".woff", ".woff2", ".ttf", ".otf",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".flac",
    ".db", ".sqlite", ".sqlite3", ".pak", ".wasm",
}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build",
              ".venv", "venv", "env", ".tox", ".mypy_cache", ".idea", ".svn"}

# 常见密钥模式 (保守匹配, 误报率优先低)
_SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws_secret", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("stripe_key", re.compile(r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("google_api", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("generic_api_key", re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}['\"]?")),
    ("generic_secret", re.compile(r"(?i)(secret|client_secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}['\"]?")),
    ("generic_password", re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{8,}['\"]?")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*")),
    ("token_assign", re.compile(r"(?i)(access[_-]?token|auth[_-]?token|token)\s*[:=]\s*['\"]?[A-Za-z0-9\-._~+/]{20,}['\"]?")),
]


def secret_audit(args, ctx):
    """扫描目录/文件中的硬编码密钥.

    args:
      path        文件或目录路径
      recursive?  目录是否递归 (默认 true)
      max_find?   最多返回命中数 (默认 50)
    """
    path = _resolve(ctx, args.get("path") or "")
    if not path or not os.path.exists(path):
        return "[secret_audit] 路径不存在: %s" % args.get("path")
    recursive = str(args.get("recursive", "true")).lower() not in ("0", "false", "no")
    max_find = max(1, int(args.get("max_find", 50) or 50))

    files = []
    if os.path.isfile(path):
        files = [path]
    else:
        if recursive:
            for root, dirs, fnames in os.walk(path):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for fn in fnames:
                    if os.path.splitext(fn)[1].lower() not in _SKIP_EXT:
                        files.append(os.path.join(root, fn))
        else:
            for fn in os.listdir(path):
                fp = os.path.join(path, fn)
                if os.path.isfile(fp) and os.path.splitext(fn)[1].lower() not in _SKIP_EXT:
                    files.append(fp)

    findings = []
    scanned = 0
    for fp in files:
        try:
            size = os.path.getsize(fp)
        except Exception:
            continue
        if size <= 0 or size > 2 * 1024 * 1024:
            continue
        scanned += 1
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        # 跳过看起来是二进制 (含大量替换符) 的文件
        joined = "".join(lines[:200])
        if joined.count("\ufffd") > max(5, len(joined) // 20):
            continue
        for ln, text in enumerate(lines, 1):
            if len(findings) >= max_find:
                break
            for label, pat in _SECRET_PATTERNS:
                m = pat.search(text)
                if m:
                    snippet = text.strip()[:120]
                    findings.append("%s:%d [%s] %s" % (fp, ln, label, snippet))
                    break
        if len(findings) >= max_find:
            break

    if not findings:
        return "[secret_audit] 未发现可疑硬编码密钥 (扫描 %d 个文件)." % scanned
    head = "[secret_audit] 命中 %d 处可疑密钥 (扫描 %d 文件, 显示前 %d):\n" % (
        len(findings), scanned, len(findings))
    return head + "\n".join(findings)


# ---------------------------------------------------------------------------
# dep_check
# ---------------------------------------------------------------------------
_RISK_HINTS = {
    "eval": "含 eval/exec 的包需谨慎",
    "pickle": "反序列化风险",
    "yaml": "PyYAML<5.1 有反序列化漏洞, 建议钉固 >=5.1",
    "requests": "知名库, 建议钉固版本",
    "django": "建议钉固并关注安全公告",
    "flask": "建议钉固版本",
    "jinja2": "建议钉固 >=2.11.3",
}


def _parse_requirements(text):
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 去除行内注释
        line = line.split("#", 1)[0].strip()
        # 支持 -r 包含
        if line.startswith("-r ") or line.startswith("--requirement"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(==|>=|<=|~=|!=|<|>)?\s*([0-9A-Za-z.\-\*]*)", line)
        if m:
            name = m.group(1)
            spec = (m.group(2) or "") + (m.group(3) or "")
            out.append((name, spec))
    return out


def _parse_package_json(data):
    out = []
    for grp in ("dependencies", "devDependencies", "peerDependencies"):
        deps = (data or {}).get(grp) or {}
        for name, spec in deps.items():
            out.append((name, str(spec)))
    return out


def dep_check(args, ctx):
    """检查依赖清单的版本钉固与风险.

    args:
      path        目录或依赖文件 (requirements.txt/package.json/pyproject.toml)
    """
    path = _resolve(ctx, args.get("path") or "")
    if not path or not os.path.exists(path):
        return "[dep_check] 路径不存在: %s" % args.get("path")

    targets = []
    if os.path.isdir(path):
        for cand in ("requirements.txt", "package.json", "pyproject.toml", "Pipfile"):
            fp = os.path.join(path, cand)
            if os.path.isfile(fp):
                targets.append(fp)
    else:
        targets = [path]

    if not targets:
        return "[dep_check] 未找到依赖清单 (requirements.txt/package.json/pyproject.toml/Pipfile)."

    lines = []
    total = 0
    unpinned = 0
    for fp in targets:
        try:
            content = open(fp, "r", encoding="utf-8", errors="replace").read()
        except Exception as e:
            lines.append("  %s: 读取失败 %s" % (fp, e))
            continue
        if fp.endswith(".json"):
            try:
                data = json.loads(content)
            except Exception:
                data = {}
            deps = _parse_package_json(data)
        else:
            deps = _parse_requirements(content)
        lines.append("  %s: %d 个依赖" % (os.path.basename(fp), len(deps)))
        for name, spec in deps:
            total += 1
            pinned = bool(re.search(r"(==|>=|<=|~=)", spec))
            if not pinned:
                unpinned += 1
            hint = ""
            for k, v in _RISK_HINTS.items():
                if k in name.lower():
                    hint = "  ⚠ %s" % v
                    break
            mark = "钉固" if pinned else "未钉固"
            lines.append("    - %s %s  [%s]%s" % (name, spec or "*", mark, hint))
    summary = "[dep_check] 共 %d 个依赖, 未钉固 %d 个.\n" % (total, unpinned)
    return summary + "\n".join(lines)


# ---------------------------------------------------------------------------
# license_check
# ---------------------------------------------------------------------------
# (匹配关键字[大写], 展示标签, 说明). 关键字取许可证正文中必现的子串.
_LICENSE_KEYWORDS = [
    ("MIT", "MIT License", "宽松 (可商用, 仅需保留版权声明)"),
    ("APACHE", "Apache License 2.0", "宽松 (含专利授权与声明要求)"),
    ("BSD", "BSD License", "宽松"),
    ("ISC", "ISC License", "宽松 (近似 MIT)"),
    ("MOZILLA", "Mozilla Public License 2.0", "弱Copyleft (文件级)"),
    ("AFFERO", "GNU Affero General Public License", "强Copyleft (网络服务也需开源)"),
    ("LESSER", "GNU Lesser General Public License", "弱Copyleft (库级)"),
    ("GENERAL PUBLIC LICENSE", "GNU General Public License", "强Copyleft (衍生须开源)"),
    ("UNLICENSE", "The Unlicense", "公共领域捐献"),
    ("CC0", "Creative Commons Zero", "公共领域捐献"),
    ("CREATIVE COMMONS ATTRIBUTION", "Creative Commons Attribution", "署名许可"),
    ("PROPRIETARY", "Proprietary", "专有 (需商业授权)"),
]
# Copyleft 优先级 (数值越大越具体)
_COPYLEFT_RANK = {"AFFERO": 3, "LESSER": 2, "GENERAL PUBLIC LICENSE": 1}


def license_check(args, ctx):
    """识别项目许可证类型.

    args:
      path        目录 (自动找 LICENSE*) 或许可证文件
    """
    path = _resolve(ctx, args.get("path") or "")
    if not path:
        return "[license_check] 缺 path."
    if os.path.isdir(path):
        cand = None
        for fn in os.listdir(path):
            if fn.upper().startswith("LICENSE") or fn.upper().startswith("COPYING") or fn.upper().startswith("LICENCE"):
                cand = os.path.join(path, fn)
                break
        if not cand:
            return "[license_check] 目录内未找到 LICENSE/COPYING 文件: %s" % args.get("path")
        target = cand
    elif os.path.isfile(path):
        target = path
    else:
        return "[license_check] 路径不存在: %s" % args.get("path")

    try:
        text = open(target, "r", encoding="utf-8", errors="replace").read()
    except Exception as e:
        return "[license_check] 读取失败: %s" % e

    found = []
    up = text.upper()
    best_copyleft = None
    for key, label, note in _LICENSE_KEYWORDS:
        if key.upper() in up:
            if key in _COPYLEFT_RANK:
                if best_copyleft is None or _COPYLEFT_RANK[key] > _COPYLEFT_RANK[best_copyleft[0]]:
                    best_copyleft = (key, label, note)
            else:
                found.append((label, note))
    if best_copyleft:
        found.insert(0, (best_copyleft[1], best_copyleft[2]))
    if not found:
        return "[license_check] 未能识别许可证类型 (文件: %s, %d 字符)." % (target, len(text))
    lines = ["[license_check] %s" % target]
    for label, note in found:
        lines.append("  - %s: %s" % (label, note))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# perm_diff
# ---------------------------------------------------------------------------
def _tree(root, recursive):
    out = {}
    if recursive:
        for base, dirs, fnames in os.walk(root):
            for fn in fnames:
                fp = os.path.join(base, fn)
                rel = os.path.relpath(fp, root)
                try:
                    out[rel] = os.path.getsize(fp)
                except Exception:
                    out[rel] = -1
    else:
        for fn in os.listdir(root):
            fp = os.path.join(root, fn)
            if os.path.isfile(fp):
                try:
                    out[fn] = os.path.getsize(fp)
                except Exception:
                    out[fn] = -1
    return out


def perm_diff(args, ctx):
    """比较两个目录树的文件存在性/大小差异.

    args:
      a, b        两个目录路径
      recursive?  是否递归 (默认 true)
      max?        最多列出差异数 (默认 100)
    """
    a = _resolve(ctx, args.get("a") or "")
    b = _resolve(ctx, args.get("b") or "")
    if not a or not os.path.isdir(a):
        return "[perm_diff] a 不是有效目录: %s" % args.get("a")
    if not b or not os.path.isdir(b):
        return "[perm_diff] b 不是有效目录: %s" % args.get("b")
    recursive = str(args.get("recursive", "true")).lower() not in ("0", "false", "no")
    maxn = max(1, int(args.get("max", 100) or 100))

    ta = _tree(a, recursive)
    tb = _tree(b, recursive)
    only_a = sorted(set(ta) - set(tb))
    only_b = sorted(set(tb) - set(ta))
    differ = sorted([k for k in set(ta) & set(tb) if ta[k] != tb[k] and ta[k] >= 0 and tb[k] >= 0])

    parts = []
    parts.append("[perm_diff] a=%d 文件, b=%d 文件" % (len(ta), len(tb)))
    parts.append("  仅存在于 a: %d" % len(only_a))
    for k in only_a[:maxn]:
        parts.append("    - %s (%d B)" % (k, ta[k]))
    parts.append("  仅存在于 b: %d" % len(only_b))
    for k in only_b[:maxn]:
        parts.append("    + %s (%d B)" % (k, tb[k]))
    parts.append("  大小不一致: %d" % len(differ))
    for k in differ[:maxn]:
        parts.append("    ~ %s  a=%d b=%d" % (k, ta[k], tb[k]))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# json_to_csv
# ---------------------------------------------------------------------------
def json_to_csv(args, ctx):
    """将 JSON 数组转换为 CSV 并写出.

    args:
      json?       JSON 字符串 (数组)
      file?       JSON 文件路径 (优先于 json)
      out_file   输出 CSV 路径 (必填)
    """
    out_file = _resolve(ctx, args.get("out_file") or "")
    if not out_file:
        return "[json_to_csv] 缺 out_file."
    raw = args.get("json")
    if not raw and args.get("file"):
        fp = _resolve(ctx, args.get("file"))
        try:
            raw = open(fp, "r", encoding="utf-8", errors="replace").read()
        except Exception as e:
            return "[json_to_csv] 读取 %s 失败: %s" % (args.get("file"), e)
    if raw is None:
        return "[json_to_csv] 需提供 json 或 file."
    try:
        data = json.loads(raw)
    except Exception as e:
        return "[json_to_csv] JSON 解析失败: %s" % e
    if not isinstance(data, list):
        return "[json_to_csv] 顶层须为数组."

    rows = []
    for item in data:
        if isinstance(item, dict):
            rows.append(item)
        elif isinstance(item, (list, tuple)):
            rows.append({"col%d" % i: v for i, v in enumerate(item)})
        else:
            rows.append({"value": item})

    if not rows:
        return "[json_to_csv] 数组为空, 未写出."

    # 列 = 所有 dict 键的并集, 保持首次出现顺序
    cols = []
    seen = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                cols.append(k)

    try:
        with open(out_file, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow(r)
    except Exception as e:
        return "[json_to_csv] 写出失败: %s" % e
    return "[json_to_csv] 已写出 %s (%d 行 x %d 列)." % (out_file, len(rows), len(cols))


# ---------------------------------------------------------------------------
# xml_query
# ---------------------------------------------------------------------------
def xml_query(args, ctx):
    """极简 XPath 式 XML 查询.

    args:
      file?       XML 文件路径
      xml?        XML 字符串 (优先于 file)
      query       路径查询, 如 "root/item/name" 取文本, "root/item/@id" 取属性
      attr?       取属性名 (与 query 末段二选一, 以 @ 前缀更直观)
      max?        最多返回条数 (默认 50)
    """
    raw = args.get("xml")
    if not raw and args.get("file"):
        fp = _resolve(ctx, args.get("file"))
        try:
            raw = open(fp, "r", encoding="utf-8", errors="replace").read()
        except Exception as e:
            return "[xml_query] 读取 %s 失败: %s" % (args.get("file"), e)
    if not raw:
        return "[xml_query] 需提供 xml 或 file."
    query = (args.get("query") or "").strip()
    if not query:
        return "[xml_query] 缺 query."

    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw)
    except Exception as e:
        return "[xml_query] XML 解析失败: %s" % e

    maxn = max(1, int(args.get("max", 50) or 50))
    parts = [p for p in query.strip("/").split("/") if p]
    if not parts:
        return "[xml_query] query 为空."

    attr = None
    if parts[-1].startswith("@"):
        attr = parts[-1][1:]
        parts = parts[:-1]

    nodes = [root]
    # 首段若等于根标签则从根自身开始 (支持 "root/item/name" 写法)
    if parts and parts[0] == root.tag:
        parts = parts[1:]
    for step in parts:
        nxt = []
        for nd in nodes:
            nxt.extend(nd.findall(step))
        nodes = nxt
        if not nodes:
            break

    results = []
    for nd in nodes:
        if attr is not None:
            if attr in nd.attrib:
                results.append(nd.attrib[attr])
        else:
            txt = (nd.text or "").strip()
            if txt:
                results.append(txt)
    if not results:
        return "[xml_query] 无匹配 (query=%s)." % query
    return "[xml_query] 命中 %d 项 (query=%s):\n%s" % (len(results), query, "\n".join(results[:maxn]))


# ---------------------------------------------------------------------------
# toml_query
# ---------------------------------------------------------------------------
def toml_query(args, ctx):
    """TOML 路径查询.

    args:
      file?       TOML 文件路径
      toml?       TOML 字符串 (优先于 file)
      path        点分路径, 如 "a.b.c" 或 "a.list[0].c"
    """
    raw = args.get("toml")
    if not raw and args.get("file"):
        fp = _resolve(ctx, args.get("file"))
        try:
            raw = open(fp, "r", encoding="utf-8", errors="replace").read()
        except Exception as e:
            return "[toml_query] 读取 %s 失败: %s" % (args.get("file"), e)
    if not raw:
        return "[toml_query] 需提供 toml 或 file."
    path = (args.get("path") or "").strip()
    if not path:
        return "[toml_query] 缺 path."

    try:
        import tomllib
    except Exception:
        return "[toml_query] 当前 Python 版本过低 (<3.11), 无 tomllib 支持."
    try:
        data = tomllib.loads(raw)
    except Exception as e:
        return "[toml_query] TOML 解析失败: %s" % e

    # 解析路径: a.b.c / a.list[0].c
    cur = data
    for seg in path.split("."):
        m = re.match(r"^([A-Za-z0-9_\-]+)(?:\[(\d+)\])?$", seg)
        if not m:
            return "[toml_query] 非法路径段: %s" % seg
        key, idx = m.group(1), m.group(2)
        if not isinstance(cur, dict) or key not in cur:
            return "[toml_query] 路径不存在: ...%s" % seg
        cur = cur[key]
        if idx is not None:
            if not isinstance(cur, list) or int(idx) >= len(cur):
                return "[toml_query] 索引越界: %s[%s]" % (key, idx)
            cur = cur[int(idx)]
    if isinstance(cur, (dict, list)):
        return "[toml_query] %s = %s" % (path, json.dumps(cur, ensure_ascii=False))
    return "[toml_query] %s = %r" % (path, cur)

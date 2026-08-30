"""Phase 97 工具套件: 集成 / 运维 / 数据增强 (零依赖优雅降级).

新增 7 工具:
  webhook_sign     计算 webhook 签名 (HMAC-SHA256, 含时间戳防重放)
  db_diff          对比两个 SQLite 库: 表结构 + 行差异
  changelog_update 按 Keep a Changelog 格式在 CHANGELOG.md 顶部插入新版本块
  code_search_ast  基于 AST 搜索 Python 代码 (def/class/call/import/name)
  csv_merge        合并多个 CSV: 纵向 concat 或横向 join on 键
  json_query       轻量 JSONPath 查询 ($.a.b / $.x[*].y / $.a[0])
  env_check        校验必需环境变量 / 对比 .env 模板与当前环境

全部走标准库, 失败以 [tool] 前缀 + 可读信息回灌模型。
"""

import os
import re
import ast
import csv
import json
import hmac
import hashlib
import sqlite3
import time

from lingmengwork.tools import fs


def _resolve(ctx, path):
    try:
        return str(fs.resolve_path(ctx.get("roots") or [], path).resolve())
    except Exception:
        return str(path)


# ---------------------------------------------------------------------------
# webhook_sign
# ---------------------------------------------------------------------------
def webhook_sign(args, ctx):
    """计算 webhook 签名 (HMAC-SHA256, 含可选时间戳防重放)."""
    secret = args.get("secret") or ""
    payload = args.get("payload") or ""
    ts = args.get("timestamp")
    if ts is None:
        ts = int(time.time())
    else:
        ts = int(ts)
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if not secret:
        return "[webhook_sign] 缺 secret, 无法签名。"
    body = "%d.%s" % (ts, payload)
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return ("[webhook_sign] 签名完成\n"
            "timestamp: %d\n"
            "X-Signature: sha256=%s\n"
            "校验串(待发送): %s.%s" % (ts, sig, sig, ts))


# ---------------------------------------------------------------------------
# db_diff
# ---------------------------------------------------------------------------
def db_diff(args, ctx):
    """对比两个 SQLite 库: 表结构 + 行差异(按全列 tuple 比对)."""
    a = _resolve(ctx, args.get("a") or "")
    b = _resolve(ctx, args.get("b") or "")
    if not os.path.exists(a):
        return "[db_diff] 库A不存在: %s" % a
    if not os.path.exists(b):
        return "[db_diff] 库B不存在: %s" % b
    try:
        ca = sqlite3.connect(a)
        cb = sqlite3.connect(b)
        ta = {r[0] for r in ca.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tb = {r[0] for r in cb.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        lines = []
        only_a = ta - tb
        only_b = tb - ta
        if only_a:
            lines.append("仅库A有表: %s" % ", ".join(sorted(only_a)))
        if only_b:
            lines.append("仅库B有表: %s" % ", ".join(sorted(only_b)))
        for t in sorted(ta & tb):
            ca_cols = {r[1] for r in ca.execute("PRAGMA table_info(%s)" % t)}
            cb_cols = {r[1] for r in cb.execute("PRAGMA table_info(%s)" % t)}
            dcol = ca_cols ^ cb_cols
            if dcol:
                lines.append("表 %s 列差异: %s" % (t, ", ".join(sorted(dcol))))
            try:
                ra = set(ca.execute("SELECT * FROM %s" % t))
                rb = set(cb.execute("SELECT * FROM %s" % t))
                if ra != rb:
                    lines.append("表 %s 行差异: 仅A %d / 仅B %d" % (t, len(ra - rb), len(rb - ra)))
            except Exception:
                pass
        ca.close()
        cb.close()
        if not lines:
            return "[db_diff] 两库结构一致(表/列/行均相同)。"
        return "[db_diff]\n" + "\n".join(lines)
    except Exception as e:
        return "[db_diff] 失败: %s" % e


# ---------------------------------------------------------------------------
# changelog_update
# ---------------------------------------------------------------------------
def changelog_update(args, ctx):
    """按 Keep a Changelog 格式在 CHANGELOG.md 顶部插入新版本块."""
    path = _resolve(ctx, args.get("file") or "CHANGELOG.md")
    version = args.get("version") or ""
    if not version:
        return "[changelog_update] 缺 version。"
    date = args.get("date") or time.strftime("%Y-%m-%d")
    changes = args.get("changes") or []
    if isinstance(changes, str):
        changes = [c for c in changes.splitlines() if c.strip()]
    section = args.get("section") or "Added"
    bullets = "\n".join("- %s" % c for c in changes) if changes else "- (无明细)"
    block = "\n## [%s] - %s\n\n### %s\n\n%s\n" % (version, date, section, bullets)
    if os.path.exists(path):
        old = open(path, encoding="utf-8").read()
        m = re.search(r"^##\s+\[", old, re.M)
        if m:
            new = old[:m.start()] + block.strip() + "\n\n" + old[m.start():]
        else:
            new = old.rstrip() + "\n" + block
    else:
        new = ("# Changelog\n\n所有 notable changes 见此文件。格式参考 Keep a Changelog。\n"
               + block)
    try:
        open(path, "w", encoding="utf-8").write(new)
        return "[changelog_update] 已写入 %s (v%s, %d 条变更)" % (path, version, len(changes))
    except Exception as e:
        return "[changelog_update] 写失败: %s" % e


# ---------------------------------------------------------------------------
# code_search_ast
# ---------------------------------------------------------------------------
def code_search_ast(args, ctx):
    """基于 AST 搜索 Python 代码中的 定义/调用/导入 (其他语言正则兜底)."""
    path = _resolve(ctx, args.get("path") or "")
    if not os.path.exists(path):
        return "[code_search_ast] 路径不存在: %s" % path
    pattern = args.get("pattern") or ""
    kind = (args.get("kind") or "def").lower()  # def|class|call|import|name
    name = args.get("name") or ""
    files = []
    if os.path.isdir(path):
        for root, _, fs_l in os.walk(path):
            for f in fs_l:
                if f.endswith(".py"):
                    files.append(os.path.join(root, f))
    else:
        files = [path]
    hits = []
    for fp in files:
        try:
            src = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            if pattern:
                for i, line in enumerate(src.splitlines(), 1):
                    if pattern in line:
                        hits.append("%s:%d (regex) %s" % (fp, i, line.strip()[:120]))
            continue
        for node in ast.walk(tree):
            if kind == "def" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not name or name in node.name:
                    hits.append("%s:%d def %s" % (fp, node.lineno, node.name))
            elif kind == "class" and isinstance(node, ast.ClassDef):
                if not name or name in node.name:
                    hits.append("%s:%d class %s" % (fp, node.lineno, node.name))
            elif kind == "call" and isinstance(node, ast.Call):
                fn = _call_name(node.func)
                if (not name or name in fn) and (not pattern or pattern in fn):
                    hits.append("%s:%d call %s" % (fp, node.lineno, fn))
            elif kind == "import" and isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = _import_names(node)
                if not name or any(name in m for m in mods):
                    hits.append("%s:%d import %s" % (fp, node.lineno, ", ".join(mods)))
            elif kind == "name" and isinstance(node, ast.Name):
                if name and name == node.id:
                    hits.append("%s:%d name %s" % (fp, node.lineno, node.id))
    if not hits:
        return "[code_search_ast] 未命中 (path=%s kind=%s name=%s)." % (path, kind, name)
    return "[code_search_ast] 命中 %d 处:\n%s" % (len(hits), "\n".join(hits[:200]))


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _call_name(node.value) + "." + node.attr
    return "?"


def _import_names(node):
    out = []
    if isinstance(node, ast.Import):
        out = [a.name for a in node.names]
    else:
        out = ["%s.%s" % (node.module or "", a.name) for a in node.names]
    return [o for o in out if o]


# ---------------------------------------------------------------------------
# csv_merge
# ---------------------------------------------------------------------------
def csv_merge(args, ctx):
    """合并多个 CSV: 纵向(concat) 或 横向(join on 键)."""
    files = args.get("files") or []
    if isinstance(files, str):
        files = [files]
    if not files:
        return "[csv_merge] 缺 files。"
    out = _resolve(ctx, args.get("out") or "merged.csv")
    how = (args.get("how") or "concat").lower()
    keys = args.get("keys") or []
    if isinstance(keys, str):
        keys = [keys]
    try:
        rowsets = []
        headers_all = []
        for f in files:
            p = _resolve(ctx, f)
            with open(p, encoding="utf-8", newline="") as fh:
                rows = list(csv.reader(fh))
            if not rows:
                continue
            headers_all.append(rows[0])
            rowsets.append(rows)
        if not rowsets:
            return "[csv_merge] 无有效数据。"
        if how == "join" and keys:
            h0 = headers_all[0]
            merged_header = list(h0)
            idx = {k: h0.index(k) for k in keys if k in h0}
            maps = []
            for hi, rows in zip(headers_all, rowsets):
                m = {}
                keyidx = [hi.index(k) for k in keys if k in hi]
                for r in rows[1:]:
                    kk = tuple(r[i] for i in keyidx)
                    m[kk] = r
                maps.append((hi, m))
                for c in hi:
                    if c not in merged_header:
                        merged_header.append(c)
            out_rows = [merged_header]
            seen = set()
            for r in rowsets[0][1:]:
                kk = tuple(r[idx[k]] for k in keys)
                if kk in seen:
                    continue
                seen.add(kk)
                merged = dict(zip(h0, r))
                for hi, m in maps[1:]:
                    if kk in m:
                        for c, v in zip(hi, m[kk]):
                            merged[c] = v
                out_rows.append([merged.get(c, "") for c in merged_header])
        else:
            header = headers_all[0]
            out_rows = [header]
            for hi, rows in zip(headers_all, rowsets):
                pos = {c: i for i, c in enumerate(hi)}
                for r in rows[1:]:
                    out_rows.append([r[pos[c]] if c in pos else "" for c in header])
        with open(out, "w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerows(out_rows)
        return "[csv_merge] 已合并 %d 文件 -> %s (%d 行, %d 列, 模式=%s)" % (
            len(files), out, len(out_rows) - 1, len(out_rows[0]), how)
    except Exception as e:
        return "[csv_merge] 失败: %s" % e


# ---------------------------------------------------------------------------
# json_query
# ---------------------------------------------------------------------------
def json_query(args, ctx):
    """轻量 JSONPath 查询: 支持 $.a.b / $.x[*].y / $.a[0]."""
    path = args.get("path")
    data = args.get("data")
    if data is None and path:
        p = _resolve(ctx, path)
        if not os.path.exists(p):
            return "[json_query] 文件不存在: %s" % p
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            return "[json_query] 解析失败: %s" % e
    if data is None:
        return "[json_query] 缺 path 或 data。"
    jp = args.get("jsonpath") or args.get("query") or "$"
    try:
        res = _jsonpath(data, jp)
    except Exception as e:
        return "[json_query] 路径错误: %s" % e
    if res is None:
        return "[json_query] 路径无匹配: %s" % jp
    return "[json_query] %s =\n%s" % (jp, json.dumps(res, ensure_ascii=False, indent=2)[:4000])


def _jsonpath(data, expr):
    expr = expr.strip()
    if expr.startswith("$."):
        expr = expr[2:]
    elif expr.startswith("$"):
        expr = expr[1:]
    toks = []
    for key, br in re.findall(r"([^.\[\]]+)|\[(\*|\d+)\]", expr):
        if key:
            toks.append(("k", key))
        else:
            toks.append(("*" if br == "*" else "i", br))
    return _jp_apply(data, toks)


def _jp_apply(cur, toks):
    if not toks:
        return cur
    head, rest = toks[0], toks[1:]
    kind, val = head
    if kind == "k":
        if not isinstance(cur, dict) or val not in cur:
            return None
        return _jp_apply(cur[val], rest)
    if not isinstance(cur, list):
        return None
    if kind == "*":
        return [_jp_apply(x, rest) for x in cur]
    i = int(val)
    if i >= len(cur):
        return None
    return _jp_apply(cur[i], rest)


# ---------------------------------------------------------------------------
# env_check
# ---------------------------------------------------------------------------
def env_check(args, ctx):
    """校验必需环境变量是否设置, 或对比 .env 模板与当前环境."""
    required = args.get("required") or []
    if isinstance(required, str):
        required = [x for x in re.split(r"[,\s]+", required) if x]
    template = args.get("template")
    env_file = args.get("env_file")
    names = list(required)
    if template:
        tp = _resolve(ctx, template)
        if os.path.exists(tp):
            for line in open(tp, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                names.append(line.split("=", 1)[0].strip())
    if env_file:
        ep = _resolve(ctx, env_file)
        loaded = {}
        if os.path.exists(ep):
            for line in open(ep, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                loaded[line.split("=", 1)[0].strip()] = line.split("=", 1)[1].strip()
        cur = dict(os.environ)
        cur.update(loaded)
    else:
        cur = dict(os.environ)
    missing = []
    present = []
    for n in dict.fromkeys(names):
        if cur.get(n):
            present.append(n)
        else:
            missing.append(n)
    lines = []
    if present:
        lines.append("已设置(%d): %s" % (len(present), ", ".join(present)))
    if missing:
        lines.append("缺失(%d): %s" % (len(missing), ", ".join(missing)))
    if not lines:
        return "[env_check] 无需校验的变量。"
    return "[env_check]\n" + "\n".join(lines)

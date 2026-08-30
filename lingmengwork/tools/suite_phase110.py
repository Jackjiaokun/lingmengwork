"""Phase 110 工具套件: API 契约生成 / 正则与版本 / SQL 与 cron 校验 / 编码 (零依赖).

全部工具仅用标准库, 输入为文本, 失败以 [tool] 前缀优雅降级回灌模型。
- openapi_gen   : JSON Schema -> OpenAPI 3.0 片段 (路径/方法可配)
- json_minify   : JSON 压缩 (剥离 // 与 /* */ 注释与尾随逗号)
- regex_test    : 正则测试台 (匹配项/位置/捕获组/替换预览, i/m/s/x 标志)
- semver_compare: 语义化版本比较 (含预发布, 1.0.0-alpha < 1.0.0)
- sql_validate  : SQL 基础校验 (括号/字符串配对、语句首字母、破坏性操作与缺 WHERE 提示)
- cron_validate : cron 表达式校验 (5/6 字段, 逐字段范围与步长)
- base64_codec  : Base64 编解码 (标准/URL 安全)

标准库: json / re / base64
"""

import json
import re
import base64


# ---------------------------------------------------------------------------
# json_minify (辅助: 宽松解析)
# ---------------------------------------------------------------------------
def _strip_json_comments(s):
    """剥离 // 与 /* */ 注释, 不破坏字符串字面量。"""
    out = []
    i = 0
    in_str = False
    esc = False
    n = len(s)
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            while i < n and s[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            i = (j + 2) if j != -1 else n
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _loads_lenient(raw):
    try:
        return json.loads(raw)
    except Exception:
        cleaned = _strip_json_comments(raw)
        cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)  # 尾随逗号
        return json.loads(cleaned)


# ---------------------------------------------------------------------------
# openapi_gen
# ---------------------------------------------------------------------------
def openapi_gen(args, ctx):
    raw = args.get("schema") or ""
    title = (args.get("title") or "API").strip() or "API"
    path = (args.get("path") or "/resource").strip()
    method = (args.get("method") or "post").strip().lower()
    if method not in ("get", "post", "put", "patch", "delete"):
        return "[openapi_gen] method 需为 get/post/put/patch/delete 之一."
    try:
        schema = json.loads(raw)
    except Exception as e:
        return "[openapi_gen] Schema 解析失败: %s" % e
    op = {
        "summary": "%s %s" % (method.upper(), path),
        "responses": {
            "200": {"description": "OK"},
            "400": {"description": "Bad Request"},
        },
    }
    if method in ("get", "delete"):
        op["parameters"] = [
            {"name": k, "in": "query", "required": k in (schema.get("required") or []),
             "schema": {"type": (v or {}).get("type", "string")}}
            for k, v in (schema.get("properties") or {}).items()
        ]
    else:
        op["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": schema}},
        }
    doc = {
        "openapi": "3.0.3",
        "info": {"title": title, "version": "1.0.0"},
        "paths": {path: {method: op}},
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# json_minify
# ---------------------------------------------------------------------------
def json_minify(args, ctx):
    raw = args.get("json") or ""
    if not raw.strip():
        return "[json_minify] 空输入."
    try:
        data = _loads_lenient(raw)
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        return "[json_minify] 解析失败: %s" % e


# ---------------------------------------------------------------------------
# regex_test
# ---------------------------------------------------------------------------
def regex_test(args, ctx):
    pattern = args.get("pattern") or ""
    text = args.get("text") or ""
    flags_str = (args.get("flags") or "").strip()
    repl = args.get("replace")
    if not pattern:
        return "[regex_test] 缺少 pattern."
    flags = 0
    for ch in flags_str.upper():
        if ch == "I":
            flags |= re.IGNORECASE
        elif ch == "M":
            flags |= re.MULTILINE
        elif ch == "S":
            flags |= re.DOTALL
        elif ch == "X":
            flags |= re.VERBOSE
    try:
        rx = re.compile(pattern, flags)
    except Exception as e:
        return "[regex_test] 正则编译失败: %s" % e
    matches = []
    for m in rx.finditer(text):
        matches.append({
            "match": m.group(0),
            "start": m.start(),
            "end": m.end(),
            "groups": list(m.groups()),
        })
        if len(matches) >= 50:
            break
    out = {
        "pattern": pattern,
        "flags": flags_str,
        "count": len(matches),
        "truncated": len(matches) >= 50,
        "matches": matches,
    }
    if repl is not None:
        try:
            out["replaced"] = rx.sub(repl, text)
        except Exception as e:
            out["replace_error"] = str(e)
    return json.dumps(out, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# semver_compare
# ---------------------------------------------------------------------------
def _parse_semver(v):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?(?:\+[0-9A-Za-z.\-]+)?$",
                 (v or "").strip())
    if not m:
        return None
    major, minor, patch, pre = m.groups()
    return (int(major), int(minor), int(patch), pre or "")


def semver_compare(args, ctx):
    a_raw = (args.get("a") or "").strip()
    b_raw = (args.get("b") or "").strip()
    pa, pb = _parse_semver(a_raw), _parse_semver(b_raw)
    if not pa:
        return "[semver_compare] 版本 a 不是合法 semver: %s" % (a_raw or "(空)")
    if not pb:
        return "[semver_compare] 版本 b 不是合法 semver: %s" % (b_raw or "(空)")

    def key(p):
        # 带预发布标识的版本低于同号正式版
        return (p[0], p[1], p[2], 0 if p[3] else 1, p[3])

    ka, kb = key(pa), key(pb)
    rel = -1 if ka < kb else (1 if ka > kb else 0)
    return json.dumps({
        "a": a_raw, "b": b_raw, "result": rel,
        "relation": "a<b" if rel < 0 else ("a>b" if rel > 0 else "a==b"),
        "a_prerelease": pa[3] or None, "b_prerelease": pb[3] or None,
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# sql_validate
# ---------------------------------------------------------------------------
_SQL_HEAD = {"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
             "TRUNCATE", "WITH", "EXPLAIN", "PRAGMA", "REPLACE", "GRANT",
             "BEGIN", "COMMIT", "ROLLBACK"}


def sql_validate(args, ctx):
    raw = args.get("sql") or ""
    if not raw.strip():
        return "[sql_validate] 空 SQL."
    issues = []
    depth = 0
    in_str = False
    for i, c in enumerate(raw):
        if c == "'":
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                issues.append({"type": "unmatched_paren", "pos": i})
                depth = 0
    if depth != 0:
        issues.append({"type": "unclosed_paren", "count": depth})
    if in_str:
        issues.append({"type": "unterminated_string"})

    stmt = raw.strip().rstrip(";").strip()
    if not stmt:
        issues.append({"type": "empty_statement"})
    else:
        head = re.split(r"\s+", stmt)[0].upper()
        if head not in _SQL_HEAD:
            issues.append({"type": "unknown_statement", "detail": head})

    if re.search(r"\b(DROP|TRUNCATE)\b", stmt, re.I) and not re.search(r"\bIF\s+EXISTS\b", stmt, re.I):
        issues.append({"type": "destructive", "detail": "DROP/TRUNCATE 缺少 IF EXISTS 保护"})
    if re.search(r"\b(UPDATE|DELETE)\b", stmt, re.I) and not re.search(r"\bWHERE\b", stmt, re.I):
        issues.append({"type": "missing_where", "detail": "UPDATE/DELETE 缺少 WHERE 条件"})
    return json.dumps({"ok": not issues, "issues": issues}, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# cron_validate
# ---------------------------------------------------------------------------
_CRON_FIELDS = [("minute", 0, 59), ("hour", 0, 23), ("day_of_month", 1, 31),
                ("month", 1, 12), ("day_of_week", 0, 7)]


def _check_cron_field(part_field, lo, hi):
    if part_field in ("*", "?"):
        return None
    for part in part_field.split(","):
        if "/" in part:
            base, step = part.split("/", 1)
            if not step.isdigit() or int(step) <= 0:
                return "步长非法: %s" % step
            part = base
            if part in ("*", "?"):
                continue
        if part in ("*", "?"):
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                return "范围含非数字: %s" % part
            if not (lo <= int(a) <= hi and lo <= int(b) <= hi):
                return "范围越界: %s (允许 %d-%d)" % (part, lo, hi)
            if int(a) > int(b):
                return "范围倒置: %s" % part
        elif part.isdigit():
            if not (lo <= int(part) <= hi):
                return "取值越界: %s (允许 %d-%d)" % (part, lo, hi)
        else:
            return "非法片段: %s" % part
    return None


def cron_validate(args, ctx):
    expr = (args.get("cron") or "").strip()
    if not expr:
        return "[cron_validate] 缺少 cron 表达式."
    fields = expr.split()
    if len(fields) not in (5, 6):
        return json.dumps({
            "expression": expr, "ok": False,
            "issues": [{"type": "field_count",
                        "detail": "需 5 或 6 个字段, 实际 %d" % len(fields)}],
        }, ensure_ascii=False, indent=2)
    if len(fields) == 6:
        fields = fields[1:]  # 忽略秒字段
    issues = []
    for (name, lo, hi), f in zip(_CRON_FIELDS, fields):
        err = _check_cron_field(f, lo, hi)
        if err:
            issues.append({"field": name, "value": f, "detail": err})
    return json.dumps({
        "expression": expr,
        "fields": {n: f for (n, _, _), f in zip(_CRON_FIELDS, fields)},
        "ok": not issues,
        "issues": issues,
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# base64_codec
# ---------------------------------------------------------------------------
def base64_codec(args, ctx):
    mode = (args.get("mode") or "encode").strip().lower()
    text = args.get("text") or ""
    urlsafe = str(args.get("urlsafe") or "").lower() in ("1", "true", "yes", "on")
    if mode not in ("encode", "decode"):
        return "[base64_codec] mode 需为 encode 或 decode."
    if not text:
        return "[base64_codec] 缺少 text."
    try:
        if mode == "encode":
            fn = base64.urlsafe_b64encode if urlsafe else base64.b64encode
            return fn(text.encode("utf-8")).decode("ascii")
        fn = base64.urlsafe_b64decode if urlsafe else base64.b64decode
        return fn(text.encode("ascii")).decode("utf-8", errors="replace")
    except Exception as e:
        return "[base64_codec] %s失败: %s" % ("编码" if mode == "encode" else "解码", e)

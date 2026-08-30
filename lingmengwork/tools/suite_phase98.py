"""Phase 98 工具套件: 集成 / 运维 / 安全增强 (零依赖优雅降级).

新增 7 工具:
  webhook_verify       验证 webhook 签名 (HMAC-SHA256, 含时间戳防重放)
  sql_format           轻量 SQL 格式化 (关键字折行 + 括号缩进)
  csv_diff             两个 CSV 按 key 列对齐的差量报告
  json_schema_validate 校验 JSON 是否符合简化 schema
  release_tag          semver 解析/校验/比较/递增
  log_tail             读日志尾部 N 行, 支持关键字过滤
  password_generate    生成强密码/口令 (secrets 安全随机)

全部走标准库, 失败以 [tool] 前缀 + 可读信息回灌模型。
"""

import os
import re
import csv
import json
import time
import hmac
import secrets
import hashlib
import string

from lingmengwork.tools import fs


def _resolve(ctx, path):
    try:
        return str(fs.resolve_path(ctx.get("roots") or [], path).resolve())
    except Exception:
        return str(path)


# ---------------------------------------------------------------------------
# webhook_verify
# ---------------------------------------------------------------------------
def webhook_verify(args, ctx):
    """验证 webhook 签名 (HMAC-SHA256, 可选时间戳防重放)."""
    secret = args.get("secret") or ""
    payload = args.get("payload") or ""
    signature = (args.get("signature") or "").strip()
    ts = args.get("timestamp")
    tolerance = args.get("tolerance")
    tolerance = int(tolerance) if tolerance is not None else 300
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if not secret:
        return "[webhook_verify] 缺 secret, 无法验证。"
    if not signature:
        return "[webhook_verify] 缺 signature, 无法验证。"
    if signature.lower().startswith("sha256="):
        signature = signature[len("sha256="):]
    if ts is not None:
        try:
            ts = int(ts)
        except Exception:
            return "[webhook_verify] timestamp 非整数。"
        now = int(time.time())
        delta = abs(now - ts)
        replay = "重放风险!" if delta > tolerance else "正常"
        ts_note = "\n时间戳: %d (now=%d, 偏差=%ds, 容差=%ds, %s)" % (ts, now, delta, tolerance, replay)
        body = "%d.%s" % (ts, payload)
    else:
        ts_note = ""
        body = payload
    expect = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    ok = hmac.compare_digest(expect, signature)
    return ("[webhook_verify] %s\n期望签名: sha256=%s\n收到签名: %s%s"
            % ("签名有效 ✓" if ok else "签名无效 ✗", expect, signature, ts_note))


# ---------------------------------------------------------------------------
# sql_format
# ---------------------------------------------------------------------------
_KEYWORDS = [
    "select", "from", "where", "group by", "order by", "having", "limit",
    "left join", "right join", "inner join", "outer join", "cross join", "join",
    "on", "union all", "union", "insert into", "values", "update", "set",
    "delete from", "and", "or",
]


def _sql_format(sql):
    s = " " + " ".join(sql.split()) + " "
    for kw in _KEYWORDS:
        pattern = r"(?i)(?<!\w)(%s)(?!\w)" % re.escape(kw)
        s = re.sub(pattern, lambda m: "\n" + m.group(1).upper(), s)
    cur_indent = 0
    result = []
    for raw in s.split("\n"):
        stripped = raw.strip()
        if not stripped:
            continue
        result.append(("  " * cur_indent) + stripped)
        opens = stripped.count("(")
        closes = stripped.count(")")
        cur_indent += opens - closes
        if cur_indent < 0:
            cur_indent = 0
    return "\n".join(result)


def sql_format(args, ctx):
    """轻量 SQL 格式化: 关键字折行 + 括号层级缩进 (零依赖)."""
    sql = args.get("sql")
    if not sql and args.get("file"):
        p = _resolve(ctx, args["file"])
        if not os.path.exists(p):
            return "[sql_format] 文件不存在: %s" % p
        try:
            sql = open(p, encoding="utf-8").read()
        except Exception as e:
            return "[sql_format] 读文件失败: %s" % e
    if not sql or not sql.strip():
        return "[sql_format] 缺 sql 或 file。"
    return "[sql_format] 格式化结果:\n" + _sql_format(sql)


# ---------------------------------------------------------------------------
# csv_diff
# ---------------------------------------------------------------------------
def csv_diff(args, ctx):
    """对比两个 CSV: 按 key 列对齐, 输出新增/删除/修改报告."""
    a = _resolve(ctx, args.get("a") or "")
    b = _resolve(ctx, args.get("b") or "")
    key = args.get("key")
    if not os.path.exists(a):
        return "[csv_diff] 文件A不存在: %s" % a
    if not os.path.exists(b):
        return "[csv_diff] 文件B不存在: %s" % b
    try:
        with open(a, encoding="utf-8", newline="") as f:
            ra = list(csv.DictReader(f))
        with open(b, encoding="utf-8", newline="") as f:
            rb = list(csv.DictReader(f))
    except Exception as e:
        return "[csv_diff] 读 CSV 失败: %s" % e
    parts = ["[csv_diff] A=%d 行, B=%d 行" % (len(ra), len(rb))]
    if key:
        da = {r.get(key): r for r in ra}
        db = {r.get(key): r for r in rb}
        only_a = [k for k in da if k not in db]
        only_b = [k for k in db if k not in da]
        changes = []
        for k in da:
            if k in db and da[k] != db[k]:
                diff_cols = [c for c in da[k] if da[k].get(c) != db[k].get(c)]
                changes.append((k, diff_cols))
        parts.append("对齐键: %s" % key)
        parts.append("仅 A 有(%d): %s" % (len(only_a), only_a[:20]))
        parts.append("仅 B 有(%d): %s" % (len(only_b), only_b[:20]))
        parts.append("修改(%d):" % len(changes))
        for k, cols in changes[:20]:
            parts.append("  - %s 变更列: %s" % (k, cols))
    else:
        n = max(len(ra), len(rb))
        changes = []
        for i in range(n):
            xa = ra[i] if i < len(ra) else None
            xb = rb[i] if i < len(rb) else None
            if xa != xb:
                changes.append((i, xa, xb))
        parts.append("按行号差异(%d):" % len(changes))
        for i, xa, xb in changes[:20]:
            parts.append("  行%d: A=%s | B=%s" % (i, xa, xb))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# json_schema_validate
# ---------------------------------------------------------------------------
def json_schema_validate(args, ctx):
    """校验 JSON 是否符合简化 schema (type/required/properties/enum/items)."""
    data = args.get("data")
    schema = args.get("schema")
    if data is None and args.get("file"):
        p = _resolve(ctx, args["file"])
        if not os.path.exists(p):
            return "[json_schema_validate] 数据文件不存在: %s" % p
        data = open(p, encoding="utf-8").read()
    if schema is None and args.get("schema_file"):
        p = _resolve(ctx, args["schema_file"])
        if not os.path.exists(p):
            return "[json_schema_validate] schema 文件不存在: %s" % p
        schema = open(p, encoding="utf-8").read()
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as e:
            return "[json_schema_validate] 数据 JSON 解析失败: %s" % e
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except Exception as e:
            return "[json_schema_validate] schema JSON 解析失败: %s" % e
    if not isinstance(schema, dict):
        return "[json_schema_validate] schema 必须是对象。"
    errors = []
    _validate_node(data, schema, "$", errors)
    if errors:
        return "[json_schema_validate] 校验未通过 (%d 处):\n%s" % (len(errors), "\n".join(errors))
    return "[json_schema_validate] 校验通过 ✓"


def _validate_node(node, schema, path, errors):
    if not isinstance(schema, dict):
        return
    t = schema.get("type")
    if t == "string" and not isinstance(node, str):
        errors.append("%s: 期望 string, 实际 %s" % (path, type(node).__name__))
    elif t == "integer" and (isinstance(node, bool) or not isinstance(node, int)):
        errors.append("%s: 期望 integer, 实际 %s" % (path, type(node).__name__))
    elif t == "number" and (isinstance(node, bool) or not isinstance(node, (int, float))):
        errors.append("%s: 期望 number, 实际 %s" % (path, type(node).__name__))
    elif t == "boolean" and not isinstance(node, bool):
        errors.append("%s: 期望 boolean, 实际 %s" % (path, type(node).__name__))
    elif t == "array" and not isinstance(node, list):
        errors.append("%s: 期望 array, 实际 %s" % (path, type(node).__name__))
    elif t == "object" and not isinstance(node, dict):
        errors.append("%s: 期望 object, 实际 %s" % (path, type(node).__name__))
    if "enum" in schema and node not in schema["enum"]:
        errors.append("%s: 值 %r 不在枚举 %s" % (path, node, schema["enum"]))
    if t == "object" and isinstance(node, dict):
        for req in schema.get("required", []):
            if req not in node:
                errors.append("%s: 缺必需字段 %s" % (path, req))
        for pk, ps in schema.get("properties", {}).items():
            if pk in node:
                _validate_node(node[pk], ps, "%s.%s" % (path, pk), errors)
    if t == "array" and isinstance(node, list):
        items = schema.get("items")
        if items:
            for i, it in enumerate(node):
                _validate_node(it, items, "%s[%d]" % (path, i), errors)


# ---------------------------------------------------------------------------
# release_tag
# ---------------------------------------------------------------------------
def _parse_ver(v):
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", v or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def release_tag(args, ctx):
    """semver 解析/校验/比较/递增 (major.minor.patch)."""
    version = args.get("version") or ""
    bump = (args.get("bump") or "").lower()
    compare = args.get("compare")
    parsed = _parse_ver(version)
    if parsed is None:
        return "[release_tag] 非法版本号: %s (期望 x.y.z)" % version
    if bump:
        if bump not in ("major", "minor", "patch"):
            return "[release_tag] bump 须为 major/minor/patch。"
        maj, minr, pat = parsed
        if bump == "major":
            parsed = (maj + 1, 0, 0)
        elif bump == "minor":
            parsed = (maj, minr + 1, 0)
        else:
            parsed = (maj, minr, pat + 1)
        return "[release_tag] %s -> %d.%d.%d" % (version, parsed[0], parsed[1], parsed[2])
    if compare is not None:
        pc = _parse_ver(compare)
        if pc is None:
            return "[release_tag] 非法比较版本: %s" % compare
        if parsed > pc:
            rel = "%s 更新" % version
        elif parsed < pc:
            rel = "%s 更新" % compare
        else:
            rel = "相等"
        return "[release_tag] %s vs %s -> %s" % (version, compare, rel)
    return "[release_tag] 解析: %d.%d.%d (合法)" % parsed


# ---------------------------------------------------------------------------
# log_tail
# ---------------------------------------------------------------------------
def log_tail(args, ctx):
    """读日志尾部 N 行, 支持关键字过滤 (grep)."""
    p = _resolve(ctx, args.get("file") or "")
    if not os.path.exists(p):
        return "[log_tail] 文件不存在: %s" % p
    n = args.get("n")
    n = int(n) if n else 50
    grep = args.get("grep")
    ic = bool(args.get("ignore_case"))
    try:
        pat = re.compile(re.escape(grep), re.IGNORECASE if ic else 0) if grep else None
        matched = []
        seen = 0
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                seen += 1
                line = line.rstrip("\n")
                if pat and not pat.search(line):
                    continue
                matched.append(line)
        tail = matched[-n:]
        head = "[log_tail] 文件: %s (扫描 %d 行, 命中 %d, 取末尾 %d)\n" % (p, seen, len(matched), len(tail))
        return head + "\n".join(tail)
    except Exception as e:
        return "[log_tail] 读取失败: %s" % e


# ---------------------------------------------------------------------------
# password_generate
# ---------------------------------------------------------------------------
def password_generate(args, ctx):
    """生成强密码/口令 (secrets 安全随机, 可选可读模式)."""
    length = args.get("length")
    length = int(length) if length else 16
    count = args.get("count")
    count = int(count) if count else 1
    if length < 1 or count < 1:
        return "[password_generate] length/count 须 >= 1。"
    lower = args.get("lower", True)
    upper = args.get("upper", True)
    digit = args.get("digit", True)
    symbol = args.get("symbol", True)
    readable = bool(args.get("readable"))
    pool = ""
    if lower:
        pool += string.ascii_lowercase
    if upper:
        pool += string.ascii_uppercase
    if digit:
        pool += string.digits
    if symbol:
        pool += "!@#$%^&*()-_=+[]{}<>?"
    if readable:
        ambiguous = set("l1IoO0|`'\";:.,")
        pool = "".join(c for c in pool if c not in ambiguous)
    if not pool:
        return "[password_generate] 字符集为空, 请至少启用一类字符。"
    out = ["".join(secrets.choice(pool) for _ in range(length)) for _ in range(count)]
    return "[password_generate] 已生成 %d 个 (长度 %d%s):\n%s" % (
        count, length, " 可读模式" if readable else "", "\n".join(out))

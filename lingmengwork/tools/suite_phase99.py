"""Phase 99 工具套件: 集成 / 运维 / 数据增强 (零依赖优雅降级).

新增 7 工具:
  webhook_emit        发送 webhook (urllib POST, 超时保护, 支持 dry_run)
  sql_explain         提取 SQL 操作类型 / 表 / 列 (轻量正则解析)
  csv_to_json         CSV -> JSON 数组 (标准库 csv)
  hash_file           多算法文件哈希 (md5/sha1/sha256/sha512)
  cron_parse          cron 表达式解析 (中文描述 + 下次运行时间)
  text_diff           两文本行级统一 diff (difflib)
  yaml_query          极简 YAML 路径查询 (key.subkey[0])

全部走标准库, 失败以 [tool] 前缀 + 可读信息回灌模型。
"""

import os
import re
import csv
import json
import time
import hmac
import base64
import hashlib
import difflib
from datetime import datetime, timedelta

from lingmengwork.tools import fs


def _resolve(ctx, path):
    try:
        return str(fs.resolve_path(ctx.get("roots") or [], path).resolve())
    except Exception:
        return str(path)


# ---------------------------------------------------------------------------
# webhook_emit
# ---------------------------------------------------------------------------
def webhook_emit(args, ctx):
    """发送 webhook (POST), 可选 HMAC 签名与 dry_run 预演."""
    url = (args.get("url") or "").strip()
    if not url:
        return "[webhook_emit] 缺 url, 无法发送。"
    body = args.get("body")
    ctype = (args.get("content_type") or "application/json").strip()
    method = (args.get("method") or "POST").strip().upper()
    headers = dict(args.get("headers") or {})
    timeout = args.get("timeout")
    timeout = int(timeout) if timeout is not None else 8
    dry_run = bool(args.get("dry_run"))

    # 构造请求体
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if "json" in ctype or ctype == "application/json":
                ctype = "application/json; charset=utf-8"
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = str(body).encode("utf-8")

    secret = args.get("secret")
    signed = False
    if secret:
        ts = int(time.time())
        payload_for_sign = (data or b"").decode("utf-8", "replace")
        mac = hmac.new(secret.encode("utf-8"), (payload_for_sign + str(ts)).encode("utf-8"), hashlib.sha256)
        sig = mac.hexdigest()
        headers["X-Signature"] = "sha256=" + sig
        headers["X-Timestamp"] = str(ts)
        signed = True

    hdr_disp = ", ".join("%s: %s" % (k, v) for k, v in headers.items())
    size = len(data) if data is not None else 0
    preview = (data or b"").decode("utf-8", "replace")
    if len(preview) > 200:
        preview = preview[:200] + "...(truncated)"

    if dry_run:
        return (
            "[webhook_emit] dry_run 预演:\n"
            "method: %s\nurl: %s\ncontent_type: %s\nsigned: %s\n"
            "headers: %s\nbytes: %d\nbody: %s"
        ) % (method, url, ctype, ("yes" if signed else "no"), hdr_disp, size, preview)

    try:
        import urllib.request
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", ctype)
        for k, v in headers.items():
            req.add_header(k, str(v))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            raw = resp.read(4096).decode("utf-8", "replace")
        return "[webhook_emit] 已发送 -> HTTP %d\n%s" % (code, raw if raw else "(空响应)")
    except Exception as e:
        return "[webhook_emit] 发送失败: %s" % e


# ---------------------------------------------------------------------------
# sql_explain
# ---------------------------------------------------------------------------
_RE_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\*|\.|,|\(|\)")


def _strip_comments(sql):
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return sql


def sql_explain(args, ctx):
    """提取 SQL 的操作类型 / 涉及的表 / 列 (轻量解析, 非完整 SQL 引擎)."""
    sql = args.get("sql") or ""
    if not sql.strip():
        return "[sql_explain] 缺 sql。"
    raw = _strip_comments(sql)
    head = _RE_WS.sub(" ", raw).strip()
    up = head.upper()
    # 操作类型
    m = re.match(r"\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|WITH|REPLACE|MERGE)", up)
    op = m.group(1).lower() if m else "unknown"
    tables = []
    if op == "select":
        body = head[len("SELECT"):]
        mb = re.search(r"\bFROM\b", up)
        if mb:
            after = body[mb.start() - len("SELECT"):]
            after = after[5:]
            # 取 FROM 之后到 WHERE/GROUP/ORDER/JOIN/HAVING/LIMIT 之前的表
            after = re.split(r"\b(WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|INNER\s+JOIN|OUTER\s+JOIN|ON|UNION)\b", after, flags=re.I)[0]
            tables = _parse_tables(after)
    elif op in ("insert", "replace"):
        m2 = re.search(r"\bINTO\s+([A-Za-z_][\w.]*)", up)
        if m2:
            tables = [m2.group(1).lower()]
    elif op == "update":
        m2 = re.search(r"\bUPDATE\s+([A-Za-z_][\w.]*)", up)
        if m2:
            tables = [m2.group(1).lower()]
    elif op in ("create", "drop", "alter", "truncate"):
        m2 = re.search(r"\b(?:TABLE|VIEW|INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w.]*)", up)
        if m2:
            tables = [m2.group(1).lower()]
    else:
        # DELETE FROM
        m2 = re.search(r"\bFROM\s+([A-Za-z_][\w.]*)", up)
        if m2:
            tables = [m2.group(1).lower()]
    # 列提取 (SELECT 列表 / INSERT 列)
    cols = []
    if op == "select":
        sel_part = head[6:].strip()
        end = re.search(r"\bFROM\b", up)
        if end:
            sel_part = head[6:end.start()].strip()
            cols = _parse_cols(sel_part)
    elif op in ("insert", "replace"):
        m3 = re.search(r"\(([^)]*)\)\s*(?:VALUES|SELECT)", head, flags=re.I)
        if m3:
            cols = [c.strip().strip("`\"[]").lower() for c in m3.group(1).split(",") if c.strip()]
    return (
        "[sql_explain] 操作类型: %s\n表(%d): %s\n列(%d): %s"
        % (op, len(tables), ", ".join(tables) or "(未识别)", len(cols), ", ".join(cols) or "(未识别)")
    )


def _parse_tables(s):
    out = []
    for t in re.split(r",", s):
        t = t.strip()
        m = re.match(r"([A-Za-z_][\w.`]*)", t)
        if m:
            out.append(m.group(1).strip("`").lower())
    return out


def _parse_cols(s):
    if s.strip() == "*":
        return ["*"]
    out = []
    for c in re.split(r",", s):
        c = c.strip()
        if not c:
            continue
        m = re.match(r"(?:[A-Za-z_][\w.`]*\.)?([A-Za-z_][\w`]*)\s*(?:AS\s+[A-Za-z_]\w*)?$", c, flags=re.I)
        if m:
            out.append(m.group(1).strip("`").lower())
        else:
            out.append(c.split()[0].strip("`").lower())
    return out


# ---------------------------------------------------------------------------
# csv_to_json
# ---------------------------------------------------------------------------
def csv_to_json(args, ctx):
    """CSV -> JSON 数组 (每行一个对象)."""
    path = args.get("file") or args.get("path") or ""
    if not path:
        return "[csv_to_json] 缺 file。"
    p = _resolve(ctx, path)
    if not os.path.exists(p):
        return "[csv_to_json] 文件不存在: %s" % path
    try:
        delim = args.get("delimiter") or ","
        enc = args.get("encoding") or "utf-8-sig"
        with open(p, "r", encoding=enc, newline="") as f:
            reader = csv.DictReader(f, delimiter=delim)
            rows = [dict(r) for r in reader]
        return json.dumps(rows, ensure_ascii=False, indent=2)
    except Exception as e:
        return "[csv_to_json] 转换失败: %s" % e


# ---------------------------------------------------------------------------
# hash_file
# ---------------------------------------------------------------------------
def hash_file(args, ctx):
    """计算文件多算法哈希 (md5/sha1/sha256/sha512), 分块读取."""
    path = args.get("file") or args.get("path") or ""
    if not path:
        return "[hash_file] 缺 file。"
    p = _resolve(ctx, path)
    if not os.path.exists(p):
        return "[hash_file] 文件不存在: %s" % path
    algs = args.get("algorithms") or ["sha256"]
    if isinstance(algs, str):
        algs = [algs]
    # 兼容 "sha256,md5"
    expanded = []
    for a in algs:
        for x in str(a).split(","):
            x = x.strip()
            if x:
                expanded.append(x)
    valid = {"md5", "sha1", "sha256", "sha512"}
    expanded = [a for a in expanded if a in valid] or ["sha256"]
    try:
        hs = {a: hashlib.new(a) for a in expanded}
        size = 0
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                size += len(chunk)
                for h in hs.values():
                    h.update(chunk)
        out = "[hash_file] 文件: %s (%d bytes)\n" % (path, size)
        for a in expanded:
            out += "%s: %s\n" % (a, hs[a].hexdigest())
        return out.rstrip()
    except Exception as e:
        return "[hash_file] 计算失败: %s" % e


# ---------------------------------------------------------------------------
# cron_parse
# ---------------------------------------------------------------------------
_WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]


def _cron_field(val, lo, hi):
    """返回该字段允许的整数集合."""
    val = val.strip()
    out = set()
    for part in val.split(","):
        part = part.strip()
        if part == "*":
            out.update(range(lo, hi + 1))
            continue
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            part = base
        if part == "*":
            rng = range(lo, hi + 1)
        elif "-" in part:
            a, b = part.split("-", 1)
            rng = range(int(a), int(b) + 1)
        else:
            rng = [int(part)]
        out.update(rng[::step])
    return out


def cron_parse(args, ctx):
    """解析 cron 表达式 (5 段), 产出中文描述 + 下次运行时间."""
    expr = (args.get("expression") or "").strip()
    if not expr:
        return "[cron_parse] 缺 expression (格式: 分 时 日 月 周)."
    parts = expr.split()
    if len(parts) != 5:
        return "[cron_parse] 表达式需 5 段, 实得 %d 段." % len(parts)
    try:
        mins = _cron_field(parts[0], 0, 59)
        hrs = _cron_field(parts[1], 0, 23)
        doms = _cron_field(parts[2], 1, 31)
        mons = _cron_field(parts[3], 1, 12)
        dows = _cron_field(parts[4], 0, 7)  # 0/7=周日
    except Exception as e:
        return "[cron_parse] 字段解析失败: %s" % e

    desc = _cron_describe(parts, mins, hrs, doms, mons, dows)
    # 计算下次运行时间 (逐分钟步进, 上限 ~208 天)
    now = datetime.now()
    nxt = None
    for i in range(300000):
        t = now + timedelta(minutes=i)
        if t.month not in mons:
            continue
        if t.day not in doms:
            # 日与周是 OR 关系 (标准 cron)
            if t.weekday() + 1 not in dows and 0 not in dows and 7 not in dows:
                continue
        if t.hour not in hrs or t.minute not in mins:
            continue
        nxt = t
        break
    when = nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else "(208 天内无匹配)"
    return "[cron_parse] %s\n描述: %s\n下次运行: %s" % (expr, desc, when)


def _cron_describe(parts, mins, hrs, doms, mons, dows):
    def fmt(s):
        return ",".join(str(x) for x in sorted(s))
    mon_names = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"]
    mons_s = ",".join(mon_names[m - 1] for m in sorted(mons))
    dows_s = ",".join(_WEEK[d % 7] for d in sorted(dows))
    return "每月[%s] 在 [%s] 的 [%s] 点 [%s] 分触发 (星期[%s])" % (
        mons_s, fmt(doms), fmt(hrs), fmt(mins), dows_s,
    )


# ---------------------------------------------------------------------------
# text_diff
# ---------------------------------------------------------------------------
def text_diff(args, ctx):
    """两文本行级统一 diff (difflib.unified_diff)."""
    a = args.get("a") or ""
    b = args.get("b") or ""
    if isinstance(a, (list, tuple)):
        a = "\n".join(a)
    if isinstance(b, (list, tuple)):
        b = "\n".join(b)
    al = a.splitlines(keepends=True)
    bl = b.splitlines(keepends=True)
    name_a = args.get("name_a") or "a"
    name_b = args.get("name_b") or "b"
    diff = list(difflib.unified_diff(
        al, bl, fromfile=name_a, tofile=name_b,
        lineterm="",
    ))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    if not diff:
        return "[text_diff] 两文本完全相同 (无差异)."
    return "[text_diff] +%d/-%d 行\n%s" % (added, removed, "\n".join(diff))


# ---------------------------------------------------------------------------
# yaml_query
# ---------------------------------------------------------------------------
def yaml_query(args, ctx):
    """极简 YAML 路径查询: 支持 file 或 text, 路径如 a.b.c 或 a.list[0]."""
    path_arg = (args.get("path_q") or args.get("query") or "").strip()
    text = args.get("text")
    file_arg = args.get("file") or args.get("path") or ""
    if text is None and file_arg:
        p = _resolve(ctx, file_arg)
        if not os.path.exists(p):
            return "[yaml_query] 文件不存在: %s" % file_arg
        try:
            with open(p, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            return "[yaml_query] 读取失败: %s" % e
    if not text:
        return "[yaml_query] 缺 text 或 file。"
    try:
        data = _simple_yaml_load(text)
    except Exception as e:
        return "[yaml_query] 解析失败: %s" % e
    if not path_arg:
        return json.dumps(data, ensure_ascii=False, indent=2)
    try:
        val = _yaml_get(data, path_arg)
    except Exception as e:
        return "[yaml_query] 路径 %r 未命中: %s" % (path_arg, e)
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False, indent=2)
    return str(val)


def _yaml_get(node, path):
    cur = node
    # 拆分 a.b.c 与 a.list[0]
    for seg in re.split(r"(?<!\[)\.", path):
        seg = seg.strip()
        if not seg:
            continue
        m = re.match(r"^([^\[\]]+)((?:\[\d+\])+)$", seg)
        key = seg
        idxs = []
        if m:
            key = m.group(1)
            idxs = [int(x) for x in re.findall(r"\[(\d+)\]", m.group(2))]
        if isinstance(cur, dict):
            if key not in cur:
                raise KeyError(key)
            cur = cur[key]
        elif isinstance(cur, list):
            cur = cur[int(key)]
        else:
            raise TypeError("非容器节点")
        for ix in idxs:
            if not isinstance(cur, list) or ix >= len(cur):
                raise IndexError(ix)
            cur = cur[ix]
    return cur


def _simple_yaml_load(text):
    """极简 YAML 解析: 缩进 nested dict / list, 基本标量类型."""
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()
        lines.append((indent, content))
    pos, root = _yaml_parse_block(lines, 0, lines[0][0] if lines else 0)
    return root


def _yaml_parse_block(lines, start, indent):
    # 判断块是列表还是字典
    is_list = False
    for i in range(start, len(lines)):
        ind, c = lines[i]
        if ind < indent:
            break
        if ind == indent and c.startswith("- "):
            is_list = True
            break
    if is_list:
        result = []
        i = start
        while i < len(lines):
            ind, c = lines[i]
            if ind < indent:
                break
            if ind == indent and c.startswith("- "):
                item = c[2:].strip()
                if _is_kv(item):
                    # 该列表项是含 key 的对象
                    sub = {**_kv(item)}
                    i = _absorb_subkeys(lines, i + 1, indent + 2, sub)
                    result.append(sub)
                elif item.startswith("- "):
                    result.append(item[2:])
                    i += 1
                else:
                    result.append(_scalar(item))
                    i += 1
            else:
                i += 1
        return i, result
    else:
        result = {}
        i = start
        while i < len(lines):
            ind, c = lines[i]
            if ind < indent:
                break
            if ind == indent and _is_kv(c):
                k, v = _kv(c)
                if v == "":
                    # 嵌套块
                    nxt = i + 1
                    if nxt < len(lines) and lines[nxt][0] > indent:
                        child_indent = lines[nxt][0]
                        nxt, child = _yaml_parse_block(lines, nxt, child_indent)
                        result[k] = child
                        i = nxt
                    else:
                        result[k] = None
                        i += 1
                else:
                    result[k] = _scalar(v)
                    i += 1
            else:
                i += 1
        return i, result


def _absorb_subkeys(lines, start, indent, sub):
    i = start
    while i < len(lines):
        ind, c = lines[i]
        if ind < indent:
            break
        if ind == indent and _is_kv(c):
            k, v = _kv(c)
            if v == "":
                nxt = i + 1
                if nxt < len(lines) and lines[nxt][0] > indent:
                    ci = lines[nxt][0]
                    nxt, child = _yaml_parse_block(lines, nxt, ci)
                    sub[k] = child
                    i = nxt
                else:
                    sub[k] = None
                    i += 1
            else:
                sub[k] = _scalar(v)
                i += 1
        else:
            i += 1
    return i


def _is_kv(s):
    return bool(re.match(r"^[A-Za-z_][\w-]*\s*:", s)) or ": " in s or s.endswith(":")


def _kv(s):
    if ":" in s:
        k, v = s.split(":", 1)
        return k.strip(), v.strip()
    return s.strip(), ""


def _scalar(v):
    v = v.strip().strip('"').strip("'")
    if v in ("true", "True", "yes", "Yes"):
        return True
    if v in ("false", "False", "no", "No"):
        return False
    if v in ("null", "None", "~", ""):
        return None if v in ("null", "None", "~") else v
    if re.match(r"^-?\d+$", v):
        return int(v)
    if re.match(r"^-?\d+\.\d+$", v):
        return float(v)
    return v

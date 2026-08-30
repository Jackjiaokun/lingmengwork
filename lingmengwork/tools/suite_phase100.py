# -*- coding: utf-8 -*-
"""Phase 100 工具套件 (里程碑): 集成 / 运维 / 数据增强 (零依赖优雅降级).

新增 7 工具:
  webhook_dispatch    按事件路由到多目标 webhook 并发发送 (urllib POST)
  sql_lint            轻量 SQL 静态检查 (SELECT */缺 WHERE/关键字大小写等)
  json_schema_gen    由样本 JSON 推断 JSON Schema
  cron_next_n         cron 表达式 -> 接下来 N 次运行时间
  diff_patch         将统一 diff 应用到文本, 产出打补丁后文本
  yaml_merge          两份 YAML 深度合并
  hash_verify         验证文件哈希与期望值是否一致

全部走标准库, 失败以 [tool] 前缀 + 可读信息回灌模型。
各函数为自包含实现, 不跨模块依赖私有 helper, 保证 suite 独立可测。
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
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from lingmengwork.tools import fs


def _resolve(ctx, path):
    try:
        return str(fs.resolve_path(ctx.get("roots") or [], path).resolve())
    except Exception:
        return str(path)


# ---------------------------------------------------------------------------
# webhook_dispatch
# ---------------------------------------------------------------------------
def webhook_dispatch(args, ctx):
    """按事件路由到多个 webhook 目标并发送.

    args:
      event        事件名 (如 "push" / "deploy")
      routes       {event: url} 或 {event: [url,...]} 路由表
      body         请求体 (dict/list/str)
      secret?      可选 HMAC 密钥 (对所有目标签名 X-Signature)
      timeout?     单目标超时秒 (默认 8)
      dry_run?     仅预演不真正发送
    """
    event = (args.get("event") or "").strip()
    routes = args.get("routes") or {}
    if not event:
        return "[webhook_dispatch] 缺 event, 无法路由。"
    if not isinstance(routes, dict):
        return "[webhook_dispatch] routes 须为 {event: url} 映射."
    targets = routes.get(event)
    if targets is None:
        return "[webhook_dispatch] 事件 %r 无匹配目标." % event
    if isinstance(targets, str):
        targets = [targets]
    if not isinstance(targets, (list, tuple)) or not targets:
        return "[webhook_dispatch] 事件 %r 目标为空." % event

    body = args.get("body")
    ctype = "application/json; charset=utf-8"
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
            ctype = "text/plain; charset=utf-8"
    secret = args.get("secret")
    timeout = args.get("timeout")
    timeout = int(timeout) if timeout is not None else 8
    dry_run = bool(args.get("dry_run"))

    results = []
    ts = int(time.time())
    for url in targets:
        url = (url or "").strip()
        if not url:
            results.append({"url": url, "ok": False, "error": "空 url"})
            continue
        headers = {"Content-Type": ctype}
        if secret:
            raw = (data or b"")
            sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
            headers["X-Signature"] = "sha256=" + sig
            headers["X-Timestamp"] = str(ts)
        if dry_run:
            results.append({"url": url, "ok": True, "dry_run": True, "bytes": len(data or b"")})
            continue
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = resp.getcode()
                results.append({"url": url, "ok": 200 <= code < 300, "status": code})
        except urllib.error.HTTPError as e:
            results.append({"url": url, "ok": False, "status": e.code, "error": str(e)})
        except Exception as e:
            results.append({"url": url, "ok": False, "error": str(e)})
    ok_n = sum(1 for r in results if r.get("ok"))
    summary = "[webhook_dispatch] event=%s 已发送 %d/%d 目标:\n" % (event, ok_n, len(results))
    for r in results:
        summary += "  - %s ok=%s %s\n" % (r.get("url"), r.get("ok"),
                                          r.get("status") or r.get("error") or r.get("dry_run") and "dry_run" or "")
    return summary.strip()


# ---------------------------------------------------------------------------
# sql_lint
# ---------------------------------------------------------------------------
def sql_lint(args, ctx):
    """轻量 SQL 静态检查, 返回告警列表 (零依赖, 正则 + 关键字)."""
    sql = (args.get("sql") or "").strip()
    if not sql:
        return "[sql_lint] 缺 sql."
    warnings = []
    upper = sql.upper()
    # 1) SELECT *
    if re.search(r"SELECT\s+\*\s+FROM", upper):
        warnings.append("避免使用 SELECT * (应显式列出列, 便于索引与schema 演进)")
    # 2) UPDATE / DELETE 缺 WHERE
    for kw in ("UPDATE", "DELETE"):
        m = re.search(r"\b%s\b" % kw, upper)
        if m:
            # 取该语句后续片段
            tail = upper[m.end():]
            stmt_end = tail.find(";")
            if stmt_end >= 0:
                tail = tail[:stmt_end]
            if " WHERE " not in (" " + tail):
                warnings.append("%s 语句缺少 WHERE 条件 (全表更新/删除风险)" % kw)
    # 3) INSERT 未指定列
    for m in re.finditer(r"\bINSERT\s+INTO\s+(\w+)", upper):
        tail = upper[m.end():]
        e = tail.find("VALUES")
        cols = tail[:e] if e >= 0 else tail
        if "(" not in cols:
            warnings.append("INSERT INTO %s 未指定列名 (列顺序依赖表结构, 易错)" % m.group(1))
    # 4) 关键字小写 (风格)
    kw_list = ["select", "from", "where", "insert", "update", "delete", "join", "group by", "order by"]
    low = sql.lower()
    for kw in kw_list:
        if kw in low and kw.upper() not in upper:
            pass
    # 关键字大小写: 检查常见关键字是否小写出现
    for kw in ("select", "from", "where", "and", "or", "join", "update", "delete", "insert", "set", "values"):
        if re.search(r"\b%s\b" % kw, sql):
            warnings.append("关键字建议大写: 发现小写 %r" % kw)
            break
    # 5) 缺 LIMIT 的 SELECT
    if re.search(r"\bSELECT\b", upper) and " LIMIT " not in upper and " TOP " not in upper:
        warnings.append("SELECT 未限制行数 (建议加 LIMIT, 避免大结果集)")
    if not warnings:
        return "[sql_lint] 未发现明显问题 (通过)."
    return "[sql_lint] 发现 %d 项告警:\n%s" % (len(warnings), "\n".join("  - " + w for w in warnings))


# ---------------------------------------------------------------------------
# json_schema_gen
# ---------------------------------------------------------------------------
def json_schema_gen(args, ctx):
    """由样本 JSON 推断 JSON Schema (type/required/properties/items)."""
    text = args.get("text")
    file_arg = args.get("file") or args.get("path") or ""
    if text is None and file_arg:
        p = _resolve(ctx, file_arg)
        if not os.path.exists(p):
            return "[json_schema_gen] 文件不存在: %s" % file_arg
        try:
            with open(p, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            return "[json_schema_gen] 读取失败: %s" % e
    if not text:
        return "[json_schema_gen] 缺 text 或 file."
    try:
        sample = json.loads(text)
    except Exception as e:
        return "[json_schema_gen] JSON 解析失败: %s" % e

    def infer(v):
        if isinstance(v, bool):
            return {"type": "boolean"}
        if v is None:
            return {"type": "null"}
        if isinstance(v, str):
            return {"type": "string"}
        if isinstance(v, int):
            return {"type": "integer"}
        if isinstance(v, float):
            return {"type": "number"}
        if isinstance(v, list):
            if v:
                sub = infer(v[0])
                return {"type": "array", "items": sub}
            return {"type": "array", "items": {}}
        if isinstance(v, dict):
            props = {}
            required = []
            for k, val in v.items():
                props[k] = infer(val)
                required.append(k)
            return {"type": "object", "properties": props, "required": required}
        return {"type": "string"}

    schema = infer(sample)
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    return "[json_schema_gen]\n" + json.dumps(schema, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# cron_next_n
# ---------------------------------------------------------------------------
_WEEK = ["日", "一", "二", "三", "四", "五", "六"]


def _cron_field(part, lo, hi):
    out = set()
    for seg in part.split(","):
        seg = seg.strip()
        if not seg:
            continue
        step = 1
        if "/" in seg:
            base, step_s = seg.split("/", 1)
            step = int(step_s)
            seg = base
        if seg == "*":
            rng = range(lo, hi + 1)
        elif "-" in seg:
            a, b = seg.split("-", 1)
            rng = range(int(a), int(b) + 1)
        else:
            rng = [int(seg)]
        out.update(rng[::step])
    return out


def cron_next_n(args, ctx):
    """cron 表达式 -> 接下来 N 次运行时间 (N 默认 5)."""
    expr = (args.get("expression") or "").strip()
    if not expr:
        return "[cron_next_n] 缺 expression (格式: 分 时 日 月 周)."
    parts = expr.split()
    if len(parts) != 5:
        return "[cron_next_n] 表达式需 5 段, 实得 %d 段." % len(parts)
    try:
        mins = _cron_field(parts[0], 0, 59)
        hrs = _cron_field(parts[1], 0, 23)
        doms = _cron_field(parts[2], 1, 31)
        mons = _cron_field(parts[3], 1, 12)
        dows = _cron_field(parts[4], 0, 7)
    except Exception as e:
        return "[cron_next_n] 字段解析失败: %s" % e
    n = args.get("count")
    n = int(n) if n is not None else 5
    times = []
    now = datetime.now()
    for i in range(300000):
        t = now + timedelta(minutes=i)
        if t.month not in mons:
            continue
        if t.day not in doms:
            if t.weekday() + 1 not in dows and 0 not in dows and 7 not in dows:
                continue
        if t.hour not in hrs or t.minute not in mins:
            continue
        times.append(t.strftime("%Y-%m-%d %H:%M"))
        if len(times) >= n:
            break
    if not times:
        return "[cron_next_n] %s 在 208 天内无 %d 次匹配." % (expr, n)
    return "[cron_next_n] %s 接下来 %d 次:\n%s" % (expr, len(times), "\n".join("  " + x for x in times))


# ---------------------------------------------------------------------------
# diff_patch
# ---------------------------------------------------------------------------
def diff_patch(args, ctx):
    """将统一 diff 应用到文本, 产出打补丁后的文本.

    args:
      original   原始文本 (或行列表)
      patch      统一 diff 文本
      out_file?  可选, 写入目标文件 (否则仅返回结果)
    """
    original = args.get("original") or ""
    patch_text = args.get("patch") or ""
    if isinstance(original, (list, tuple)):
        o_lines = [str(x) for x in original]
    else:
        o_lines = original.split("\n")
    if not patch_text.strip():
        return "[diff_patch] 缺 patch 文本."

    out = []
    src = o_lines[:]
    p_lines = patch_text.split("\n")
    i = 0
    while i < len(p_lines) and not p_lines[i].startswith("@@"):
        i += 1
    cur = 0
    while i < len(p_lines):
        line = p_lines[i]
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not m:
                i += 1
                continue
            old_start = int(m.group(1)) - 1
            while cur < old_start:
                out.append(src[cur]); cur += 1
            i += 1
            while i < len(p_lines) and not p_lines[i].startswith("@@"):
                h = p_lines[i]
                if h.startswith("+") and not h.startswith("+++"):
                    out.append(h[1:])
                elif h.startswith("-") and not h.startswith("---"):
                    cur += 1
                elif h == "":
                    out.append(""); cur += 1
                elif h.startswith(" "):
                    out.append(h[1:]); cur += 1
                else:
                    out.append(h); cur += 1
                i += 1
            continue
        i += 1
    while cur < len(src):
        out.append(src[cur]); cur += 1

    result = "\n".join(out)
    out_file = args.get("out_file") or args.get("file") or ""
    if out_file:
        p = _resolve(ctx, out_file)
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(result)
            return "[diff_patch] 已写入 %s (%d 行)." % (out_file, len(out))
        except Exception as e:
            return "[diff_patch] 写入失败: %s" % e
    return "[diff_patch]\n" + result


# ---------------------------------------------------------------------------
# yaml_merge
# ---------------------------------------------------------------------------
def _yaml_scalar(v):
    v = v.strip()
    if v == "" or v in ("~", "null", "Null", "NULL"):
        return None
    if v in ("true", "True", "TRUE"):
        return True
    if v in ("false", "False", "FALSE"):
        return False
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _yaml_block(lines, start, indent):
    is_list = False
    for ind, c in lines[start:]:
        if ind < indent:
            break
        if ind == indent and c.startswith("- "):
            is_list = True
            break
    if is_list:
        res = []
        i = start
        while i < len(lines):
            ind, c = lines[i]
            if ind < indent:
                break
            if ind == indent and c.startswith("- "):
                item = c[2:].strip()
                if ":" in item and not item.startswith(("'", '"')):
                    k, v = item.split(":", 1)
                    k, v = k.strip(), v.strip()
                    sub = {}
                    if v == "":
                        nxt = i + 1
                        if nxt < len(lines) and lines[nxt][0] > indent:
                            nxt, child = _yaml_block(lines, nxt, lines[nxt][0])
                            sub[k] = child
                            i = nxt
                        else:
                            sub[k] = None
                            i += 1
                    else:
                        sub[k] = _yaml_scalar(v)
                        i += 1
                    res.append(sub)
                else:
                    res.append(_yaml_scalar(item))
                    i += 1
            else:
                i += 1
        return i, res
    res = {}
    i = start
    while i < len(lines):
        ind, c = lines[i]
        if ind < indent:
            break
        if ind == indent and ":" in c and not c.startswith(("'", '"')):
            k, v = c.split(":", 1)
            k, v = k.strip(), v.strip()
            if v == "":
                nxt = i + 1
                if nxt < len(lines) and lines[nxt][0] > indent:
                    nxt, child = _yaml_block(lines, nxt, lines[nxt][0])
                    res[k] = child
                    i = nxt
                else:
                    res[k] = None
                    i += 1
            else:
                res[k] = _yaml_scalar(v)
                i += 1
        else:
            i += 1
    return i, res


def _yaml_load_min(text):
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip()))
    if not lines:
        return {}
    _, val = _yaml_block(lines, 0, lines[0][0])
    return val


def _yaml_deep_merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            if k in out:
                out[k] = _yaml_deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    if isinstance(base, list) and isinstance(over, list):
        return base + over
    return over


def yaml_merge(args, ctx):
    """深度合并两份 YAML (a 作为基底, b 覆盖/扩展)."""
    a = args.get("a") or args.get("yaml_a") or ""
    b = args.get("b") or args.get("yaml_b") or ""
    file_a = args.get("file_a") or ""
    file_b = args.get("file_b") or ""
    if not a and file_a:
        p = _resolve(ctx, file_a)
        if not os.path.exists(p):
            return "[yaml_merge] 文件 a 不存在: %s" % file_a
        a = open(p, "r", encoding="utf-8").read()
    if not b and file_b:
        p = _resolve(ctx, file_b)
        if not os.path.exists(p):
            return "[yaml_merge] 文件 b 不存在: %s" % file_b
        b = open(p, "r", encoding="utf-8").read()
    if not a or not b:
        return "[yaml_merge] 需要 a/b 两份 YAML (或 file_a/file_b)."
    try:
        da = _yaml_load_min(a)
        db = _yaml_load_min(b)
    except Exception as e:
        return "[yaml_merge] 解析失败: %s" % e
    merged = _yaml_deep_merge(da, db)
    out_file = args.get("out_file") or ""
    if out_file:
        p = _resolve(ctx, out_file)
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            return "[yaml_merge] 已写入 %s." % out_file
        except Exception as e:
            return "[yaml_merge] 写入失败: %s" % e
    return "[yaml_merge]\n" + json.dumps(merged, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# hash_verify
# ---------------------------------------------------------------------------
def hash_verify(args, ctx):
    """校验文件哈希是否与期望值一致.

    args:
      file        文件路径
      expected    期望哈希值 (hex)
      algo?       md5/sha1/sha256/sha512 (默认 sha256)
    """
    file_arg = args.get("file") or args.get("path") or ""
    expected = (args.get("expected") or "").strip().lower()
    algo = (args.get("algo") or "sha256").strip().lower()
    if not file_arg:
        return "[hash_verify] 缺 file."
    if not expected:
        return "[hash_verify] 缺 expected (期望哈希值)."
    if algo not in ("md5", "sha1", "sha256", "sha512"):
        return "[hash_verify] 不支持的 algo: %s" % algo
    p = _resolve(ctx, file_arg)
    if not os.path.exists(p):
        return "[hash_verify] 文件不存在: %s" % file_arg
    h = hashlib.new(algo)
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception as e:
        return "[hash_verify] 读取失败: %s" % e
    got = h.hexdigest()
    ok = got.lower() == expected
    return "[hash_verify] %s %s\n实际: %s\n期望: %s" % (algo, "一致✓" if ok else "不一致✗", got, expected)

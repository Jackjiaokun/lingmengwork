"""Phase 109 工具套件: 令牌/URL 解析、文档与文本处理、依赖与环境检查 (零依赖).

全部工具仅用标准库, 输入为文本, 失败以 [tool] 前缀优雅降级回灌模型。
- jwt_decode       : JWT 解码 (header/payload/过期检查, 不校验签名)
- url_parse        : URL 结构化解析 (协议/主机/端口/路径/查询串, 密码自动掩码)
- markdown_toc     : Markdown 目录生成 (跳过代码块, GitHub 风格锚点)
- text_stats       : 文本统计 (字符/行数/英文词/中日韩字/UTF-8 字节/高频词)
- csv_to_markdown  : CSV 转 Markdown 表格 (对齐方式可配)
- env_lint         : .env 语法检查 (重复键/非法键名/空值/未引号空格/行内注释)
- requirements_diff: 依赖清单差分 (新增/移除/版本变更)

标准库: json / re / csv / io / base64 / time / collections / urllib.parse
"""

import json
import re
import csv
import io
import base64
import time
from collections import Counter
from urllib.parse import urlparse, parse_qs


# ---------------------------------------------------------------------------
# jwt_decode
# ---------------------------------------------------------------------------
def _b64_decode_json(seg):
    pad = "=" * (-len(seg) % 4)
    try:
        raw = base64.urlsafe_b64decode(seg + pad)
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        return {"_decode_error": str(e)}


def jwt_decode(args, ctx):
    token = (args.get("token") or "").strip()
    if not token:
        return "[jwt_decode] 缺少 token."
    parts = token.split(".")
    if len(parts) != 3:
        return "[jwt_decode] JWT 格式错误 (应为 header.payload.signature 三段, 实际 %d 段)." % len(parts)
    header = _b64_decode_json(parts[0])
    payload = _b64_decode_json(parts[1])
    out = {
        "header": header,
        "payload": payload,
        "signature_present": bool(parts[2]),
        "note": "仅解码, 未校验签名",
    }
    if isinstance(payload, dict):
        for key, label in (("exp", "expired"), ("nbf", "not_before")):
            ts = payload.get(key)
            if isinstance(ts, (int, float)):
                now = time.time()
                if key == "exp":
                    out["expired"] = now > ts
                    out["expires_in_sec"] = int(ts - now)
                else:
                    out["not_yet_valid"] = now < ts
                    out["valid_in_sec"] = int(ts - now)
    return json.dumps(out, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# url_parse
# ---------------------------------------------------------------------------
def url_parse(args, ctx):
    raw = (args.get("url") or "").strip()
    if not raw:
        return "[url_parse] 缺少 url."
    try:
        u = urlparse(raw)
        pairs = parse_qs(u.query, keep_blank_values=True)
        query = {k: (v[0] if len(v) == 1 else v) for k, v in pairs.items()}
        # netloc 里的明文密码同样必须掩码, 否则会经 netloc 字段回灌上下文造成泄露
        netloc = u.netloc
        if u.password:
            netloc = netloc.replace(":%s@" % u.password, ":***@")
        out = {
            "scheme": u.scheme,
            "netloc": netloc,
            "host": u.hostname,
            "port": u.port,
            "path": u.path,
            "params": u.params,
            "query": query,
            "fragment": u.fragment,
            "username": u.username,
            # 密码一律掩码, 避免凭据回灌上下文
            "password": "***" if u.password else None,
        }
        return json.dumps(out, ensure_ascii=False, indent=2)
    except Exception as e:
        return "[url_parse] 解析失败: %s" % e


# ---------------------------------------------------------------------------
# markdown_toc
# ---------------------------------------------------------------------------
def markdown_toc(args, ctx):
    raw = args.get("markdown") or ""
    try:
        max_level = int(args.get("max_level") or 3)
    except Exception:
        max_level = 3
    max_level = max(1, min(6, max_level))
    out = []
    in_fence = False
    for ln in raw.splitlines():
        stripped = ln.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", ln)
        if not m:
            continue
        level = len(m.group(1))
        if level > max_level:
            continue
        title = m.group(2).strip()
        anchor = title.lower()
        anchor = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", anchor)
        anchor = anchor.strip().replace(" ", "-")
        out.append("%s- [%s](#%s)" % ("  " * (level - 1), title, anchor))
    if not out:
        return "(未找到标题)"
    return "\n".join(out)


# ---------------------------------------------------------------------------
# text_stats
# ---------------------------------------------------------------------------
def text_stats(args, ctx):
    raw = args.get("text") or ""
    if not raw:
        return "[text_stats] 空文本."
    lines = raw.splitlines()
    freq = Counter(w.lower() for w in re.findall(r"[A-Za-z]{2,}", raw))
    out = {
        "chars": len(raw),
        "chars_no_whitespace": len(re.sub(r"\s", "", raw)),
        "lines": len(lines),
        "lines_non_empty": sum(1 for x in lines if x.strip()),
        "words_en": len(re.findall(r"[A-Za-z]{2,}", raw)),
        "chars_cjk": len(re.findall(r"[\u4e00-\u9fff]", raw)),
        "bytes_utf8": len(raw.encode("utf-8")),
        "top_words": [{"word": w, "count": c} for w, c in freq.most_common(10)],
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# csv_to_markdown
# ---------------------------------------------------------------------------
def csv_to_markdown(args, ctx):
    raw = args.get("csv") or ""
    delim = args.get("delimiter") or ","
    align = (args.get("align") or "left").strip().lower()
    try:
        rows = [r for r in csv.reader(io.StringIO(raw), delimiter=delim)]
    except Exception as e:
        return "[csv_to_markdown] CSV 解析失败: %s" % e
    if not rows:
        return "(空)"
    header, body = rows[0], rows[1:]
    n = len(header)
    marker = {"left": ":---", "center": ":---:", "right": "---:"}.get(align, ":---")
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join([marker] * n) + " |",
    ]
    for r in body:
        cells = (list(r) + [""] * n)[:n]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# env_lint
# ---------------------------------------------------------------------------
def env_lint(args, ctx):
    raw = args.get("env") or ""
    issues = []
    seen = {}
    for i, ln in enumerate(raw.splitlines(), 1):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[len("export "):].strip()
        if "=" not in s:
            issues.append({"line": i, "type": "no_equals", "detail": s[:40]})
            continue
        key, val = s.split("=", 1)
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            issues.append({"line": i, "type": "bad_key", "detail": key[:40]})
        if key in seen:
            issues.append({"line": i, "type": "duplicate_key",
                           "detail": "%s (首次出现: 第 %d 行)" % (key, seen[key])})
        else:
            seen[key] = i
        val = val.strip()
        quoted = (len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'")
        if val == "":
            issues.append({"line": i, "type": "empty_value", "detail": key})
        elif not quoted:
            if " " in val:
                issues.append({"line": i, "type": "unquoted_space", "detail": key})
            if "#" in val:
                issues.append({"line": i, "type": "inline_comment", "detail": key})
    return json.dumps({"keys": len(seen), "issues": issues, "ok": not issues},
                      ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# requirements_diff
# ---------------------------------------------------------------------------
def _parse_requirements(txt):
    d = {}
    for ln in txt.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("-"):
            continue
        s = re.split(r"\s+#", s)[0].strip()      # 去行内注释
        s = re.split(r"[;\[]", s)[0].strip()     # 去环境标记
        for op in ("===", "==", ">=", "<=", "~=", "!=", ">", "<"):
            if op in s:
                name, ver = s.split(op, 1)
                d[name.strip().lower()] = op + ver.strip()
                break
        else:
            if s:
                d[s.lower()] = ""
    return d


def requirements_diff(args, ctx):
    try:
        a = _parse_requirements(args.get("a") or "")
        b = _parse_requirements(args.get("b") or "")
    except Exception as e:
        return "[requirements_diff] 解析失败: %s" % e
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = [{"name": k, "from": a[k], "to": b[k]}
               for k in sorted(set(a) & set(b)) if a[k] != b[k]]
    out = {
        "added": [{"name": n, "spec": b[n]} for n in added],
        "removed": [{"name": n, "spec": a[n]} for n in removed],
        "changed": changed,
        "unchanged": len(set(a) & set(b)) - len(changed),
    }
    return json.dumps(out, ensure_ascii=False, indent=2)

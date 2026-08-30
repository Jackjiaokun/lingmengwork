"""协作 / 运维 / 文档增强工具集 (Phase 96): 对标主流产品的团队/合规/发布/检索差异化能力。

覆盖:
  - agent_team_run       : 执行 agent_team 生成的团队清单(落盘各 agent prompt + 调度计划), 供主控派发
  - pdf_redact           : PDF 脱敏(遮盖关键词/正则, pypdf 优先, 无库优雅降级)
  - db_schema_doc        : SQLite schema 文档生成(标准库 sqlite3, 零依赖)
  - form_validate        : 表单/数据校验(零依赖规则引擎: 必填/类型/正则/枚举/范围)
  - release_notes        : 发布说明生成(零依赖, 接受 changes 或读取 CHANGELOG)
  - code_search_semantic : 语义代码搜索(零依赖 TF-IDF 跨文件)
  - template_render      : 模板渲染(零依赖 {{var}} / {% for %} 循环)

设计纪律(与既有 suite_* 一致):
  - 工具函数签名统一 def name(args, ctx) -> str
  - 路径经 common.resolve_path 落域防护
  - 零硬依赖: PDF 脱敏优先外部引擎, 缺失自动降级并提示; 其余纯标准库
  - 失败信息以 [tool] 前缀回灌模型, 让其自我修复, 而非抛异常中断
"""

import os
import re
import json
import time
import math
import datetime
import sqlite3
import collections

from .common import resolve_path


# ============================================================================
# 公共辅助
# ============================================================================
def _roots(ctx):
    return ctx.get("roots") or ["."]


def _cwd(ctx):
    return ctx.get("cwd") or (str(_roots(ctx)[0]) if _roots(ctx) else ".")


def _resolve(ctx, path):
    return resolve_path(_roots(ctx), path)


def _trim(text, limit=20000):
    text = text.strip() if isinstance(text, str) else str(text)
    try:
        n = int(limit)
    except Exception:
        n = 20000
    if len(text) <= n:
        return text
    return text[:n] + "\n... (已截断, 共 %d 字符)" % len(text)


def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ============================================================================
# agent_team_run — 团队清单执行 / 派发落地
# ============================================================================
def agent_team_run(args, ctx):
    """读取 agent_team 生成的团队清单, 落盘各 agent 的派发 prompt + 调度计划, 供主控 AgentLoop 派发。"""
    spec = None
    team = args.get("team")
    if team:
        tp = _resolve(ctx, team)
        if os.path.exists(tp):
            try:
                spec = json.load(open(tp, encoding="utf-8"))
            except Exception as e:
                return "[agent_team_run] 无法读取团队清单 %s: %s" % (tp, e)
    if spec is None:
        team_dir = os.path.join(_cwd(ctx), ".lmw_team")
        if os.path.isdir(team_dir):
            files = sorted(
                (f for f in os.listdir(team_dir)
                 if f.startswith("team_") and f.endswith(".json")),
                reverse=True)
            if files:
                try:
                    spec = json.load(open(os.path.join(team_dir, files[0]), encoding="utf-8"))
                except Exception:
                    spec = None
    if spec is None:
        raw = args.get("spec") or args.get("agents")
        if raw:
            try:
                spec = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                spec = None
    if not isinstance(spec, dict) or not spec.get("agents"):
        return "[agent_team_run] 未找到团队清单(请先调用 agent_team 生成, 或传 team/spec)"

    agents = spec.get("agents", [])
    strategy = spec.get("strategy", "parallel")
    aggregator = spec.get("aggregator", "merge")
    if strategy not in ("parallel", "sequential", "debate"):
        strategy = "parallel"

    out_dir = os.path.join(_cwd(ctx), ".lmw_team")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass
    ts = int(time.time())

    prompts = []
    for a in agents:
        aid = a.get("id", "agent")
        role = a.get("role", aid)
        task = a.get("task", "")
        pfile = os.path.join(out_dir, "%s_prompt.md" % aid)
        k = 1
        while os.path.exists(pfile):
            pfile = os.path.join(out_dir, "%s_%d_prompt.md" % (aid, k))
            k += 1
        content = (
            "# Agent 调度指令: %s (role=%s)\n\n"
            "## 任务\n%s\n\n"
            "## 策略上下文\n"
            "- 全局策略: %s\n- 聚合器: %s\n"
            "- 模型: %s\n- 授权工具: %s\n"
            "- 所属团队: 共 %d 个成员\n"
        ) % (aid, role, task, strategy, aggregator,
             a.get("model") or "默认", a.get("tools") or "全部", len(agents))
        try:
            with open(pfile, "w", encoding="utf-8") as f:
                f.write(content)
            prompts.append((aid, pfile))
        except Exception:
            prompts.append((aid, None))

    if strategy == "sequential":
        order = [a.get("id", "agent") for a in agents]
    elif strategy == "debate":
        order = [a.get("id", "agent") for a in agents]
    else:
        order = [a.get("id", "agent") for a in agents]

    plan_lines = ["[agent_team_run] 团队执行清单已落地"]
    plan_lines.append("- 策略: %s | 聚合器: %s | 成员: %d" % (strategy, aggregator, len(agents)))
    plan_lines.append("- 调度顺序: %s" % " -> ".join(order))
    for aid, pf in prompts:
        plan_lines.append("  - %s -> prompt: %s" % (aid, pf))
    if strategy == "debate":
        rounds = max(1, int(args.get("rounds") or spec.get("rounds") or 2))
        plan_lines.append("- 辩论回合: %d 轮, 每轮所有成员依次发言并引用上一轮结论" % rounds)
    plan_lines.append("- 派发方式: 主控 AgentLoop 依次 read_file 上述 prompt, "
                       "调用 subagent 工具派发执行, 最后按聚合器(%s)汇总。" % aggregator)

    plan_text = "\n".join(plan_lines)
    plan_file = os.path.join(out_dir, "run_plan_%d.md" % ts)
    try:
        with open(plan_file, "w", encoding="utf-8") as f:
            f.write(plan_text)
        plan_lines.append("- 执行计划已写入: %s" % plan_file)
    except Exception:
        pass
    return "\n".join(plan_lines)


# ============================================================================
# pdf_redact — PDF 脱敏 (pypdf 优先, 无库降级)
# ============================================================================
def pdf_redact(args, ctx):
    src = args.get("file") or args.get("path")
    if not src:
        return "[pdf_redact] 缺 file 参数(待脱敏 PDF 路径)"
    src = _resolve(ctx, src)
    if not os.path.exists(src):
        return "[pdf_redact] 文件不存在: %s" % src

    terms = args.get("terms") or args.get("keywords") or []
    if isinstance(terms, str):
        terms = [terms]
    regex_mode = bool(args.get("regex"))
    if not terms and args.get("regex"):
        terms = [args["regex"]]
    if not terms:
        return "[pdf_redact] 缺 terms(待遮盖关键词列表) 或 regex 参数"

    out = _resolve(ctx, args.get("out", "redacted.pdf"))

    try:
        from pypdf import PdfReader, PdfWriter
    except Exception:
        try:
            from PyPDF2 import PdfReader, PdfWriter
        except Exception:
            return ("[pdf_redact] 未安装 PDF 处理库(PyPDF2 或 pypdf), 无法脱敏。\n"
                    "请安装后重试: pip install pypdf  [降级]")

    try:
        reader = PdfReader(src)
    except Exception as e:
        return "[pdf_redact] 读取失败: %s" % e

    if not reader.pages or not hasattr(reader.pages[0], "redact"):
        return ("[pdf_redact] 当前 PDF 库版本不支持 redact; 请升级: "
                "pip install --upgrade pypdf  [降级]")

    try:
        writer = PdfWriter()
        for page in reader.pages:
            for term in terms:
                try:
                    page.redact(search_term=term, regex=regex_mode, fill_color=(0, 0, 0))
                except Exception:
                    pass
            writer.add_page(page)
        with open(out, "wb") as f:
            writer.write(f)
    except Exception as e:
        return "[pdf_redact] 脱敏失败: %s" % e
    return "[pdf_redact] 已遮盖 %d 个关键词 -> %s (共 %d 页)" % (
        len(terms), out, len(reader.pages))


# ============================================================================
# db_schema_doc — SQLite schema 文档生成 (标准库 sqlite3, 零依赖)
# ============================================================================
def db_schema_doc(args, ctx):
    db = args.get("db")
    if not db:
        return "[db_schema_doc] 缺 db 参数(目标 sqlite 文件路径)"
    db_path = _resolve(ctx, db)
    if not os.path.exists(db_path):
        return "[db_schema_doc] 数据库不存在: %s" % db_path
    fmt = (args.get("format") or "md").lower()

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND type IN ('table','view') ORDER BY type, name").fetchall()
    except Exception as e:
        return "[db_schema_doc] 无法读取数据库: %s" % e

    tables = [r for r in rows if r[0] == "table"]
    views = [r for r in rows if r[0] == "view"]

    def _info(name):
        cols = cur.execute("PRAGMA table_info('%s')" % name.replace("'", "''")).fetchall()
        idxs = cur.execute("PRAGMA index_list('%s')" % name.replace("'", "''")).fetchall()
        fks = cur.execute("PRAGMA foreign_key_list('%s')" % name.replace("'", "''")).fetchall()
        return cols, idxs, fks

    if fmt == "json":
        doc = {"database": os.path.basename(db_path), "tables": [], "views": []}
        for t in tables:
            cols, idxs, fks = _info(t[1])
            doc["tables"].append({
                "name": t[1],
                "columns": [{"name": c[1], "type": c[2], "notnull": bool(c[3]),
                             "default": c[4], "pk": bool(c[5])} for c in cols],
                "indexes": [{"name": i[1], "unique": bool(i[3])} for i in idxs],
                "foreign_keys": [{"column": f[3], "ref_table": f[4], "ref_column": f[5]} for f in fks],
            })
        for v in views:
            doc["views"].append({"name": v[1], "sql": v[2]})
        conn.close()
        text = json.dumps(doc, ensure_ascii=False, indent=2)
        if args.get("out"):
            text = _maybe_write(ctx, args["out"], text)
        return text

    md = []
    md.append("# 数据库结构文档: %s" % os.path.basename(db_path))
    md.append("")
    md.append("- 表: %d | 视图: %d" % (len(tables), len(views)))
    md.append("")
    for t in tables:
        name = t[1]
        cols, idxs, fks = _info(name)
        md.append("## 表 `%s`" % name)
        md.append("")
        md.append("| 列 | 类型 | 非空 | 默认 | 主键 |")
        md.append("| --- | --- | --- | --- | --- |")
        for c in cols:
            md.append("| %s | %s | %s | %s | %s |" % (
                c[1], c[2] or "", "是" if c[3] else "否",
                (c[4] if c[4] is not None else ""), "是" if c[5] else "否"))
        if idxs:
            md.append("")
            md.append("- 索引: " + ", ".join(
                "%s(%s, unique=%s)" % (i[1], i[2], bool(i[3])) for i in idxs))
        if fks:
            md.append("- 外键: " + ", ".join(
                "%s.%s -> %s.%s" % (f[2], f[3], f[4], f[5]) for f in fks))
        md.append("")
    for v in views:
        md.append("## 视图 `%s`" % v[1])
        md.append("")
        md.append("```sql")
        md.append(v[2] or "")
        md.append("```")
        md.append("")
    conn.close()
    text = "\n".join(md)
    if args.get("out"):
        text = _maybe_write(ctx, args["out"], text)
    return text


def _maybe_write(ctx, out_path, text, suffix=""):
    op = _resolve(ctx, out_path)
    try:
        with open(op, "w", encoding="utf-8") as f:
            f.write(text)
        return text + suffix + "\n\n文档已写入: %s" % op
    except Exception:
        return text


# ============================================================================
# form_validate — 表单/数据校验 (零依赖规则引擎)
# ============================================================================
def _check_type(val, typ):
    typ = (typ or "").lower()
    if typ in ("str", "string"):
        return isinstance(val, str)
    if typ in ("int", "integer"):
        return isinstance(val, int) and not isinstance(val, bool)
    if typ in ("float", "number", "double"):
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if typ in ("bool", "boolean"):
        return isinstance(val, bool)
    if typ in ("list", "array"):
        return isinstance(val, list)
    if typ in ("dict", "object", "map"):
        return isinstance(val, dict)
    return True


def _err_detail(e):
    if e.get("error") == "type":
        return " (期望 %s, 实际 %s)" % (e.get("expected"), e.get("got"))
    if e.get("error") == "enum":
        return " (允许: %s)" % e.get("allowed")
    if e.get("error") == "pattern":
        return " (不匹配 %s)" % e.get("pattern")
    if e.get("error") == "min":
        return " (需>=%s)" % e.get("min")
    if e.get("error") == "max":
        return " (需<=%s)" % e.get("max")
    return ""


def form_validate(args, ctx):
    data = args.get("data")
    schema = args.get("schema") or args.get("rules")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return "[form_validate] data 不是合法 JSON"
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except Exception:
            return "[form_validate] schema 不是合法 JSON"
    if not isinstance(data, dict):
        return "[form_validate] data 必须是对象(字段->值)"
    if not isinstance(schema, dict):
        return "[form_validate] 缺 schema(校验规则对象)"

    fields = schema.get("fields") or {}
    required = set(schema.get("required") or [])
    if not fields:
        fields = {}
        for k, v in schema.items():
            if k in ("required",):
                continue
            fields[k] = {"type": v} if isinstance(v, str) else (v or {})
        required = set(schema.get("required") or [])

    errors = []
    passed = []
    for name, rule in fields.items():
        if not isinstance(rule, dict):
            rule = {"type": rule} if isinstance(rule, str) else {}
        val = data.get(name)
        if name in required and (val is None or val == ""):
            errors.append({"field": name, "error": "required"})
            continue
        if val is None:
            passed.append(name)
            continue
        typ = rule.get("type")
        if typ:
            if not _check_type(val, typ):
                errors.append({"field": name, "error": "type",
                               "expected": typ, "got": type(val).__name__})
                continue
        if rule.get("enum") is not None and val not in rule["enum"]:
            errors.append({"field": name, "error": "enum", "allowed": rule["enum"]})
            continue
        if rule.get("pattern"):
            try:
                if not re.search(rule["pattern"], str(val)):
                    errors.append({"field": name, "error": "pattern", "pattern": rule["pattern"]})
                    continue
            except Exception:
                pass
        if typ in ("int", "float", "number"):
            try:
                num = float(val)
                if "min" in rule and num < rule["min"]:
                    errors.append({"field": name, "error": "min", "min": rule["min"]})
                    continue
                if "max" in rule and num > rule["max"]:
                    errors.append({"field": name, "error": "max", "max": rule["max"]})
                    continue
            except Exception:
                pass
        passed.append(name)

    extra = [k for k in data if k not in fields]
    ok_flag = not errors
    lines = ["[form_validate] 校验%s" % ("通过 ✓" if ok_flag else "失败 ✗")]
    lines.append("- 字段数: %d | 通过: %d | 错误: %d" % (len(data), len(passed), len(errors)))
    for e in errors:
        lines.append("  - ✗ %s: %s%s" % (e["field"], e["error"], _err_detail(e)))
    if extra:
        lines.append("- 未在 schema 中声明的字段(%d): %s" % (len(extra), ", ".join(extra)))
    if args.get("out"):
        op = _resolve(ctx, args["out"])
        try:
            with open(op, "w", encoding="utf-8") as f:
                json.dump({"valid": ok_flag, "passed": passed,
                           "errors": errors, "extra": extra}, f, ensure_ascii=False, indent=2)
            lines.append("- 结果已写入: %s" % op)
        except Exception:
            pass
    return "\n".join(lines)


# ============================================================================
# release_notes — 发布说明生成 (零依赖)
# ============================================================================
_CHANGE_LABELS = {
    "feat": "✨ 新特性", "fix": "🐛 修复", "perf": "⚡ 性能",
    "docs": "📝 文档", "refactor": "♻️ 重构", "test": "✅ 测试",
    "chore": "🔧 构建/杂项", "other": "🔹 其他",
}
_CHANGE_ORDER = ["feat", "fix", "perf", "docs", "refactor", "test", "chore", "other"]


def _classify_change(text):
    t = (text or "")
    # 中文关键词用子串(避免 \b 对 CJK 失效); 英文关键词用单词边界防误匹配
    if ("修复" in t) or re.search(r"\bfix\b|\bbug\b", t, re.I):
        return {"type": "fix", "desc": text}
    if ("新增" in t or "支持" in t or "实现" in t) or re.search(r"\bfeat\b|\bfeature\b", t, re.I):
        return {"type": "feat", "desc": text}
    if ("性能" in t or "优化" in t or "提速" in t) or re.search(r"\bperf\b", t, re.I):
        return {"type": "perf", "desc": text}
    if ("重构" in t or "重做" in t or "重写" in t) or re.search(r"\brefactor\b", t, re.I):
        return {"type": "refactor", "desc": text}
    if ("文档" in t or "说明" in t) or re.search(r"\bdoc\b", t, re.I):
        return {"type": "docs", "desc": text}
    if "测试" in t or re.search(r"\btest\b", t, re.I):
        return {"type": "test", "desc": text}
    return {"type": "other", "desc": text}


def release_notes(args, ctx):
    version = args.get("version") or "unreleased"
    changes = args.get("changes") or args.get("items") or []
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except Exception:
            changes = [changes]
    if not changes:
        for cand in ["CHANGELOG.md", "changes.txt", "commits.txt"]:
            cp = os.path.join(_cwd(ctx), cand)
            if os.path.exists(cp):
                try:
                    changes = [l.strip() for l in open(cp, encoding="utf-8", errors="replace") if l.strip()]
                except Exception:
                    changes = []
                if changes:
                    break
    if not changes:
        return "[release_notes] 缺 changes(变更清单); 可传 changes 或提供 CHANGELOG.md/changes.txt"

    norm = []
    for c in changes:
        if isinstance(c, str):
            norm.append(_classify_change(c))
        elif isinstance(c, dict):
            typ = c.get("type") or _classify_change(c.get("desc", "")).get("type")
            norm.append({"type": typ, "desc": c.get("desc") or c.get("text") or ""})
        else:
            norm.append({"type": "other", "desc": str(c)})

    groups = collections.OrderedDict()
    for n in norm:
        groups.setdefault(n["type"], []).append(n["desc"])

    date = _now_iso()[:10]
    lines = ["# 发布说明 v%s" % version, "", "日期: %s" % date, "",
             "本版本共 %d 项变更。" % len(norm), ""]
    for t in _CHANGE_ORDER:
        if t in groups:
            lines.append("## %s" % _CHANGE_LABELS.get(t, t))
            for d in groups[t]:
                lines.append("- %s" % d)
            lines.append("")
    text = "\n".join(lines).rstrip() + "\n"
    if args.get("out"):
        text = _maybe_write(ctx, args["out"], text)
    return text


# ============================================================================
# code_search_semantic — 语义代码搜索 (零依赖 TF-IDF 跨文件)
# ============================================================================
_STOP = set(
    "a an the of to in for and or is are be on with by from as at it this that "
    "def class return if else for while import function var let const public private "
    "static new not null true false none self get set using namespace include".split())


def _tok(s):
    s2 = re.sub(r"_", " ", s)
    s2 = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s2)
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", s2.lower())
    return [w for w in toks if w not in _STOP and len(w) > 1]


def _extract_snippet(src, q_toks):
    lines = src.splitlines()
    best = []
    for ln in lines:
        low = ln.lower()
        if any(w in low for w in q_toks):
            best.append(ln.strip())
        if len(best) >= 8:
            break
    if not best:
        best = lines[:5]
    return "\n".join("  %s" % b for b in best[:8])


def code_search_semantic(args, ctx):
    query = args.get("query") or args.get("q")
    if not query:
        return "[code_search_semantic] 缺 query 参数(查询文本)"
    path = args.get("path", ".")
    root = str(_resolve(ctx, path))
    if not os.path.exists(root):
        return "[code_search_semantic] 路径不存在: %s" % root

    exts = args.get("ext") or [".py", ".js", ".ts", ".tsx", ".jsx", ".java",
                               ".go", ".rs", ".c", ".cpp", ".h", ".rb", ".md"]
    if isinstance(exts, str):
        exts = [exts]
    top_k = max(1, int(args.get("top_k") or 5))

    files = []
    if os.path.isfile(root):
        files = [root] if any(root.endswith(e) for e in exts) else []
    else:
        for dp, _, fns in os.walk(root):
            for f in fns:
                if any(f.endswith(e) for e in exts):
                    files.append(os.path.join(dp, f))
    if not files:
        return "[code_search_semantic] 未找到匹配扩展名的代码文件: %s" % root

    docs = []
    for fp in files:
        try:
            src = open(fp, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        docs.append((fp, collections.Counter(_tok(src)), src))
    if not docs:
        return "[code_search_semantic] 文件读取失败: %s" % root

    q_toks = _tok(query)
    if not q_toks:
        return "[code_search_semantic] 查询无可索引词: %s" % query
    q_tf = collections.Counter(q_toks)

    N = len(docs)
    df = collections.Counter()
    for _, tf, _ in docs:
        for w in tf:
            df[w] += 1

    def _w(c, w):
        idf = math.log((N + 1) / (df.get(w, 0) + 1)) + 1
        return c * idf

    scored = []
    for fp, tf, src in docs:
        dot = 0.0
        for w, c in q_tf.items():
            if w in tf:
                dot += _w(c, w) * _w(tf[w], w)
        norm_d = math.sqrt(sum(_w(c, w) ** 2 for w, c in tf.items())) or 1
        norm_q = math.sqrt(sum(_w(c, w) ** 2 for w, c in q_tf.items())) or 1
        sim = dot / (norm_d * norm_q)
        if sim > 0:
            scored.append((sim, fp, src))
    scored.sort(reverse=True, key=lambda x: x[0])
    top = scored[:top_k]
    if not top:
        return "[code_search_semantic] 未找到语义相关文件 (query=%s)" % query

    lines = ["[code_search_semantic] 语义代码搜索"]
    lines.append("- 查询: %s | 索引文件: %d | 返回: %d" % (query, N, len(top)))
    for sim, fp, src in top:
        rel = os.path.relpath(fp, _cwd(ctx))
        snippet = _extract_snippet(src, q_toks)
        lines.append("")
        lines.append("### %s  (相似度=%.3f)" % (rel, sim))
        lines.append(snippet)
    return "\n".join(lines)


# ============================================================================
# template_render — 模板渲染 (零依赖 {{var}} / {% for %})
# ============================================================================
def _render_template(tpl, vars_):
    def _for_repl(m):
        expr = m.group(1).strip()
        body = m.group(2)
        mm = re.match(r"(\w+)\s+in\s+(\w+)", expr)
        if not mm:
            return m.group(0)
        var = mm.group(1)
        arr = vars_.get(mm.group(2), [])
        if not isinstance(arr, list):
            return ""
        res = []
        for item in arr:
            local = dict(vars_)
            local[var] = item
            res.append(_render_template(body, local))
        return "\n".join(res)

    tpl = re.sub(r"{%\s*for\s+(.+?)\s*%}(.*?){%\s*endfor\s*%}",
                 _for_repl, tpl, flags=re.DOTALL)

    def _var_repl(m):
        key = m.group(1).strip()
        cur = vars_
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return m.group(0)
        return "" if cur is None else str(cur)

    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", _var_repl, tpl)


def template_render(args, ctx):
    template = args.get("template") or args.get("text")
    tfile = args.get("template_file") or args.get("file")
    if not template and tfile:
        tp = _resolve(ctx, tfile)
        if os.path.exists(tp):
            template = open(tp, encoding="utf-8", errors="replace").read()
    if not template:
        return "[template_render] 缺 template(模板文本) 或 template_file(模板文件)"

    raw_vars = args.get("vars") or args.get("data") or {}
    if isinstance(raw_vars, str):
        try:
            raw_vars = json.loads(raw_vars)
        except Exception:
            raw_vars = {}
    if not isinstance(raw_vars, dict):
        raw_vars = {}

    out = _render_template(template, raw_vars)
    if args.get("out"):
        op = _resolve(ctx, args["out"])
        try:
            with open(op, "w", encoding="utf-8") as f:
                f.write(out)
            out += "\n\n渲染结果已写入: %s" % op
        except Exception:
            pass
    return out

"""研发效能与文档工具集 (Phase 95): 对标主流产品的研发/协作/文档差异化能力。

覆盖:
  - code_metrics      : AST 代码指标(SLOC/函数数/类数/圈复杂度/最大嵌套), 零依赖
  - agent_team       : 多 Agent 编排(并行/串行/辩论策略 + 聚合器), 产出团队调度清单(JSON)
  - db_migrate       : SQLite 迁移运行器(init/create/status/up/down), 标准库 sqlite3
  - pdf_merge        : 多 PDF 合并(PyPDF2/pypdf 优先, 无库优雅降级)
  - pdf_split        : PDF 按页拆分(PyPDF2/pypdf 优先, 无库优雅降级)
  - form_to_pdf      : 表单/字段清单 -> PDF(零依赖最小 PDF 写入器, 多页分页)
  - text_compare     : 双文本差异与相似度(difflib 行级 diff + 相似比), 零依赖

设计纪律(与既有 suite_* 一致):
  - 工具函数签名统一 def name(args, ctx) -> str
  - 路径经 common.resolve_path 落域防护
  - 零硬依赖: PDF 合并/拆分优先外部引擎, 缺失自动降级并提示; 其余纯标准库
  - 失败信息以 [tool] 前缀回灌模型, 让其自我修复, 而非抛异常中断
"""

import os
import re
import json
import ast
import time
import difflib
import datetime
import sqlite3
import struct
import zlib

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
# 零依赖最小 PDF 写入器(多页, 用于 form_to_pdf)
# ============================================================================
# ============================================================================
# 零依赖 PDF 写入器(支持中文: 嵌入 TrueType 字体 + CIDFontType2/Identity-H)
# ============================================================================
def _find_cjk_font():
    for p in ["C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simfang.ttf",
              "C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/msyh.ttc"]:
        if os.path.exists(p) and not p.lower().endswith(".ttc"):
            return p
    return None


def _ttf_parse(ttf):
    """解析 TTF: 返回 cmap(可调用 gid(c)), numGlyphs, bbox, ascent, descent。"""
    res = {"cmap": lambda c: 0, "numGlyphs": 0, "bbox": [0, 0, 1000, 1000],
           "ascent": 800, "descent": -200}
    data = ttf
    if data[:4] == b"ttcf":
        n = struct.unpack(">I", data[8:12])[0]
        offs = [struct.unpack(">I", data[12 + 4 * i:16 + 4 * i])[0] for i in range(n)]
        data = ttf[offs[0]:]
    num_tables = struct.unpack(">H", data[4:6])[0]
    tables = {}
    p = 12
    for _ in range(num_tables):
        tag = data[p:p + 4].decode("latin-1")
        off = struct.unpack(">I", data[p + 8:p + 12])[0]
        length = struct.unpack(">I", data[p + 12:p + 16])[0]
        tables[tag] = (off, length)
        p += 16

    def td(tag):
        o, l = tables[tag]
        return data[o:o + l]

    if "head" in tables:
        h = td("head")
        res["bbox"] = list(struct.unpack(">hhhh", h[36:44]))
    if "hhea" in tables:
        hh = td("hhea")
        res["ascent"] = struct.unpack(">h", hh[4:6])[0]
        res["descent"] = struct.unpack(">h", hh[6:8])[0]
    if "maxp" in tables:
        res["numGlyphs"] = struct.unpack(">H", td("maxp")[4:6])[0]
    if "cmap" in tables:
        c = td("cmap")
        num_subs = struct.unpack(">H", c[2:4])[0]
        best = None
        for i in range(num_subs):
            pid = struct.unpack(">H", c[4 + 8 * i:6 + 8 * i])[0]
            eid = struct.unpack(">H", c[6 + 8 * i:8 + 8 * i])[0]
            off = struct.unpack(">I", c[8 + 8 * i:12 + 8 * i])[0]
            if best is None:
                best = (pid, eid, off)
            elif (pid, eid) in [(3, 1), (0, 4), (0, 3), (3, 10)]:
                best = (pid, eid, off)
        if best:
            sub = c[best[2]:]
            fmt = struct.unpack(">H", sub[0:2])[0]
            if fmt == 4:
                res["cmap"] = _cmap_fmt4(sub)
            elif fmt == 12:
                res["cmap"] = _cmap_fmt12(sub)
    return res


def _cmap_fmt4(sub):
    # sub[6:8] 是 segCountX2 (不是段数)
    seg_count_x2 = struct.unpack(">H", sub[6:8])[0]
    seg = seg_count_x2 // 2
    sx2 = seg_count_x2
    end = [struct.unpack(">H", sub[14 + i * 2:16 + i * 2])[0] for i in range(seg)]
    start = [struct.unpack(">H", sub[16 + sx2 + i * 2:18 + sx2 + i * 2])[0] for i in range(seg)]
    delta = [struct.unpack(">H", sub[16 + 2 * sx2 + i * 2:18 + 2 * sx2 + i * 2])[0] for i in range(seg)]
    roff = [struct.unpack(">H", sub[16 + 3 * sx2 + i * 2:18 + 3 * sx2 + i * 2])[0] for i in range(seg)]
    gstart = 16 + 4 * sx2
    sub_len = len(sub)

    def gid(c):
        for i in range(seg):
            if start[i] <= c <= end[i]:
                if roff[i] == 0:
                    return (c + delta[i]) & 0xFFFF
                idx = roff[i] // 2 + (c - start[i]) + i
                pos = gstart + idx * 2
                if pos + 2 > sub_len:
                    return 0
                g = struct.unpack(">H", sub[pos:pos + 2])[0]
                return (g + delta[i]) & 0xFFFF if g != 0 else 0
        return 0

    return gid


def _cmap_fmt12(sub):
    n = struct.unpack(">I", sub[12:16])[0]

    def gid(c):
        for i in range(n):
            base = 16 + i * 12
            s = struct.unpack(">I", sub[base:base + 4])[0]
            e = struct.unpack(">I", sub[base + 4:base + 8])[0]
            sg = struct.unpack(">I", sub[base + 8:base + 12])[0]
            if s <= c <= e:
                return sg + (c - s)
        return 0

    return gid


def _build_pdf_cjk(pages_text, font_path):
    ttf = open(font_path, "rb").read()
    info = _ttf_parse(ttf)
    gid = info["cmap"]
    used = set()
    for page in pages_text:
        used.update(page)
    gid_of = {ch: gid(ord(ch)) for ch in used}
    rev = {g: ch for ch, g in gid_of.items()}

    objs = {}
    nid = [1]

    def new(body):
        i = nid[0]
        nid[0] += 1
        objs[i] = body
        return i

    catalog = new(None)
    pages_id = new(None)
    type0 = new(None)
    cidfont = new(None)
    fdesc = new(None)
    fontfile = new(None)

    comp = zlib.compress(ttf)
    objs[fontfile] = (
        ("<< /Length %d /Length1 %d /Filter /FlateDecode >>\nstream\n" % (len(comp), len(ttf))).encode("latin-1")
        + comp + b"\nendstream")
    bbox = info["bbox"]
    objs[fdesc] = ("<< /Type /FontDescriptor /FontName /LMWCJK /Flags 4 "
                   "/FontBBox [%d %d %d %d] /ItalicAngle 0 /Ascent %d /Descent %d "
                   "/CapHeight %d /StemV 80 /FontFile2 %d 0 R >>" % (
                       bbox[0], bbox[1], bbox[2], bbox[3],
                       info["ascent"], info["descent"], info["ascent"], fontfile))
    objs[cidfont] = ("<< /Type /Font /Subtype /CIDFontType2 /BaseFont /LMWCJK "
                     "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
                     "/FontDescriptor %d 0 R /DW 1000 /CIDToGIDMap /Identity >>" % fdesc)

    # ToUnicode CMap
    tuni = new(None)
    objs[type0] = ("<< /Type /Font /Subtype /Type0 /BaseFont /LMWCJK "
                   "/Encoding /Identity-H /DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>"
                   % (cidfont, tuni))
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
             "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> def",
             "/CMapName /LMWToUnicode def", "/CMapType 2 def",
             "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
    entries = ["<%04X> <%04X>" % (g, ord(ch)) for g, ch in sorted(rev.items())]
    for i in range(0, len(entries), 100):
        chunk = entries[i:i + 100]
        lines.append("%d beginbfchar" % len(chunk))
        lines.extend(chunk)
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end end"]
    cmap_text = "\n".join(lines)
    objs[tuni] = ("<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap_text.encode("utf-8")), cmap_text)).encode("latin-1")

    page_ids, content_ids = [], []
    for page in pages_text:
        pid = new(None)
        cid = new(None)
        page_ids.append(pid)
        content_ids.append(cid)
        y = 760
        parts = []
        for line in page.split("\n"):
            hexstr = "".join("%04X" % gid_of.get(ch, 0) for ch in line)
            parts.append("BT /F1 11 Tf 1 0 0 1 50 %d Tm <%s> Tj ET" % (y, hexstr))
            y -= 18
        stream = "\n".join(parts)
        objs[cid] = ("<< /Length %d >>\nstream\n%s\nendstream" % (len(stream.encode("utf-8")), stream)).encode("latin-1")
        objs[pid] = ("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                     "/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                     % (pages_id, type0, cid))
    objs[pages_id] = "<< /Type /Pages /Count %d /Kids [%s] >>" % (
        len(page_ids), " ".join("%d 0 R" % p for p in page_ids))
    objs[catalog] = "<< /Type /Catalog /Pages %d 0 R >>" % pages_id

    return _serialize(objs)


def _serialize(objs):
    buf = b"%PDF-1.4\n"
    offsets = {}
    for oid in sorted(objs):
        body = objs[oid]
        if isinstance(body, str):
            body = body.encode("latin-1")
        offsets[oid] = len(buf)
        buf += ("%d 0 obj\n" % oid).encode("latin-1") + body + b"\nendobj\n"
    xref_pos = len(buf)
    max_id = max(objs)
    buf += ("xref\n0 %d\n" % (max_id + 1)).encode("latin-1")
    buf += b"0000000000 65535 f \n"
    for oid in range(1, max_id + 1):
        if oid in offsets:
            buf += ("%010d 00000 n \n" % offsets[oid]).encode("latin-1")
        else:
            buf += b"0000000000 65535 f \n"
    buf += ("trailer\n<< /Size %d /Root 1 %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (max_id + 1, 1, xref_pos)).encode("latin-1")
    return buf


def _minimal_pdf(pages_text):
    """pages_text: list[str], 每项为该页纯文本(已按行拼接)。返回 bytes。

    优先嵌入系统 CJK 字体(支持中文); 缺失字体时回退到 ASCII 最小写入器。
    """
    fp = _find_cjk_font()
    if fp:
        try:
            return _build_pdf_cjk(pages_text, fp)
        except Exception:
            pass
    # ASCII 回退(非 latin-1 字符以 ? 替代, 绝不崩溃)
    objects = []
    n_pages = len(pages_text)
    page_obj_ids, content_ids = [], []
    nid = 3
    for _ in range(n_pages):
        page_obj_ids.append(nid); nid += 1
        content_ids.append(nid); nid += 1
    font_id = nid
    objects.append((1, "<< /Type /Catalog /Pages 2 0 R >>"))
    kids = " ".join("%d 0 R" % p for p in page_obj_ids)
    objects.append((2, "<< /Type /Pages /Count %d /Kids [%s] >>" % (n_pages, kids)))
    objects.append((font_id, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    for i, text in enumerate(pages_text):
        cid = content_ids[i]
        safe = text.encode("latin-1", "replace").decode("latin-1")
        escaped = safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = "BT /F1 11 Tf 50 760 Td 14 TL (%s) Tj ET" % escaped
        objects.append((cid, "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)))
        objects.append((page_obj_ids[i],
                        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                        "/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                        % (font_id, cid)))
    objects.sort(key=lambda x: x[0])
    return _serialize(dict(objects))



# ============================================================================
# code_metrics — AST 代码指标
# ============================================================================
def code_metrics(args, ctx):
    path = args.get("path", ".")
    root = str(_resolve(ctx, path))
    if not os.path.exists(root):
        return "[code_metrics] 路径不存在: %s" % root

    files = []
    if os.path.isfile(root):
        if root.endswith(".py"):
            files = [root]
    else:
        for dirpath, _, fnames in os.walk(root):
            for f in fnames:
                if f.endswith(".py"):
                    files.append(os.path.join(dirpath, f))
    if not files:
        return "[code_metrics] 未找到 .py 文件: %s" % root

    agg = {"files": 0, "loc": 0, "sloc": 0, "blank": 0, "comment": 0,
           "functions": 0, "classes": 0, "methods": 0, "max_complexity": 0,
           "total_complexity": 0, "syntax_errors": 0}
    per_file = []

    for fp in files:
        try:
            src = open(fp, encoding="utf-8", errors="replace").read()
        except Exception as e:
            agg["syntax_errors"] += 1
            continue
        lines = src.splitlines()
        loc = len(lines)
        blank = sum(1 for l in lines if not l.strip())
        comment = sum(1 for l in lines if l.strip().startswith("#"))
        sloc = loc - blank - comment
        try:
            tree = ast.parse(src)
        except SyntaxError:
            agg["syntax_errors"] += 1
            per_file.append({"file": os.path.relpath(fp, _cwd(ctx)), "syntax_error": True})
            continue
        funcs = classes = methods = 0
        max_depth = 0
        branches = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs += 1
                branches += 1
            elif isinstance(node, ast.ClassDef):
                classes += 1
            elif isinstance(node, (ast.If, ast.For, ast.While, ast.AsyncFor,
                                   ast.With, ast.AsyncWith, ast.Try,
                                   ast.BoolOp, ast.IfExp, ast.comprehension,
                                   ast.Assert)):
                branches += 1
            elif isinstance(node, ast.ExceptHandler):
                branches += 1
        # methods = functions defined inside classes
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods += 1
        complexity = branches + 1
        agg["files"] += 1
        agg["loc"] += loc
        agg["sloc"] += sloc
        agg["blank"] += blank
        agg["comment"] += comment
        agg["functions"] += funcs
        agg["classes"] += classes
        agg["methods"] += methods
        agg["max_complexity"] = max(agg["max_complexity"], complexity)
        agg["total_complexity"] += complexity
        per_file.append({
            "file": os.path.relpath(fp, _cwd(ctx)),
            "loc": loc, "sloc": sloc, "functions": funcs,
            "classes": classes, "complexity": complexity,
        })

    avg_cx = round(agg["total_complexity"] / agg["files"], 2) if agg["files"] else 0
    report = []
    report.append("[code_metrics] 代码指标报告")
    report.append("- 文件数: %d" % agg["files"])
    report.append("- 总行数 LOC: %d | 有效代码 SLOC: %d | 空行: %d | 注释: %d" % (
        agg["loc"], agg["sloc"], agg["blank"], agg["comment"]))
    report.append("- 函数: %d | 类: %d | 方法: %d" % (agg["functions"], agg["classes"], agg["methods"]))
    report.append("- 圈复杂度 合计: %d | 均值: %s | 峰值: %d" % (
        agg["total_complexity"], avg_cx, agg["max_complexity"]))
    if agg["syntax_errors"]:
        report.append("- 语法错误文件: %d" % agg["syntax_errors"])
    report.append("")
    report.append("Top 文件 (按复杂度):")
    for rec in sorted(per_file, key=lambda r: r.get("complexity", 0), reverse=True)[:10]:
        if rec.get("syntax_error"):
            report.append("  - %s [语法错误]" % rec["file"])
        else:
            report.append("  - %s: SLOC=%d 函数=%d 类=%d 复杂度=%d" % (
                rec["file"], rec["sloc"], rec["functions"], rec["classes"], rec["complexity"]))

    out_path = os.path.join(_cwd(ctx), "code_metrics.json")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"aggregate": agg, "files": per_file}, f, ensure_ascii=False, indent=2)
        report.append("")
        report.append("明细已写入: %s" % out_path)
    except Exception:
        pass
    return "\n".join(report)


# ============================================================================
# agent_team — 多 Agent 编排
# ============================================================================
def agent_team(args, ctx):
    spec = args.get("spec") or args.get("agents")
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except Exception:
            spec = {"agents": [{"role": "general", "task": spec}]}
    if not isinstance(spec, dict):
        spec = {"agents": spec if isinstance(spec, list) else []}

    agents = spec.get("agents") or []
    if isinstance(agents, str):
        agents = [{"role": "general", "task": agents}]
    if not isinstance(agents, list) or not agents:
        return "[agent_team] 未提供有效的 agents(角色+任务) 清单"

    strategy = spec.get("strategy", "parallel")
    aggregator = spec.get("aggregator", "merge")
    if strategy not in ("parallel", "sequential", "debate"):
        strategy = "parallel"

    norm = []
    for i, a in enumerate(agents):
        if isinstance(a, str):
            a = {"role": "agent%d" % (i + 1), "task": a}
        norm.append({
            "id": "agent%d" % (i + 1),
            "role": a.get("role") or ("agent%d" % (i + 1)),
            "task": a.get("task") or a.get("prompt") or "",
            "model": a.get("model"),
            "tools": a.get("tools"),
        })

    manifest = {
        "created": _now_iso(),
        "strategy": strategy,
        "aggregator": aggregator,
        "agent_count": len(norm),
        "agents": norm,
        "dispatch_order": [a["id"] for a in norm] if strategy != "sequential"
        else [a["id"] for a in norm],
        "note": "由 agent_team 生成调度清单; 实际执行由主控 AgentLoop 按策略派发子 Agent。",
    }

    out_dir = os.path.join(_cwd(ctx), ".lmw_team")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass
    out_path = os.path.join(out_dir, "team_%s.json" % int(time.time()))
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception:
        out_path = None

    lines = ["[agent_team] 多 Agent 编排清单"]
    lines.append("- 策略: %s | 聚合器: %s | 成员: %d" % (strategy, aggregator, len(norm)))
    for a in norm:
        lines.append("  - %s (role=%s): %s" % (a["id"], a["role"], _trim(a["task"], 120)))
    lines.append("- 调度顺序: %s" % " -> ".join(manifest["dispatch_order"]))
    if out_path:
        lines.append("- 清单已写入: %s" % out_path)
    return "\n".join(lines)


# ============================================================================
# db_migrate — SQLite 迁移运行器
# ============================================================================
_MIG_TABLE = "lmw_migrations"


def db_migrate(args, ctx):
    action = (args.get("action") or "status").strip().lower()
    db = args.get("db")
    if not db:
        return "[db_migrate] 缺 db 参数(目标 sqlite 文件路径)"
    db_path = _resolve(ctx, db)
    mig_dir = _resolve(ctx, args.get("dir", "migrations"))

    def _conn():
        return sqlite3.connect(db_path)

    if action == "init":
        try:
            os.makedirs(mig_dir, exist_ok=True)
            conn = _conn()
            conn.execute("CREATE TABLE IF NOT EXISTS %s ("
                         "name TEXT PRIMARY KEY, applied_at TEXT)" % _MIG_TABLE)
            conn.commit()
            conn.close()
        except Exception as e:
            return "[db_migrate] init 失败: %s" % e
        return "[db_migrate] 已初始化迁移表 `%s` 与目录 %s" % (_MIG_TABLE, mig_dir)

    if action == "create":
        try:
            os.makedirs(mig_dir, exist_ok=True)
        except Exception as e:
            return "[db_migrate] 无法创建目录 %s: %s" % (mig_dir, e)
        name = args.get("name") or ("m%03d" % (len(os.listdir(mig_dir)) + 1))
        fname = "%s.sql" % name
        fpath = os.path.join(mig_dir, fname)
        if os.path.exists(fpath):
            return "[db_migrate] 迁移文件已存在: %s" % fpath
        tpl = ("-- up\nCREATE TABLE IF NOT EXISTS example (\n"
               "  id INTEGER PRIMARY KEY,\n  created_at TEXT\n);\n\n-- down\n"
               "DROP TABLE IF EXISTS example;\n")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(tpl)
        return "[db_migrate] 已创建迁移模板: %s" % fpath

    # status / up / down 都需要迁移表
    if action in ("status", "up", "down"):
        if not os.path.exists(db_path):
            return "[db_migrate] 数据库不存在: %s (请先 action=init 或建库)" % db_path
        try:
            conn = _conn()
            conn.execute("CREATE TABLE IF NOT EXISTS %s ("
                         "name TEXT PRIMARY KEY, applied_at TEXT)" % _MIG_TABLE)
            conn.commit()
        except Exception as e:
            return "[db_migrate] 无法访问迁移表: %s" % e

        applied = set(r[0] for r in conn.execute(
            "SELECT name FROM %s" % _MIG_TABLE).fetchall())
        files = []
        if os.path.isdir(mig_dir):
            files = sorted(f for f in os.listdir(mig_dir) if f.endswith(".sql"))

        if action == "status":
            lines = ["[db_migrate] 迁移状态"]
            lines.append("- 已应用: %d | 待执行: %d" % (
                len(applied & set(files)), len(set(files) - applied)))
            for f in files:
                mark = "OK " if f in applied else ".. "
                lines.append("  %s%s" % (mark, f))
            conn.close()
            return "\n".join(lines)

        if action == "up":
            pending = [f for f in files if f not in applied]
            done = 0
            for f in pending:
                fpath = os.path.join(mig_dir, f)
                try:
                    sql = open(fpath, encoding="utf-8", errors="replace").read()
                    up = _split_migration(sql)[0]
                    if up.strip():
                        conn.executescript(up)
                    conn.execute("INSERT OR IGNORE INTO %s (name, applied_at) VALUES (?,?)"
                                 % _MIG_TABLE, (f, _now_iso()))
                    conn.commit()
                    done += 1
                except Exception as e:
                    conn.close()
                    return "[db_migrate] 应用 %s 失败: %s (已回滚此前批次)" % (f, e)
            conn.close()
            return "[db_migrate] 已应用 %d 个迁移 (共 %d 个待执行)" % (done, len(pending))

        if action == "down":
            last = args.get("name")
            if not last:
                rows = conn.execute(
                    "SELECT name FROM %s ORDER BY applied_at DESC LIMIT 1" % _MIG_TABLE).fetchall()
                last = rows[0][0] if rows else None
            if not last:
                conn.close()
                return "[db_migrate] 没有可回滚的迁移"
            fpath = os.path.join(mig_dir, last)
            try:
                if os.path.exists(fpath):
                    sql = open(fpath, encoding="utf-8", errors="replace").read()
                    down = _split_migration(sql)[1]
                    if down.strip():
                        conn.executescript(down)
                conn.execute("DELETE FROM %s WHERE name=?" % _MIG_TABLE, (last,))
                conn.commit()
                conn.close()
                return "[db_migrate] 已回滚迁移: %s" % last
            except Exception as e:
                conn.close()
                return "[db_migrate] 回滚 %s 失败: %s" % (last, e)

    return "[db_migrate] 未知 action: %s (支持 init/create/status/up/down)" % action


def _split_migration(sql):
    """按 '-- up' / '-- down' 分隔符切分 SQL。"""
    up, down = "", ""
    mode = "up"
    for line in sql.splitlines():
        low = line.strip().lower()
        if low.startswith("-- up"):
            mode = "up"; continue
        if low.startswith("-- down"):
            mode = "down"; continue
        if mode == "up":
            up += line + "\n"
        else:
            down += line + "\n"
    return up, down


# ============================================================================
# pdf_merge / pdf_split — PDF 合并/拆分 (有库则真实, 无库降级)
# ============================================================================
def _load_pdf_lib():
    try:
        from pypdf import PdfReader, PdfWriter
        return ("pypdf", PdfReader, PdfWriter)
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader, PdfWriter
        return ("PyPDF2", PdfReader, PdfWriter)
    except Exception:
        pass
    return None


def pdf_merge(args, ctx):
    files = args.get("files") or []
    if isinstance(files, str):
        files = [files]
    if not files:
        return "[pdf_merge] 缺 files 参数(待合并 PDF 路径列表)"
    out = _resolve(ctx, args.get("out", "merged.pdf"))
    paths = [_resolve(ctx, f) for f in files]
    for p in paths:
        if not os.path.exists(p):
            return "[pdf_merge] 文件不存在: %s" % p

    lib = _load_pdf_lib()
    if not lib:
        return ("[pdf_merge] 未安装 PDF 处理库(PyPDF2 或 pypdf), 无法合并。\n"
                "请安装后重试: pip install pypdf  [降级]")
    _, PdfReader, PdfWriter = lib
    try:
        writer = PdfWriter()
        for p in paths:
            reader = PdfReader(p)
            for page in reader.pages:
                writer.add_page(page)
        with open(out, "wb") as f:
            writer.write(f)
    except Exception as e:
        return "[pdf_merge] 合并失败: %s" % e
    return "[pdf_merge] 已合并 %d 个文件 -> %s" % (len(paths), out)


def pdf_split(args, ctx):
    src = args.get("file") or args.get("path")
    if not src:
        return "[pdf_split] 缺 file 参数(待拆分 PDF 路径)"
    src = _resolve(ctx, src)
    if not os.path.exists(src):
        return "[pdf_split] 文件不存在: %s" % src

    lib = _load_pdf_lib()
    if not lib:
        return ("[pdf_split] 未安装 PDF 处理库(PyPDF2 或 pypdf), 无法拆分。\n"
                "请安装后重试: pip install pypdf  [降级]")
    _, PdfReader, PdfWriter = lib

    try:
        reader = PdfReader(src)
        total = len(reader.pages)
    except Exception as e:
        return "[pdf_split] 读取失败: %s" % e

    out_dir = _resolve(ctx, args.get("out_dir", "split"))
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        return "[pdf_split] 无法创建输出目录 %s: %s" % (out_dir, e)

    # 每页一个文件 (pages 参数可指定范围, 形如 "1-3,5")
    pages = args.get("pages")
    idxs = _parse_page_ranges(pages, total) if pages else list(range(total))

    count = 0
    try:
        for i in idxs:
            if i < 0 or i >= total:
                continue
            writer = PdfWriter()
            writer.add_page(reader.pages[i])
            op = os.path.join(out_dir, "page_%03d.pdf" % (i + 1))
            with open(op, "wb") as f:
                writer.write(f)
            count += 1
    except Exception as e:
        return "[pdf_split] 拆分失败: %s" % e
    return "[pdf_split] 已拆分 %d 页(共 %d 页) -> %s/" % (count, total, out_dir)


def _parse_page_ranges(spec, total):
    idxs = []
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                a = int(a) - 1
                b = int(b) - 1
            except Exception:
                continue
            idxs.extend(range(a, min(b, total - 1) + 1))
        elif part:
            try:
                idxs.append(int(part) - 1)
            except Exception:
                continue
    return sorted(set(idxs))


# ============================================================================
# form_to_pdf — 表单/字段清单 -> PDF
# ============================================================================
def form_to_pdf(args, ctx):
    title = args.get("title", "表单")
    fields = args.get("fields") or args.get("items") or []
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except Exception:
            fields = [fields]
    body_lines = []
    body_lines.append("== %s ==" % title)
    body_lines.append("")
    if not fields:
        body_lines.append("(无字段)")
    for i, fld in enumerate(fields, 1):
        if isinstance(fld, str):
            body_lines.append("%d. %s" % (i, fld))
        elif isinstance(fld, dict):
            label = fld.get("label") or fld.get("name") or ("字段%d" % i)
            value = fld.get("value", "")
            typ = fld.get("type", "")
            if typ in ("text", "input", "textarea"):
                body_lines.append("%d. %s: ___%s" % (i, label, value))
            else:
                body_lines.append("%d. %s: %s" % (i, label, value))
        else:
            body_lines.append("%d. %s" % (i, str(fld)))

    # 分页: 每页 45 行
    pages = [body_lines[i:i + 45] for i in range(0, len(body_lines), 45)] or [[""]]
    data = _minimal_pdf(["\n".join(p) for p in pages])
    out = _resolve(ctx, args.get("out", "form.pdf"))
    try:
        with open(out, "wb") as f:
            f.write(data)
    except Exception as e:
        return "[form_to_pdf] 写入失败: %s" % e
    return "[form_to_pdf] 已生成 %d 页 PDF -> %s (共 %d 字段)" % (len(pages), out, len(fields))


# ============================================================================
# text_compare — 双文本差异与相似度
# ============================================================================
def text_compare(args, ctx):
    a = args.get("a") or args.get("text_a") or ""
    b = args.get("b") or args.get("text_b") or ""
    # 支持从文件读取
    if args.get("file_a"):
        pa = _resolve(ctx, args["file_a"])
        if os.path.exists(pa):
            a = open(pa, encoding="utf-8", errors="replace").read()
    if args.get("file_b"):
        pb = _resolve(ctx, args["file_b"])
        if os.path.exists(pb):
            b = open(pb, encoding="utf-8", errors="replace").read()

    if not a and not b:
        return "[text_compare] 缺少待比较文本 (a/b 或 file_a/file_b)"
    if not isinstance(a, str):
        a = str(a)
    if not isinstance(b, str):
        b = str(b)

    sm = difflib.SequenceMatcher(None, a, b)
    ratio = round(sm.ratio() * 100, 2)

    la = a.splitlines()
    lb = b.splitlines()
    diff = list(difflib.unified_diff(la, lb, fromfile="A", tofile="B", lineterm=""))

    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))

    lines = ["[text_compare] 文本差异报告"]
    lines.append("- 相似度: %s%%" % ratio)
    lines.append("- A 行数: %d | B 行数: %d" % (len(la), len(lb)))
    lines.append("- 新增行: %d | 删除行: %d" % (added, removed))
    lines.append("")
    lines.append("差异片段:")
    shown = 0
    for d in diff:
        if d.startswith(("+++", "---", "@@")):
            lines.append(d)
        elif d.startswith(("+", "-", " ")):
            lines.append(d)
            shown += 1
        if shown >= 80:
            lines.append("... (差异较多, 已截断, 共 %d 行差异)" % len(diff))
            break

    if args.get("out"):
        out = _resolve(ctx, args["out"])
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            lines.append("")
            lines.append("报告已写入: %s" % out)
        except Exception:
            pass
    return "\n".join(lines)

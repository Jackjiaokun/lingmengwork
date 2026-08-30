"""Phase 93 生产力工具套件: 可视化 + 办公自动化 + 本地知识检索。

全部零硬依赖, 缺失可选引擎时优雅降级 (返回 [tool] 提示而非崩溃)。
对标: draw.io/语雀绘图(diagram) · QuickChart/数据可视化(chart) · Postman(api_test)
      · 邮件/日历客户端(email_compose/calendar_event) · RAG(knowledge_search) · 文档导出(pdf_make)
"""
import os
import re
import json
import math
import html
import base64
import urllib.request
import urllib.error
import smtplib
import email.utils
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

from .common import resolve_path


# —— 共享辅助 (与 suite_extended 一致) ——
def _roots(ctx):
    return ctx.get("roots") or ["."]


def _cwd(ctx):
    return ctx.get("cwd") or (str(_roots(ctx)[0]) if _roots(ctx) else ".")


def _resolve(ctx, path):
    return resolve_path(_roots(ctx), path)


def _trim(text, limit=20000):
    text = text.strip()
    try:
        n = int(limit)
    except Exception:
        n = 20000
    if len(text) <= n:
        return text
    return text[:n] + f"\n... (已截断, 共 {len(text)} 字符)"


def _ua():
    return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ============================================================================
# 1. diagram —— 通用 Mermaid 图 (flowchart/sequence/class/state/gantt)
# ============================================================================
_MERM_KIND_HEADER = {
    "flowchart": "flowchart TD",
    "sequence": "sequenceDiagram",
    "class": "classDiagram",
    "state": "stateDiagram-v2",
    "gantt": "gantt",
}


def diagram(args, ctx):
    """生成 Mermaid 图 (.mmd), 可选渲染 SVG。

    spec: 直接给出 mermaid 正文(不含 ```mermaid 围栏)。
    或结构化: kind(flowchart/sequence/class/state/gantt) + nodes({id:label}) + edges(["A-->B: 说明"])。
    """
    kind = (args.get("kind") or "flowchart").strip().lower()
    out = args.get("out") or "diagram.mmd"
    title = (args.get("title") or "").strip()
    spec = (args.get("spec") or "").strip()

    if not spec:
        nodes = args.get("nodes") or {}
        edges = args.get("edges") or []
        if isinstance(nodes, str):
            nodes = json.loads(nodes) if nodes.strip() else {}
        if isinstance(edges, str):
            edges = json.loads(edges) if edges.strip() else []
        header = _MERM_KIND_HEADER.get(kind, "flowchart TD")
        lines = [header]
        if kind == "flowchart":
            for nid, label in (nodes or {}).items():
                lab = (label or nid)
                lines.append(f'    {nid}["{lab}"]' if not str(lab).startswith('"') else f"    {nid}{lab}")
            for e in edges:
                lines.append(f"    {e}")
        else:
            for e in edges:
                lines.append(f"    {e}")
        spec = "\n".join(lines)

    # 若已含 mermaid 关键字则不重复加头
    known = ("flowchart", "graph", "sequenceDiagram", "classDiagram",
             "stateDiagram", "erDiagram", "gantt", "journey", "pie")
    if not any(spec.lstrip().startswith(k) for k in known):
        spec = (_MERM_KIND_HEADER.get(kind, "flowchart TD") + "\n" + spec).strip()

    if title:
        spec = f"---\ntitle: {title}\n---\n" + spec

    try:
        p = _resolve(ctx, out)
        os.makedirs(os.path.dirname(str(p)) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(spec + "\n")
    except Exception as e:
        return f"[diagram] 写入失败: {e}"

    svg_note = ""
    try:
        import shutil
        mmdc = shutil.which("mmdc") or shutil.which("mmdc.cmd")
        if mmdc:
            svg = str(p) + ".svg"
            r = os.system(f'"{mmdc}" -i "{p}" -o "{svg}" -b white 2>nul')
            if r == 0 and os.path.exists(svg):
                svg_note = f"；已渲染 SVG: {svg}"
    except Exception:
        pass

    return (f"[diagram] 已生成 Mermaid 图: {p}"
            f"{('；类型=' + kind) if not args.get('spec') else ''}"
            f"{svg_note}"
            f"{'' if svg_note else '（未装 mermaid-cli, 可用 https://mermaid.live 预览 .mmd）'}")


# ============================================================================
# 2. chart —— 数据 → SVG 图表 (line/bar/pie)
# ============================================================================
def _svg_escape(s):
    return html.escape(str(s))


def _chart_line_bar(data, ctype, title, out_base):
    W, H = 760, 440
    PAD_L, PAD_R, PAD_T, PAD_B = 64, 24, 48, 56
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B

    labels = data.get("labels", [])
    series = data.get("series", [])
    if not series and ctype == "bar" and "values" in data:
        series = [{"name": "value", "values": data["values"]}]
    if not labels and series:
        n = max(len(s.get("values", [])) for s in series)
        labels = [str(i + 1) for i in range(n)]

    # 收集所有值
    all_vals = []
    for s in series:
        all_vals.extend([float(v) for v in s.get("values", []) if _is_num(v)])
    if not all_vals:
        return None, "[chart] 无数值数据"
    vmax = max(all_vals)
    vmin = min(0.0, min(all_vals)) if ctype == "bar" else min(all_vals)
    if vmax == vmin:
        vmax = vmin + 1.0
    span = vmax - vmin

    def x_at(i, n):
        if n <= 1:
            return PAD_L + plot_w / 2
        return PAD_L + (plot_w * i / (n - 1))

    def y_at(v):
        return PAD_T + plot_h * (1 - (float(v) - vmin) / span)

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    svg.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    if title:
        svg.append(f'<text x="{W/2}" y="28" text-anchor="middle" font-size="18" fill="#1f2937">{_svg_escape(title)}</text>')
    # 网格 + y 轴刻度
    for g in range(5):
        gy = PAD_T + plot_h * g / 4
        val = vmax - span * g / 4
        svg.append(f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{W-PAD_R}" y2="{gy:.1f}" stroke="#e5e7eb"/>')
        svg.append(f'<text x="{PAD_L-8}" y="{gy+4:.1f}" text-anchor="end" font-size="11" fill="#6b7280">{val:.1f}</text>')
    # 轴线
    svg.append(f'<line x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L}" y2="{PAD_T+plot_h}" stroke="#9ca3af"/>')
    svg.append(f'<line x1="{PAD_L}" y1="{PAD_T+plot_h}" x2="{W-PAD_R}" y2="{PAD_T+plot_h}" stroke="#9ca3af"/>')

    colors = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#db2777"]

    if ctype == "line":
        for si, s in enumerate(series):
            vals = s.get("values", [])
            color = colors[si % len(colors)]
            pts = " ".join(f"{x_at(i, len(vals)):.1f},{y_at(v):.1f}"
                           for i, v in enumerate(vals) if _is_num(v))
            svg.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5"/>')
            for i, v in enumerate(vals):
                if _is_num(v):
                    svg.append(f'<circle cx="{x_at(i,len(vals)):.1f}" cy="{y_at(v):.1f}" r="3" fill="{color}"/>')
            # x 标签
            for i, lb in enumerate(labels):
                if i < len(vals):
                    svg.append(f'<text x="{x_at(i,len(vals)):.1f}" y="{PAD_T+plot_h+18}" text-anchor="middle" font-size="10" fill="#6b7280">{_svg_escape(lb)}</text>')
    else:  # bar
        n_series = max(1, len(series))
        group_w = plot_w / max(1, len(labels))
        bar_w = group_w / (n_series + 1)
        for li, lb in enumerate(labels):
            gx = PAD_L + group_w * li
            for si, s in enumerate(series):
                v = s.get("values", [])[li] if li < len(s.get("values", [])) else None
                if not _is_num(v):
                    continue
                color = colors[si % len(colors)]
                bx = gx + bar_w * (si + 0.5)
                by = y_at(v)
                bh = (PAD_T + plot_h) - by
                svg.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w*0.9:.1f}" height="{max(0,bh):.1f}" fill="{color}"/>')
            svg.append(f'<text x="{gx+group_w/2:.1f}" y="{PAD_T+plot_h+18}" text-anchor="middle" font-size="10" fill="#6b7280">{_svg_escape(lb)}</text>')

    # 图例
    ly = PAD_T - 10
    lx = PAD_L
    for si, s in enumerate(series):
        color = colors[si % len(colors)]
        svg.append(f'<rect x="{lx}" y="{ly-9}" width="12" height="12" fill="{color}"/>')
        svg.append(f'<text x="{lx+16}" y="{ly+1}" font-size="11" fill="#374151">{_svg_escape(s.get("name", f"S{si+1}"))}</text>')
        lx += 28 + len(_svg_escape(s.get("name", f"S{si+1}"))) * 7

    svg.append("</svg>")
    return "\n".join(svg), None


def _chart_pie(data, title, out_base):
    W, H = 560, 460
    cx, cy, r = 230, 240, 160
    labels = data.get("labels", [])
    values = [float(v) for v in data.get("values", []) if _is_num(v)]
    if not values:
        return None, "[chart] 饼图无数值"
    total = sum(values)
    colors = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#65a30d"]
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    if title:
        svg.append(f'<text x="{W/2}" y="28" text-anchor="middle" font-size="18" fill="#1f2937">{_svg_escape(title)}</text>')
    a0 = -math.pi / 2
    for i, v in enumerate(values):
        frac = v / total
        a1 = a0 + frac * 2 * math.pi
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        large = 1 if frac > 0.5 else 0
        svg.append(f'<path d="M{cx},{cy} L{x0:.1f},{y0:.1f} A{r},{r} 0 {large} 1 {x1:.1f},{y1:.1f} Z" fill="{colors[i%len(colors)]}" stroke="#fff"/>')
        a0 = a1
    # 图例
    lx = 430
    ly = 90
    for i, lb in enumerate(labels):
        pct = values[i] / total * 100
        svg.append(f'<rect x="{lx}" y="{ly-10}" width="12" height="12" fill="{colors[i%len(colors)]}"/>')
        svg.append(f'<text x="{lx+16}" y="{ly}" font-size="12" fill="#374151">{_svg_escape(str(lb))} {pct:.1f}%</text>')
        ly += 22
    svg.append("</svg>")
    return "\n".join(svg), None


def _is_num(v):
    try:
        float(v)
        return True
    except Exception:
        return False


def chart(args, ctx):
    """数据 → SVG 图表 (line/bar/pie), 产出 .svg + .html 预览。"""
    ctype = (args.get("type") or "bar").strip().lower()
    title = (args.get("title") or "").strip()
    out = args.get("out") or f"chart_{ctype}.svg"
    raw = args.get("data")
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception as e:
            return f"[chart] data 不是合法 JSON: {e}"
    else:
        data = raw or {}

    if ctype == "pie":
        svg, err = _chart_pie(data, title, out)
    elif ctype in ("line", "bar"):
        svg, err = _chart_line_bar(data, ctype, title, out)
    else:
        return f"[chart] 不支持的类型: {ctype}（支持 line/bar/pie）"

    if err:
        return err

    try:
        p = _resolve(ctx, out)
        os.makedirs(os.path.dirname(str(p)) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(svg)
        html_path = str(p) + ".html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(f"<html><head><meta charset='utf-8'><title>{_svg_escape(title or 'chart')}</title></head>"
                    f"<body style='margin:24px;font-family:sans-serif'>"
                    f"<h3>{_svg_escape(title or 'Chart')}</h3>{svg}</body></html>")
    except Exception as e:
        return f"[chart] 写入失败: {e}"

    return f"[chart] 已生成 {ctype} 图表: {p}（预览 {html_path}）"


# ============================================================================
# 3. api_test —— 单/多接口测试 (请求 + 断言)
# ============================================================================
def _do_request(method, url, headers, body, timeout=20):
    data = None
    if body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        if isinstance(body, (dict, list)) and "Content-Type" not in {k.lower() for k in (headers or {})}:
            headers = dict(headers or {})
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method.upper())
    for k, v in (headers or {}).items():
        req.add_header(k, str(v))
    req.add_header("User-Agent", _ua())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        status = resp.status
        ctype = resp.headers.get("Content-Type", "")
    try:
        text = raw.decode("utf-8")
    except Exception:
        text = raw.decode("latin-1", "replace")
    return status, text, ctype


def _json_path(obj, path):
    cur = obj
    for part in path.replace("[", ".").replace("]", "").split("."):
        part = part.strip()
        if part == "":
            continue
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def api_test(args, ctx):
    """多接口测试: cases=[{name,method,url,headers?,body?,asserts?}]; asserts={status?,contains?,json_path?,equals?}。"""
    base = (args.get("base_url") or "").rstrip("/")
    raw = args.get("cases")
    if isinstance(raw, str):
        try:
            cases = json.loads(raw)
        except Exception as e:
            return f"[api_test] cases 不是合法 JSON: {e}"
    else:
        cases = raw or []
    if not isinstance(cases, list) or not cases:
        return "[api_test] 未提供 cases 列表"

    out = args.get("out") or "api_test_report.md"
    results = []
    for i, c in enumerate(cases):
        name = c.get("name") or f"case{i+1}"
        method = (c.get("method") or "GET").upper()
        url = c.get("url") or ""
        if base and not url.startswith("http"):
            url = base + "/" + url.lstrip("/")
        asserts = c.get("asserts") or {}
        try:
            status, text, ctype = _do_request(method, url, c.get("headers"), c.get("body"))
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            results.append((name, False, f"请求异常: {e}", None))
            continue

        ok = True
        why = []
        if "status" in asserts and int(asserts["status"]) != status:
            ok = False
            why.append(f"status {status} != {asserts['status']}")
        if "contains" in asserts and asserts["contains"] not in text:
            ok = False
            why.append("响应未含预期子串")
        if "json_path" in asserts:
            try:
                val = _json_path(json.loads(text), asserts["json_path"])
                if "equals" in asserts and str(val) != str(asserts["equals"]):
                    ok = False
                    why.append(f"{asserts['json_path']}={val} != {asserts['equals']}")
            except Exception as e:
                ok = False
                why.append(f"json_path 解析失败: {e}")
        results.append((name, ok, ("; ".join(why) or "通过"), status))

    passed = sum(1 for r in results if r[1])
    lines = [f"# API 测试报告  {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"总计 {len(results)} · 通过 {passed} · 失败 {len(results)-passed}", ""]
    for name, ok, msg, status in results:
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {name} (HTTP {status}) — {msg}")
    report = "\n".join(lines)

    try:
        p = _resolve(ctx, out)
        os.makedirs(os.path.dirname(str(p)) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(report + "\n")
    except Exception:
        pass
    return f"[api_test] 完成 {len(results)} 例, 通过 {passed}。\n{report}"


# ============================================================================
# 4. email_compose —— 撰写邮件草稿 (.eml) + 可选发送
# ============================================================================
def email_compose(args, ctx):
    """撰写邮件: to/subject/body; 可选 from/cc/smtp{host,port,user,pass}/send。产出 .eml。"""
    to = args.get("to") or ""
    subject = args.get("subject") or ""
    body = args.get("body") or ""
    frm = args.get("from") or args.get("sender") or "lingmengwork@local"
    cc = args.get("cc") or ""
    out = args.get("out") or "draft.eml"
    send = bool(args.get("send"))

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = frm
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = email.utils.format_datetime(datetime.now(timezone.utc))

    try:
        p = _resolve(ctx, out)
        os.makedirs(os.path.dirname(str(p)) or ".", exist_ok=True)
        with open(p, "wb") as f:
            f.write(msg.as_bytes())
    except Exception as e:
        return f"[email_compose] 写入失败: {e}"

    if not send:
        return f"[email_compose] 已生成邮件草稿: {p}（to={to}, subject={subject}；send=true 可发送）"

    smtp = args.get("smtp") or {}
    host = smtp.get("host")
    if not host:
        return f"[email_compose] 已生成草稿 {p}，但未提供 smtp.host，未发送。"
    try:
        port = int(smtp.get("port", 465))
        server = smtplib.SMTP_SSL(host, port, timeout=20) if port == 465 else smtplib.SMTP(host, port, timeout=20)
        if smtp.get("user"):
            server.login(smtp["user"], smtp.get("pass", ""))
        rcpts = [x.strip() for x in (to.split(",") + cc.split(",")) if x.strip()]
        server.sendmail(frm, rcpts, msg.as_bytes())
        server.quit()
        return f"[email_compose] 已发送邮件至 {rcpts}（草稿 {p}）。"
    except Exception as e:
        return f"[email_compose] 草稿已存 {p}，但发送失败: {e}"


# ============================================================================
# 5. calendar_event —— 日历事件 (.ics)
# ============================================================================
def _ics_dt(s):
    """ISO 字符串 → ICS UTC 格式 (YYYYMMDDTHHMMSSZ)。"""
    s = s.strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y%m%dT%H%M%SZ")
        except Exception:
            continue
    return None


def calendar_event(args, ctx):
    """生成日历事件 (.ics): title/start(ISO); end? 或 duration?(分钟); location?/description?/alarm?(提前分钟)。"""
    title = args.get("title") or "事件"
    start = args.get("start") or ""
    dt_start = _ics_dt(start)
    if not dt_start:
        return f"[calendar_event] start 不是合法 ISO 时间: {start}"
    end = args.get("end")
    dur = args.get("duration")
    if end:
        dt_end = _ics_dt(end) or dt_start
    elif dur:
        try:
            mins = int(dur)
            base = datetime.strptime(dt_start, "%Y%m%dT%H%M%SZ")
            dt_end = (base + timedelta(minutes=mins)).strftime("%Y%m%dT%H%M%SZ")
        except Exception:
            dt_end = dt_start
    else:
        dt_end = dt_start
    location = (args.get("location") or "").replace("\n", "\\n")
    desc = (args.get("description") or "").replace("\n", "\\n")
    alarm = args.get("alarm")
    out = args.get("out") or "event.ics"
    uid = f"{abs(hash(title + start))}@lingmengwork"

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Lingmengwork//Calendar//CN",
        "CALSCALE:GREGORIAN", "BEGIN:VEVENT", f"UID:{uid}",
        f"DTSTAMP:{_ics_dt(datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))}",
        f"DTSTART:{dt_start}", f"DTEND:{dt_end}",
        f"SUMMARY:{title}", f"LOCATION:{location}", f"DESCRIPTION:{desc}",
    ]
    if alarm:
        try:
            am = int(alarm)
            lines += ["BEGIN:VALARM", f"TRIGGER:-PT{am}M", "ACTION:DISPLAY",
                      f"DESCRIPTION:{title}", "END:VALARM"]
        except Exception:
            pass
    lines += ["END:VEVENT", "END:VCALENDAR"]

    try:
        p = _resolve(ctx, out)
        os.makedirs(os.path.dirname(str(p)) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("\r\n".join(lines) + "\r\n")
    except Exception as e:
        return f"[calendar_event] 写入失败: {e}"
    return f"[calendar_event] 已生成日历事件: {p}（{title} @ {start}）"


# ============================================================================
# 6. knowledge_search —— 本地 TF-IDF 知识库 (零依赖)
# ============================================================================
_INDEX_FILE = ".lmw_kb_index.json"
_TXT_EXTS = (".txt", ".md", ".py", ".json", ".csv", ".log", ".yaml", ".yml", ".html", ".js", ".ts")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")


def _tokenize(text):
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) >= 1]


def _kb_index(path, ctx):
    root = _resolve(ctx, path)
    docs = []
    tf = {}
    df = {}
    idx = 0
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if not fn.lower().endswith(_TXT_EXTS):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(200000)
            except Exception:
                continue
            toks = _tokenize(text)
            if not toks:
                continue
            tfm = {}
            for t in toks:
                tfm[t] = tfm.get(t, 0) + 1
            tf[str(idx)] = tfm
            for t in tfm:
                df[t] = df.get(t, 0) + 1
            docs.append({"path": str(fp), "len": len(toks)})
            idx += 1
    index = {"docs": docs, "df": df, "tf": tf, "total": len(docs)}
    return index


def _kb_score(index, query):
    qtok = _tokenize(query)
    if not qtok:
        return []
    N = index["total"]
    df = index["df"]
    qtf = {}
    for t in qtok:
        qtf[t] = qtf.get(t, 0) + 1
    qvec = {}
    for t, c in qtf.items():
        idf = math.log((N + 1) / (df.get(t, 0) + 1)) + 1
        qvec[t] = (1 + math.log(c)) * idf
    qnorm = math.sqrt(sum(v * v for v in qvec.values())) or 1
    scored = []
    for di, d in enumerate(index["docs"]):
        tfm = index["tf"].get(str(di), {})
        num = 0.0
        dnorm = 0.0
        maxtf = max(tfm.values()) if tfm else 1
        for t, c in tfm.items():
            idf = math.log((N + 1) / (df.get(t, 0) + 1)) + 1
            dv = (0.5 + 0.5 * c / maxtf) * idf
            dnorm += dv * dv
            if t in qvec:
                num += qvec[t] * dv
        dnorm = math.sqrt(dnorm) or 1
        sim = num / (qnorm * dnorm)
        if sim > 0:
            scored.append((sim, di))
    scored.sort(reverse=True)
    return scored


def knowledge_search(args, ctx):
    """本地知识检索: action=index(path) 建索引; action=query(query, limit?) 检索。

    索引存于目标目录 .lmw_kb_index.json (零依赖 TF-IDF)。对标 RAG/语义检索。
    """
    action = (args.get("action") or "query").strip().lower()
    limit = int(args.get("limit") or 5)

    if action == "index":
        path = args.get("path") or "."
        try:
            index = _kb_index(path, ctx)
        except Exception as e:
            return f"[knowledge_search] 建索引失败: {e}"
        rootp = _resolve(ctx, path)
        if str(rootp) != os.path.dirname(str(rootp)) and os.path.isfile(str(rootp)):
            rootp = os.path.dirname(str(rootp))
        idx_path = os.path.join(str(rootp), _INDEX_FILE)
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
        return f"[knowledge_search] 已索引 {index['total']} 个文档 → {idx_path}"

    # query
    query = (args.get("query") or "").strip()
    if not query:
        return "[knowledge_search] 请提供 query"
    idx_path = args.get("index") or os.path.join(_cwd(ctx), _INDEX_FILE)
    if not os.path.exists(idx_path):
        return "[knowledge_search] 未找到索引, 请先 action=index 建索引"
    try:
        with open(idx_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception as e:
        return f"[knowledge_search] 索引读取失败: {e}"
    scored = _kb_score(index, query)[:limit]
    if not scored:
        return f"[knowledge_search] 未检索到与「{query}」相关的文档"
    lines = [f"# 知识检索: {query}", ""]
    for sim, di in scored:
        d = index["docs"][di]
        lines.append(f"- 相似度 {sim:.3f} · {d['path']} (词数 {d['len']})")
    return "\n".join(lines)


# ============================================================================
# 7. pdf_make —— Markdown/文本 → PDF (reportlab 优先, 否则最小 PDF)
# ============================================================================
def _minimal_pdf(text, title):
    """零依赖最小 PDF (仅文本, 按行分页)。"""
    def esc(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    lines = text.split("\n")
    pages = []
    cur = []
    y = 800
    for ln in lines:
        if y < 60:
            pages.append(cur)
            cur = []
            y = 800
        cur.append((y, ln))
        y -= 18
    if cur:
        pages.append(cur)
    objs = []
    # 1 catalog, 2 pages, 3* font, then per page content+page obj
    n_pages = len(pages)
    font_obj = 3
    page_objs = []
    content_objs = []
    next_id = 4
    for _ in pages:
        content_objs.append(next_id); page_objs.append(next_id + 1)
        next_id += 2
    # build pdf bytes
    out = []
    out.append(b"%PDF-1.4\n")
    offsets = []
    # helper to add object
    stream_parts = []
    # Object 1: Catalog
    def obj(i, body):
        stream_parts.append((i, body))
    obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_objs)
    obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    obj(font_obj, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for pi, pg in enumerate(pages):
        cobj = content_objs[pi]
        pobj = page_objs[pi]
        content = ["BT", "/F1 11 Tf", "50 800 Td"]
        first = True
        for y, ln in pg:
            if first:
                content.append(f"1 0 0 1 50 {y} Tm ({esc(ln)}) Tj")
                first = False
            else:
                content.append(f"0 -18 Td ({esc(ln)}) Tj")
        content.append("ET")
        stream = "\n".join(content).encode()
        obj(cobj, b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        obj(pobj, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font_obj} 0 R >> >> /Contents {cobj} 0 R >>".encode())
    total = 1 + 1 + 1 + 2 * n_pages
    buf = b"%PDF-1.4\n"
    offsets = [0] * (total + 1)
    pos = len(buf)
    bodies = {}
    for i, body in stream_parts:
        bodies[i] = body
    for i in range(1, total + 1):
        offsets[i] = pos
        o = f"{i} 0 obj\n".encode() + bodies[i] + b"\nendobj\n"
        buf += o
        pos += len(o)
    xref = b"xref\n0 " + str(total + 1).encode() + b"\n"
    xref += b"0000000000 65535 f \n"
    for i in range(1, total + 1):
        xref += f"{offsets[i]:010d} 00000 n \n".encode()
    xref += (b"trailer\n<< /Size " + str(total + 1).encode() + b" /Root 1 0 R >>\nstartxref\n"
             + str(pos).encode() + b"\n%%EOF")
    return buf + xref


def pdf_make(args, ctx):
    """Markdown/文本 → PDF: input(文件) 或 text; 优先 reportlab, 否则零依赖最小 PDF。"""
    title = (args.get("title") or "Document").strip()
    out = args.get("out") or "output.pdf"
    text = ""
    if args.get("input"):
        try:
            p = _resolve(ctx, args["input"])
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            return f"[pdf_make] 读取 input 失败: {e}"
    else:
        text = args.get("text") or args.get("markdown") or ""
    if not text.strip():
        return "[pdf_make] 未提供 text/input 内容"

    try:
        import reportlab  # type: ignore
        HAVE_RL = True
    except Exception:
        HAVE_RL = False

    try:
        p = _resolve(ctx, out)
        os.makedirs(os.path.dirname(str(p)) or ".", exist_ok=True)
        if HAVE_RL:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.enums import TA_LEFT
            doc = SimpleDocTemplate(str(p), pagesize=A4, title=title)
            ss = getSampleStyleSheet()
            flow = []
            for ln in text.split("\n"):
                if ln.startswith("# "):
                    flow.append(Paragraph(ln[2:], ss["Title"]))
                elif ln.startswith("## "):
                    flow.append(Paragraph(ln[3:], ss["Heading1"]))
                elif ln.startswith("### "):
                    flow.append(Paragraph(ln[4:], ss["Heading2"]))
                elif ln.strip() == "":
                    flow.append(Spacer(1, 6))
                else:
                    flow.append(Paragraph(_svg_escape(ln), ss["BodyText"]))
            doc.build(flow)
            engine = "reportlab"
        else:
            data = _minimal_pdf(text, title)
            with open(p, "wb") as f:
                f.write(data)
            engine = "minimal"
    except Exception as e:
        return f"[pdf_make] 生成失败: {e}"

    return (f"[pdf_make] 已生成 PDF: {p}（引擎={engine}）"
            f"{'' if HAVE_RL else '（未装 reportlab, 使用零依赖最小 PDF; pip install reportlab 可获完整排版）'}")

"""办公生产力工具: PDF/Office 读取、文档生成、数据分析。

设计原则:
- 全部零依赖 (标准库 zipfile + xml.etree + sqlite3 + csv), 保证冻结 exe 与源码验证都可用。
  PDF 在无可装库时以「朴素 FlateDecode 流扫描」尽力抽取文本。
- 工具函数签名统一为 def name(args, ctx) -> str。
- 路径经 fs.resolve_path 落域防护。
"""
import re
import csv
import json
import zipfile
import base64
import zlib
import html
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from .common import ToolError, resolve_path


# ============================================================
# 1) read_pdf — 抽取 PDF 文本 (可选库 / pdftotext / 朴素回退)
# ============================================================
def read_pdf(args, ctx):
    """抽取 PDF 文本。优先 pypdf/PyPDF2/pdfplumber, 其次 pdftotext(CLI), 最后朴素 FlateDecode 流扫描。"""
    raw = (args.get("path") or "").strip()
    if not raw:
        return "[read_pdf] 未提供 path"
    rp = fs_resolve(ctx, raw)
    if not rp.exists():
        return f"[read_pdf] 文件不存在: {rp}"

    # 1) 可选库
    for mod in ("pypdf", "PyPDF2", "pdfplumber"):
        try:
            return _read_pdf_lib(mod, rp)
        except ImportError:
            continue
        except Exception as e:
            return f"[read_pdf] {mod} 解析失败: {e}"

    # 2) pdftotext (poppler)
    from shutil import which
    if which("pdftotext"):
        import subprocess
        try:
            r = subprocess.run(["pdftotext", "-layout", str(rp), "-"],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                return _clip(r.stdout, args)
        except Exception:
            pass

    # 3) 朴素回退
    return _read_pdf_naive(rp, args)


def _read_pdf_lib(mod, rp):
    if mod == "pdfplumber":
        import pdfplumber
        with pdfplumber.open(str(rp)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        return f"[read_pdf] 共 {len(pages)} 页 (pdfplumber)\n\n" + "\n\n--- 第 ".join(
            f"{i+1} 页 ---\n{t}" for i, t in enumerate(pages))
    if mod == "pypdf":
        from pypdf import PdfReader
    else:
        from PyPDF2 import PdfReader
    reader = PdfReader(str(rp))
    out = [f"[read_pdf] 共 {len(reader.pages)} 页 ({mod})"]
    for i, pg in enumerate(reader.pages):
        out.append(f"\n--- 第 {i+1} 页 ---\n" + (pg.extract_text() or ""))
    return "\n".join(out)


def _read_pdf_naive(rp, args):
    """朴素回退: 解压流对象中的 FlateDecode 文本, 抽取 Tj/TJ 中的字符串。零依赖。"""
    try:
        data = rp.read_bytes()
        texts = []
        # 找到所有流, 尝试 zlib 解压, 抽取括号字符串
        for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL):
            chunk = m.group(1)
            try:
                dec = zlib.decompress(chunk)
            except Exception:
                continue
            for s in re.findall(rb"\((?:[^()\\]|\\.)*\)", dec):
                s = s[1:-1]
                s = s.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
                try:
                    texts.append(s.decode("latin-1"))
                except Exception:
                    pass
        joined = "".join(texts)
        if not joined.strip():
            return "[read_pdf] 朴素抽取未得到文本 (该 PDF 可能含扫描图或加密)。建议安装 pypdf: pip install pypdf"
        return "[read_pdf] 朴素抽取 (可能含排版噪声):\n\n" + _clip(joined, args)
    except Exception as e:
        return f"[read_pdf] 朴素抽取失败: {e}"


def _clip(text, args):
    limit = int((args.get("max_chars") or 20000))
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... (已截断, 共 {len(text)} 字符, 用 max_chars 调整)"


# ============================================================
# 2) read_office — 抽取 docx/xlsx/pptx 文本 (零依赖 zip+XML)
# ============================================================
def read_office(args, ctx):
    """抽取 Office 文件文本。docx/xlsx/pptx 均零依赖 (zip + XML)。xlsx 输出为表格。"""
    raw = (args.get("path") or "").strip()
    if not raw:
        return "[read_office] 未提供 path"
    rp = fs_resolve(ctx, raw)
    if not rp.exists():
        return f"[read_office] 文件不存在: {rp}"
    if not zipfile.is_zipfile(str(rp)):
        return f"[read_office] 不是有效的 Office 文件 (需 .docx/.xlsx/.pptx): {rp}"
    ext = rp.suffix.lower()
    try:
        if ext == ".docx":
            return _read_docx(rp)
        if ext == ".xlsx":
            return _read_xlsx(rp)
        if ext == ".pptx":
            return _read_pptx(rp)
        return f"[read_office] 暂不支持的类型: {ext}"
    except Exception as e:
        return f"[read_office] 解析失败: {e}"


def _read_docx(rp):
    with zipfile.ZipFile(str(rp)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    paras = []
    for chunk in re.split(r"</w:p>", xml):
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", chunk, re.DOTALL)
        line = "".join(_unescape(t) for t in texts)
        if line.strip():
            paras.append(line)
    return f"[read_office/docx] 共 {len(paras)} 段:\n\n" + "\n\n".join(paras)


def _read_xlsx(rp):
    with zipfile.ZipFile(str(rp)) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = re.findall(r"<t[^>]*>(.*?)</t>", sx, re.DOTALL)
            shared = [_unescape(t) for t in shared]
        sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        out = []
        for sh in sheets:
            sx = z.read(sh).decode("utf-8", "replace")
            cells = re.findall(r"<c\s+r=\"([A-Z]+\d+)\"(?:\s+t=\"(\w+)\")?>(?:<f[^>]*>.*?</f>)?<v>(.*?)</v>", sx, re.DOTALL)
            grid = {}
            maxc = 0
            for ref, ctype, val in cells:
                col = re.match(r"([A-Z]+)", ref).group(1)
                row = int(re.match(r"[A-Z]+(\d+)", ref).group(1))
                if ctype == "s":
                    val = shared[int(val)] if val.isdigit() and int(val) < len(shared) else val
                grid[(row, col)] = _unescape(val)
                maxc = max(maxc, _col_idx(col))
            if not grid:
                continue
            rows = sorted({r for r, _ in grid})
            header = f"### 工作表 {sh.split('sheet')[-1].split('.')[0]}"
            lines = [header]
            for r in rows[:300]:
                line = []
                for c in range(1, maxc + 1):
                    line.append(grid.get((r, _col_name(c)), ""))
                lines.append(" | ".join(line))
            out.append("\n".join(lines))
    if not out:
        return "[read_office/xlsx] 未解析到单元格数据"
    return "[read_office/xlsx] 预览 (前 300 行/表):\n\n" + "\n\n".join(out)


def _read_pptx(rp):
    with zipfile.ZipFile(str(rp)) as z:
        slides = sorted(n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n))
        out = []
        for i, sl in enumerate(slides, 1):
            xml = z.read(sl).decode("utf-8", "replace")
            texts = re.findall(r"<a:t>(.*?)</a:t>", xml, re.DOTALL)
            texts = [_unescape(t) for t in texts if t.strip()]
            if texts:
                out.append(f"### 幻灯片 {i}\n" + "\n".join(texts))
    if not out:
        return "[read_office/pptx] 未解析到文本"
    return "[read_office/pptx] 共 " + str(len(out)) + " 张幻灯片:\n\n" + "\n\n".join(out)


def _unescape(s):
    s = html.unescape(s)
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def _col_idx(name):
    n = 0
    for ch in name:
        n = n * 26 + (ord(ch) - 64)
    return n


def _col_name(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ============================================================
# 3) make_doc — 生成文档 (md 零依赖 / docx 零依赖 zip 构建)
# ============================================================
def make_doc(args, ctx):
    """根据结构化内容生成文档。format=md(默认) 或 docx(零依赖生成)。输出到 path。"""
    fmt = (args.get("format") or "md").strip().lower()
    out_path = (args.get("path") or "").strip()
    title = (args.get("title") or "未命名文档").strip()
    body = args.get("body") or args.get("content") or ""
    if isinstance(body, list):
        body = "\n".join(str(b) for b in body)
    if not out_path:
        return "[make_doc] 未提供 path (输出文件路径)"
    rp = fs_resolve(ctx, out_path)

    if fmt == "md":
        md = f"# {title}\n\n{body}\n"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(md, encoding="utf-8")
        return f"[make_doc] 已生成 Markdown: {rp} ({len(md)} 字符)"

    if fmt == "docx":
        _build_docx(rp, title, body)
        return f"[make_doc] 已生成 Word 文档: {rp} (零依赖 zip 构建)"

    return f"[make_doc] 不支持的 format: {fmt} (支持 md / docx)"


def _build_docx(rp, title, body):
    paras = []
    for line in body.split("\n"):
        line = line.rstrip()
        if not line.strip():
            continue
        if line.startswith("# "):
            paras.append(("Heading1", line[2:].strip()))
        elif line.startswith("## "):
            paras.append(("Heading2", line[3:].strip()))
        elif line.startswith("- ") or line.startswith("* "):
            paras.append(("List", line[2:].strip()))
        else:
            paras.append(("Normal", line))
    doc = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
    doc.append('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">')
    doc.append("<w:body>")
    for style, text in [("Title", title)] + paras:
        doc.append(f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
                   f'<w:r><w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r></w:p>')
    doc.append("</w:body></w:document>")
    document_xml = "".join(doc)

    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '</Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:rPr><w:b/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:rPr><w:b/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="List"><w:name w:val="List Paragraph"/><w:ind w:left="360"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>'
              '</w:styles>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>%s</dc:title></cp:coreProperties>' % _xml_escape(title))

    rp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(rp), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", styles)
        z.writestr("docProps/core.xml", core)


def _xml_escape(s):
    return html.escape(str(s), quote=True)


# ============================================================
# 4) data_table — 数据分析 (csv/json 零依赖统计 + 可选图表)
# ============================================================
def data_table(args, ctx):
    """分析结构化数据。source=csv/json 路径 或 data=内联JSON。op=head/describe/columns/groupby/chart。"""
    op = (args.get("op") or "summary").strip().lower()
    rows, src_name = _load_data(args, ctx)
    if isinstance(rows, str):
        return rows  # 错误串
    if not rows:
        return f"[data_table] {src_name}: 无数据行"

    if op == "columns":
        return f"[data_table] {src_name} 列: {', '.join(rows[0].keys())} (共 {len(rows)} 行)"
    if op in ("head", "preview"):
        n = int(args.get("n") or 10)
        return _render_dicts(rows[:n], title=f"{src_name} 前 {min(n, len(rows))} 行")
    if op == "describe":
        return _describe(rows, src_name)
    if op == "groupby":
        return _groupby(rows, args, src_name)
    if op == "chart":
        return _chart(rows, args, ctx, src_name)
    # 默认 summary: 形状 + describe
    return f"[data_table] {src_name} 形状: {len(rows)} 行 × {len(rows[0])} 列\n\n" + _describe(rows, src_name)


def _load_data(args, ctx):
    inline = args.get("data")
    if inline:
        try:
            data = json.loads(inline) if isinstance(inline, str) else inline
            if isinstance(data, dict):
                data = data.get("rows") or list(data.values())[0]
            return [dict(r) for r in data], "内联JSON"
        except Exception as e:
            return f"[data_table] 内联数据解析失败: {e}", None
    src = (args.get("source") or "").strip()
    if not src:
        return "[data_table] 未提供 source(文件路径) 或 data(内联JSON)", None
    rp = fs_resolve(ctx, src)
    if rp.suffix.lower() == ".json":
        data = json.loads(rp.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("rows") or (list(data.values())[0] if data else [])
        return [dict(r) for r in data], rp.name
    # csv
    with open(rp, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)], rp.name


def _render_dicts(rows, title=""):
    if not rows:
        return "(空)"
    cols = list(rows[0].keys())
    lines = [title] if title else []
    lines.append(" | ".join(cols))
    lines.append("-+-".join("-" * max(3, len(c)) for c in cols))
    for r in rows:
        lines.append(" | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols))
    return "\n".join(lines)


def _to_num(v):
    try:
        return float(v)
    except Exception:
        return None


def _describe(rows, src):
    cols = list(rows[0].keys())
    out = [f"[data_table] {src} 描述性统计 (共 {len(rows)} 行):"]
    for c in cols:
        vals = [r.get(c) for r in rows]
        nonnull = [v for v in vals if v not in (None, "")]
        nums = [_to_num(v) for v in nonnull]
        nums = [n for n in nums if n is not None]
        is_num = len(nums) >= max(1, len(nonnull) * 0.8)
        line = f"- {c}: 非空 {len(nonnull)}/{len(vals)}, 唯一 {len(set(str(v) for v in nonnull))}"
        if is_num and nums:
            line += f", 数值: 均值={sum(nums)/len(nums):.2f}, 最小={min(nums):.2f}, 最大={max(nums):.2f}"
        out.append(line)
    return "\n".join(out)


def _groupby(rows, args, src):
    key = (args.get("key") or "").strip()
    val = (args.get("value") or "").strip()
    agg = (args.get("agg") or "count").strip().lower()
    if not key:
        return "[data_table] groupby 需要 key 列"
    groups = {}
    for r in rows:
        k = r.get(key)
        groups.setdefault(k, []).append(r)
    out = [f"[data_table] 按 {key} 分组 ({agg}{' '+val if val else ''}):"]
    for k, grp in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if agg == "count":
            out.append(f"- {k}: {len(grp)}")
        elif val and agg in ("sum", "mean", "min", "max"):
            nums = [_to_num(x.get(val)) for x in grp]
            nums = [n for n in nums if n is not None]
            if agg == "sum":
                out.append(f"- {k}: {sum(nums):.2f}")
            elif agg == "mean" and nums:
                out.append(f"- {k}: {sum(nums)/len(nums):.2f}")
            elif agg == "min" and nums:
                out.append(f"- {k}: {min(nums):.2f}")
            elif agg == "max" and nums:
                out.append(f"- {k}: {max(nums):.2f}")
        else:
            out.append(f"- {k}: {len(grp)}")
    return "\n".join(out)


def _chart(rows, args, ctx, src):
    key = (args.get("key") or "").strip()
    val = (args.get("value") or "").strip()
    title = (args.get("title") or f"{src} 图表").strip()
    if not key:
        return "[data_table] chart 需要 key 列 (分类轴)"
    groups = {}
    for r in rows:
        groups.setdefault(r.get(key), []).append(r)
    labels, values = [], []
    for k, grp in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        labels.append(str(k))
        if val:
            nums = [_to_num(x.get(val)) for x in grp]
            nums = [n for n in nums if n is not None]
            values.append(sum(nums) if nums else 0)
        else:
            values.append(len(grp))
    svg = _bar_svg(labels, values, title)
    out_path = (args.get("out") or "").strip()
    if out_path:
        rp = fs_resolve(ctx, out_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(svg, encoding="utf-8")
        return f"[data_table] 图表已保存: {rp} ({len(labels)} 个分类)"
    return f"[data_table] {title} (SVG, {len(labels)} 分类):\n\n{svg}"


def _bar_svg(labels, values, title, w=720, h=360):
    if not values:
        return "<svg></svg>"
    maxv = max(values) or 1
    pad = 50
    n = len(labels)
    bw = (w - 2 * pad) / max(1, n)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">']
    svg.append(f'<text x="{w//2}" y="24" text-anchor="middle" font-size="16" font-family="sans-serif">{_xml_escape(title)}</text>')
    # y 轴基线
    svg.append(f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="#888"/>')
    for i, (lab, v) in enumerate(zip(labels, values)):
        bh = int((v / maxv) * (h - 2 * pad))
        x = pad + i * bw + bw * 0.15
        bw2 = bw * 0.7
        y = h - pad - bh
        svg.append(f'<rect x="{x:.1f}" y="{y}" width="{bw2:.1f}" height="{bh}" fill="#7c5cff"/>')
        svg.append(f'<text x="{x+bw2/2:.1f}" y="{y-4}" text-anchor="middle" font-size="11" font-family="sans-serif">{v}</text>')
        svg.append(f'<text x="{x+bw2/2:.1f}" y="{h-pad+14}" text-anchor="middle" font-size="11" font-family="sans-serif">{_xml_escape(str(lab))[:10]}</text>')
    svg.append("</svg>")
    return "".join(svg)


# ============================================================
# 公共辅助
# ============================================================
def fs_resolve(ctx, path):
    return resolve_path(ctx["roots"], path)

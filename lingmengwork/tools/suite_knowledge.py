"""知识办公工具集 (Phase 92): 对标豆包工作 / 千问办公的办公与知识能力。

覆盖:
  - 脑图 mindmap      : 主题+分支 或 markdown 文本 -> Mermaid 脑图(可渲染 SVG)
  - 翻译 translate    : 多语互译(零依赖 MyMemory 免费 API, 无网优雅降级)
  - 摘要 summarize    : 抽取式摘要(零依赖, 词频打分, 无需 LLM)
  - PDF抽取 pdf_extract: 抽文本(PyPDF2 / pdftotext 优雅降级)
  - MD->DOCX markdown_to_docx: 零依赖 OOXML 生成的 Word 文档
  - 数据分析 data_analysis: CSV -> 统计/相关性/直方图(零依赖, 产出 md+html 图表)
  - 数据库 db_query    : SQLite 查询(标准库 sqlite3, 读/写/列出表)

设计纪律(与 suite_extended 一致):
  - 签名统一 def name(args, ctx) -> str
  - 路径经 suite_extended._resolve 落域防护
  - 零硬依赖, 失败以 [tool] 前缀回灌模型自我修复, 绝不抛异常中断
"""

import os
import re
import json
import html
import csv
import subprocess
import collections
import urllib.request
import urllib.parse
import urllib.error

from .suite_extended import _resolve, _cwd, _roots, _trim


_XE = html.escape


# ============================================================================
# 1. 脑图 mindmap —— 对标 豆包/千问 思维导图
# ============================================================================
def mindmap(args, ctx):
    """生成 Mermaid 脑图。topic+items 或直接给 text(markdown 标题层级) -> .mmd + 可选渲染 SVG。"""
    topic = (args.get("topic") or args.get("title") or args.get("root") or "").strip()
    items = args.get("items")
    text = args.get("text") or args.get("content") or ""
    out_path = (args.get("path") or "").strip()
    root = topic or "中心主题"
    branches = []
    if isinstance(items, list) and items:
        for it in items:
            if isinstance(it, (list, tuple)) and len(it) >= 1:
                b = str(it[0])
                subs = it[1] if len(it) > 1 else []
                branches.append((b, [str(s) for s in (subs or [])]))
            else:
                branches.append((str(it), []))
    elif str(text).strip():
        branches = _mindmap_from_text(str(text))
    else:
        return "[mindmap] 需提供 topic+items 或 text(支持 markdown 标题层级)"
    mmd = _build_mermaid_mindmap(root, branches)
    svg = _try_render_mermaid(mmd)
    out = (f"[mindmap] 主题: {root}, 分支 {len(branches)} 个\n\n```mermaid\n{mmd}\n```")
    if svg:
        out += f"\n\n已渲染: {svg}"
    if out_path:
        rp = _resolve(ctx, out_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(mmd, encoding="utf-8")
        out += f"\n源文件: {rp}"
    return out


def _mindmap_from_text(text):
    """从 markdown 标题层级或纯文本行构建脑图分支。"""
    branches = []
    stack = []  # (level, name)
    for line in str(text).splitlines():
        line = line.rstrip()
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            name = m.group(2).strip()
            # 找到父级
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            if stack:
                # 挂到父级的 subs
                parent = stack[-1]
                # parent 在 branches 中的引用
                pass
            branches.append((name, []))
            stack.append((lvl, name))
            continue
        s = line.strip()
        if s and not s.startswith(("- ", "* ")):
            branches.append((s, []))
    # 简化处理: 上述 stack 未真正嵌套, 这里做二次嵌套(标题层级)
    return _nest_headings(text)


def _nest_headings(text):
    """解析 markdown 标题为嵌套分支 (支持 #/##/###)。"""
    root_children = []
    stack = [(-1, None, root_children)]  # (level, name, children_list)
    for line in str(text).splitlines():
        m = re.match(r"^(#{1,4})\s+(.*)$", line.strip())
        if not m:
            continue
        lvl = len(m.group(1))
        name = m.group(2).strip()
        node = (name, [])
        while len(stack) > 1 and stack[-1][0] >= lvl:
            stack.pop()
        stack[-1][2].append(node)
        stack.append((lvl, name, node[1]))
    return root_children


def _build_mermaid_mindmap(root, branches):
    lines = ["mindmap", "  root((%s))" % _mmd_label(root)]
    for name, subs in branches:
        lines.append("    " + _mmd_label(name))
        for s in subs:
            lines.append("      " + _mmd_label(s))
    return "\n".join(lines)


def _mmd_label(s):
    s = str(s).replace("(", "（").replace(")", "）").replace("[", "【").replace("]", "】")
    return s[:40]


def _try_render_mermaid(mmd):
    from shutil import which
    mmdc = which("mmdc") or which("mmdc.cmd")
    if not mmdc:
        return None
    try:
        import tempfile
        d = tempfile.mkdtemp()
        src = os.path.join(d, "m.mmd")
        out = os.path.join(d, "m.svg")
        with open(src, "w", encoding="utf-8") as f:
            f.write(mmd)
        r = subprocess.run([mmdc, "-i", src, "-o", out], capture_output=True, timeout=60)
        if r.returncode == 0 and os.path.exists(out):
            return out
    except Exception:
        pass
    return None


# ============================================================================
# 2. 翻译 translate —— 对标 多语翻译
# ============================================================================
def translate(args, ctx):
    """多语翻译(零依赖 MyMemory 免费 API)。text 必填; to 目标语(默认 zh-CN), from 源语(默认 en)。"""
    text = (args.get("text") or "").strip()
    if not text:
        return "[translate] 需提供 text"
    to = (args.get("to") or "zh-CN").strip()
    fro = (args.get("from") or "en").strip()
    to = _norm_lang(to)
    fro = _norm_lang(fro)
    try:
        url = ("https://api.mymemory.translated.net/get?q=%s&langpair=%s|%s"
               % (urllib.parse.quote(text[:5000]), fro, to))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        out = data.get("responseData", {}).get("translatedText", "")
        if not out:
            return "[translate] 未获得翻译结果(可能触发频率限制)。原文:\n" + text[:500]
        return f"[translate] {fro} -> {to}:\n{out}"
    except urllib.error.HTTPError as e:
        return f"[translate] 翻译服务错误 {e.code}。原文:\n{text[:300]}"
    except Exception as e:
        return (f"[translate] 翻译暂不可用(无网络或沙箱限制): {e}\n"
                f"提示: 可稍后重试, 或配置本地翻译 API。原文前 200 字:\n{text[:200]}")


def _norm_lang(code):
    code = (code or "").strip().lower()
    if code in ("zh", "chinese", "中文", "cn"):
        return "zh-CN"
    if code in ("en", "english", "英语"):
        return "en"
    if code in ("ja", "japanese", "日语"):
        return "ja"
    if code in ("ko", "korean", "韩语"):
        return "ko"
    if code in ("fr", "french"):
        return "fr"
    if code in ("de", "german"):
        return "de"
    if code in ("ru", "russian"):
        return "ru"
    if code in ("es", "spanish"):
        return "es"
    if code in ("auto", ""):
        return "en"
    return code


# ============================================================================
# 3. 摘要 summarize —— 零依赖抽取式
# ============================================================================
def summarize(args, ctx):
    """长文摘要(零依赖抽取式): 词频打分选关键句 + 关键词。text 必填; sentences 提取句数(默认 5)。"""
    text = args.get("text") or args.get("content") or ""
    if isinstance(text, list):
        text = "\n".join(str(t) for t in text)
    text = str(text).strip()
    if not text:
        return "[summarize] 需提供 text"
    n = int((args.get("sentences") or 5))
    lang = "zh" if re.search(r"[\u4e00-\u9fff]", text[:300]) else "en"
    sents = _split_sentences(text, lang)
    if len(sents) <= n:
        return "[summarize] 原文较短, 直接返回:\n" + text
    toks = _tokenize(text, lang)
    freq = collections.Counter(t for t in toks if len(t) > 1)
    maxf = max(freq.values()) or 1
    scores = []
    for i, s in enumerate(sents):
        ts = _tokenize(s, lang)
        sc = sum(freq.get(t, 0) for t in ts) / maxf if ts else 0
        sc += 1.0 / (i + 1)  # 前置句略加权
        scores.append(sc)
    ranked = sorted(range(len(sents)), key=lambda i: scores[i], reverse=True)[:n]
    ranked.sort()
    summary = "\n".join(sents[i].strip() for i in ranked)
    kws = [w for w, _ in freq.most_common(8)]
    return (f"[summarize] 共 {len(sents)} 句, 提取 {n} 句摘要:\n\n{summary}\n\n"
            f"关键词: {', '.join(kws)}")


def _split_sentences(text, lang):
    if lang == "zh":
        parts = re.split(r"(?<=[。！？!?；;\n])", text)
    else:
        parts = re.split(r"(?<=[.!?])", text)
    return [p.strip() for p in parts if p.strip()]


def _tokenize(text, lang):
    if lang == "zh":
        toks = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", text)
        ex = []
        for t in toks:
            if re.fullmatch(r"[\u4e00-\u9fff]+", t):
                if len(t) >= 2:
                    for i in range(len(t) - 1):
                        ex.append(t[i:i + 2])
                else:
                    ex.append(t)
            else:
                ex.append(t.lower())
        return ex
    return re.findall(r"[A-Za-z0-9]+", text.lower())


# ============================================================================
# 4. PDF 抽取 pdf_extract
# ============================================================================
def pdf_extract(args, ctx):
    """从 PDF 抽取文本(PyPDF2 优先, pdftotext 回退, 均无则优雅提示)。path 必填; path_out 落盘。"""
    raw = (args.get("path") or "").strip()
    if not raw:
        return "[pdf_extract] 需提供 path (PDF 文件)"
    rp = _resolve(ctx, raw)
    if not rp.exists():
        return f"[pdf_extract] 文件不存在: {rp}"
    text = None
    try:
        import PyPDF2  # type: ignore
        reader = PyPDF2.PdfReader(str(rp))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except ImportError:
        pass
    except Exception as e:
        return f"[pdf_extract] PyPDF2 解析失败: {e}"
    if text is None:
        from shutil import which
        if which("pdftotext"):
            try:
                r = subprocess.run(["pdftotext", str(rp), "-"], capture_output=True, timeout=60)
                text = r.stdout.decode("utf-8", "replace")
            except Exception as e:
                return f"[pdf_extract] pdftotext 失败: {e}"
    if text is None:
        return ("[pdf_extract] 未安装 PDF 解析引擎(PyPDF2 / pdftotext)。\n"
                "提示: pip install PyPDF2, 或安装 poppler 的 pdftotext 后重试。")
    text = text.strip()
    if not text:
        return "[pdf_extract] 未提取到文本(可能是扫描件/图片型 PDF, 需 OCR)。"
    out_path = (args.get("path_out") or "").strip()
    if out_path:
        op = _resolve(ctx, out_path)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(text, encoding="utf-8")
        return f"[pdf_extract] 已提取 {len(text)} 字符 -> {op}"
    return "[pdf_extract] 提取文本 (前 6000 字符):\n" + _trim(text, 6000)


# ============================================================================
# 5. Markdown -> DOCX markdown_to_docx —— 零依赖 OOXML
# ============================================================================
def markdown_to_docx(args, ctx):
    """将 Markdown 转为 .docx(零依赖 zip+XML)。path 输出 .docx; md 为 markdown 内容(或 src 文件)。"""
    out_path = (args.get("path") or "").strip()
    if not out_path:
        return "[markdown_to_docx] 需提供 path (输出 .docx)"
    rp = _resolve(ctx, out_path)
    md = args.get("md") or args.get("content") or args.get("markdown")
    src = (args.get("src") or "").strip()
    if not md and src:
        sp = _resolve(ctx, src)
        if not sp.exists():
            return f"[markdown_to_docx] 源文件不存在: {sp}"
        md = sp.read_text(encoding="utf-8", errors="replace")
    if not md:
        return "[markdown_to_docx] 需提供 md(内容) 或 src(markdown 文件)"
    title = (args.get("title") or "灵梦work 文档").strip()
    _build_docx(rp, title, str(md))
    return f"[markdown_to_docx] 已生成 Word 文档: {rp} (零依赖 OOXML)"


def _build_docx(rp, title, md):
    paras = []
    for line in str(md).splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        if line.startswith("### "):
            paras.append(("h3", line[4:].strip()))
        elif line.startswith("## "):
            paras.append(("h2", line[3:].strip()))
        elif line.startswith("# "):
            paras.append(("h1", line[2:].strip()))
        elif line.strip().startswith("- ") or line.strip().startswith("* "):
            paras.append(("li", line.strip()[2:].strip()))
        else:
            paras.append(("p", line.strip()))
    body = "".join(_docx_para(kind, text) for kind, text in paras)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>%s'
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
                'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
                '</w:body></w:document>' % body)
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                     '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                 '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                 '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '</Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:rPr><w:b/><w:sz w:val="22"/></w:rPr></w:style>'
              '</w:styles>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>%s</dc:title></cp:coreProperties>' % _XE(title))
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"/>')
    rp.parent.mkdir(parents=True, exist_ok=True)
    import zipfile
    with zipfile.ZipFile(str(rp), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", styles)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)


def _docx_para(kind, text):
    style_map = {"h1": "Heading1", "h2": "Heading2", "h3": "Heading3"}
    ppr = ""
    if kind in style_map:
        ppr = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style_map[kind]
    prefix = "• " if kind == "li" else ""
    runs = _docx_runs(prefix + text)
    return "<w:p>%s%s</w:p>" % (ppr, runs)


def _docx_runs(text):
    """解析 **粗体** / *斜体* 为 runs。"""
    out = []
    i = 0
    # 简单分词: 按 ** 与 * 切
    import re as _re
    parts = _re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append('<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
                       % _XE(part[2:-2]))
        elif part.startswith("*") and part.endswith("*"):
            out.append('<w:r><w:rPr><w:i/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
                       % _XE(part[1:-1]))
        else:
            out.append('<w:r><w:t xml:space="preserve">%s</w:t></w:r>' % _XE(part))
    return "".join(out)


# ============================================================================
# 6. 数据分析 data_analysis —— 对标 表格/洞察
# ============================================================================
def data_analysis(args, ctx):
    """CSV 数据分析(零依赖): 列概览(数值/类别统计)、数值列相关性、首列直方图; 产出 md + html 图表。"""
    raw = (args.get("path") or "").strip()
    if not raw:
        return "[data_analysis] 需提供 path (CSV 文件)"
    rp = _resolve(ctx, raw)
    if not rp.exists():
        return f"[data_analysis] 文件不存在: {rp}"
    try:
        with open(str(rp), newline="", encoding="utf-8-sig", errors="replace") as f:
            rows = list(csv.reader(f))
    except Exception as e:
        return f"[data_analysis] 读取 CSV 失败: {e}"
    if not rows:
        return "[data_analysis] CSV 为空"
    header = rows[0]
    data = rows[1:]
    ncol = len(header)
    cols = [[row[i] if i < len(row) else "" for row in data] for i in range(ncol)]

    def isnum(x):
        try:
            float(x)
            return True
        except Exception:
            return False

    report = ["# 数据分析报告: %s" % rp.name, "行数: %d, 列数: %d\n" % (len(data), ncol), "## 列概览"]
    numeric_cols = []
    for i, h in enumerate(header):
        vals = cols[i]
        nums = [float(v) for v in vals if isnum(v)]
        if nums and len(nums) >= max(1, int(0.5 * len(vals))):
            numeric_cols.append((i, h, nums))
            mean = sum(nums) / len(nums)
            srt = sorted(nums)
            med = srt[len(srt) // 2]
            sd = (sum((x - mean) ** 2 for x in nums) / len(nums)) ** 0.5
            report.append("- **%s**: 数值列, n=%d, 均值=%.2f, 中位数=%.2f, 最小=%.2f, 最大=%.2f, 标准差=%.2f"
                          % (h, len(nums), mean, med, min(nums), max(nums), sd))
        else:
            c = collections.Counter(v for v in vals if v != "")
            top = c.most_common(5)
            report.append("- **%s**: 类别列, 取值 %d 种; 高频: %s"
                           % (h, len(c), ", ".join("%s(%d)" % (k, n) for k, n in top)))
    charts = []
    if len(numeric_cols) >= 2:
        report.append("\n## 数值列相关性 (Pearson)")
        for a in range(len(numeric_cols)):
            for b in range(a + 1, len(numeric_cols)):
                iA, hA, na = numeric_cols[a]
                iB, hB, nb = numeric_cols[b]
                r = _pearson(na, nb)
                if r is not None:
                    report.append("- %s ~ %s: r = %.2f" % (hA, hB, r))
    if numeric_cols:
        i, h, nums = numeric_cols[0]
        svg = _hist_svg(nums, h)
        charts.append((h, svg))
    body = "\n".join(report)
    html_out = _analysis_html(rp.name, body, charts)
    out_md = rp.with_suffix(".analysis.md")
    out_html = rp.with_suffix(".analysis.html")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(body, encoding="utf-8")
    out_html.write_text(html_out, encoding="utf-8")
    return ("[data_analysis] 已分析 %s (%d 行 x %d 列)\n\n%s\n\n"
            "详情已落盘: %s\n图表页: %s" % (rp.name, len(data), ncol, body[:2500], out_md, out_html))


def _pearson(a, b):
    n = min(len(a), len(b))
    if n < 2:
        return None
    a, b = a[:n], b[:n]
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def _hist_svg(nums, title, width=480, height=200):
    import math
    lo, hi = min(nums), max(nums)
    if hi == lo:
        bins = [nums]
    else:
        nb = 10
        step = (hi - lo) / nb
        bins = [0] * nb
        for x in nums:
            idx = min(nb - 1, int((x - lo) / step))
            bins[idx] += 1
    maxc = max(bins) or 1
    n = len(bins)
    bw = width / n
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height)]
    parts.append('<text x="4" y="14" font-size="12" fill="#8b5cf6">%s 分布</text>' % _XE(str(title)))
    for i, c in enumerate(bins):
        bh = int(height * 0.75 * c / maxc)
        x = i * bw
        y = height - bh - 20
        parts.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="#8b5cf6" opacity="0.85"/>'
                     % (x + 1, y, bw - 2, bh))
    parts.append('</svg>')
    return "\n".join(parts)


def _analysis_html(name, body, charts):
    md = body.replace("\n", "<br/>")
    chart_svgs = "\n".join("<h3>%s</h3>\n%s" % (_XE(t), svg) for t, svg in charts)
    return ("<html><head><meta charset='utf-8'><title>%s 分析</title></head><body>"
            "<h1>数据分析: %s</h1><div>%s</div><hr/>%s</body></html>"
            % (_XE(name), _XE(name), md, chart_svgs))


# ============================================================================
# 7. 数据库查询 db_query —— 对标 数据查询
# ============================================================================
def db_query(args, ctx):
    """SQLite 查询(标准库 sqlite3)。db 必填; sql 为空则列出表, 否则执行(SELECT 返回表格, 其他返回影响行数)。"""
    db = (args.get("db") or "").strip()
    if not db:
        return "[db_query] 需提供 db (sqlite 文件路径)"
    sql = (args.get("sql") or "").strip()
    import sqlite3
    dpath = str(_resolve(ctx, db))
    if not sql:
        try:
            con = sqlite3.connect(dpath)
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tabs = [r[0] for r in cur.fetchall()]
            con.close()
            return "[db_query] 数据库表: " + (", ".join(tabs) if tabs else "(无)") + "\n(提供 sql 参数执行查询)"
        except Exception as e:
            return f"[db_query] 打开数据库失败: {e}"
    try:
        con = sqlite3.connect(dpath)
        cur = con.cursor()
        cur.execute(sql)
        if sql.lower().startswith(("select", "pragma", "with")):
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            con.close()
            return _fmt_table(cols, rows)
        con.commit()
        n = cur.rowcount
        con.close()
        return f"[db_query] 执行成功, 影响行数: {n if n >= 0 else '未知'}"
    except Exception as e:
        return f"[db_query] 执行失败: {e}"


def _fmt_table(cols, rows):
    head = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = []
    for r in rows[:50]:
        body.append("| " + " | ".join(str(x) for x in r) + " |")
    more = ""
    if len(rows) > 50:
        more = "\n... (共 %d 行, 仅显示前 50)" % len(rows)
    return "[db_query] 返回 %d 行:\n%s\n%s\n%s%s" % (len(rows), head, sep, "\n".join(body), more)


# 导出清单(供注册表批量接入)
__all__ = ["mindmap", "translate", "summarize", "pdf_extract",
           "markdown_to_docx", "data_analysis", "db_query"]

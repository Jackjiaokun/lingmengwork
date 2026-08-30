"""扩展工具集 (Phase 91): 对标主流 AI 编码 / 办公产品的能力扩充。

对标对象与覆盖:
  - claude code / codex / opencode / tarework : 完整 Git 工作流 + 代码智能(test_gen/explain_code/security_scan)
  - dsh                                : 自动化/定时(schedule_task) + 工作流集成(webhook_send/notify) + 联网
  - 豆包工作 / 千问办公                 : 联网搜索(web_search) + 多模态图文音视频(image_generate/image_understand/tts/transcribe/video_generate) + 文档全家桶(make_ppt/make_xlsx/make_pdf/ocr)

设计纪律(与现有 registry 工具一致):
  - 工具函数签名统一 def name(args, ctx) -> str
  - 路径经 common.resolve_path 落域防护
  - 零硬依赖: 联网用标准库 urllib; 文档用标准库 zipfile+xml; 多模态委托 multimodal_adapters(Pillow/edge-tts 可选, 缺失自动降级); OCR/STT 需外部引擎时优雅降级并提示, 绝不崩溃
  - 失败信息以 [tool] 前缀回灌模型, 让其自我修复, 而非抛异常中断
"""

import os
import re
import json
import html
import ast
import zipfile
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

from .common import ToolError, resolve_path


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
# A. 联网与 API —— 对标 豆包/千问/dsh 联网能力
# ============================================================================
def web_fetch(args, ctx):
    """抓取网页 URL, 抽取可读正文(去除脚本/样式/标签)。"""
    url = (args.get("url") or "").strip()
    if not url:
        return "[web_fetch] 需提供 url 参数"
    if not url.startswith("http://") and not url.startswith("https://"):
        return f"[web_fetch] 仅支持 http/https 协议: {url}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
        enc = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype)
        if m:
            enc = m.group(1)
        try:
            text = raw.decode(enc, "replace")
        except Exception:
            text = raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return f"[web_fetch] HTTP 错误 {e.code}: {url}"
    except Exception as e:
        return f"[web_fetch] 抓取失败: {e}"
    # 去脚本/样式
    text = re.sub(r"(?is)<script.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<head.*?</head>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    # 去标签, 保留换行
    text = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6])[ >]", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    title = ""
    mt = re.search(r"(?is)<title>(.*?)</title>", text)
    if mt:
        title = mt.group(1).strip()
    return _trim(f"[web_fetch] {url}\n标题: {title}\n\n{text}", args.get("max_chars") or 20000)


def web_search(args, ctx):
    """联网搜索(零依赖 DuckDuckGo lite HTML 抓取)。无网络时优雅降级提示。"""
    q = (args.get("query") or "").strip()
    if not q:
        return "[web_search] 需提供 query 参数"
    n = int((args.get("limit") or 8))
    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html_src = resp.read().decode("utf-8", "replace")
    except Exception as e:
        return (f"[web_search] 联网搜索暂不可用(无网络或沙箱限制): {e}\n"
                f"提示: 可改用 web_fetch 直接抓取已知页面, 或本机配置搜索 API 后重试。")
    out = [f"[web_search] 查询「{q}」:"]
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html_src, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html_src, re.DOTALL)
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"', html_src)
    cnt = 0
    for i in range(min(len(titles), n)):
        t = _strip_html(titles[i]).strip()
        s = _strip_html(snippets[i]).strip() if i < len(snippets) else ""
        link = links[i] if i < len(links) else ""
        # DuckDuckGo 重定向链接解包
        link = _unpack_ddg(link)
        if not t:
            continue
        out.append(f"\n{i+1}. {t}\n   {s}\n   {link}")
        cnt += 1
        if cnt >= n:
            break
    if cnt == 0:
        return f"[web_search] 未解析到结果(可能触达频率限制或网络受限)。查询: {q}"
    return "\n".join(out)


def http_request(args, ctx):
    """调用任意 REST API。method 默认 GET; 支持 headers/body/json。返回状态 + 响应(截断)。"""
    url = (args.get("url") or "").strip()
    if not url:
        return "[http_request] 需提供 url 参数"
    method = (args.get("method") or "GET").strip().upper()
    data = None
    headers = {"User-Agent": _ua()}
    if isinstance(args.get("headers"), dict):
        headers.update({str(k): str(v) for k, v in args["headers"].items()})
    if args.get("json") is not None:
        data = json.dumps(args["json"]).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif args.get("body") is not None:
        data = args["body"].encode("utf-8") if isinstance(args["body"], str) else args["body"]
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return f"[http_request] HTTP {e.code}\n{body[:2000]}"
    except Exception as e:
        return f"[http_request] 请求失败: {e}"
    # JSON 美化
    pretty = body
    if "json" in ctype or body.strip().startswith(("{", "[")):
        try:
            pretty = json.dumps(json.loads(body), ensure_ascii=False, indent=2)
        except Exception:
            pretty = body
    return f"[http_request] {method} {url} -> {status} ({ctype})\n\n{_trim(pretty, args.get('max_chars') or 4000)}"


def _strip_html(s):
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return html.unescape(s).strip()


def _unpack_ddg(link):
    m = re.search(r"uddg=([^&]+)", link)
    if m:
        try:
            return urllib.parse.unquote(m.group(1))
        except Exception:
            return link
    return link


# ============================================================================
# B. 完整 Git 工作流 —— 对标 Claude Code / Codex
# ============================================================================
def _git_run(cmd, ctx, timeout=60):
    cwd = _cwd(ctx)
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise ToolError(f"git 命令超时 ({timeout}s): {cmd}")
    except Exception as e:
        raise ToolError(f"git 执行失败: {e}")
    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else (r.stdout or "")
    err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else (r.stderr or "")
    return r.returncode, out, err


def _git_guard(ctx, tool="git"):
    rc, _, err = _git_run("git rev-parse --is-inside-work-tree", ctx, 20)
    if rc != 0:
        return f"[{tool}] 当前目录不是 git 仓库: {err.strip()[:200]}"
    return None


def git_status(args, ctx):
    """查看工作区状态 (对标 git status --short + 当前分支)。"""
    g = _git_guard(ctx, "git_status")
    if g:
        return g
    rc, out, _ = _git_run("git status --short -b", ctx, 30)
    if not out.strip():
        return "[git_status] 工作区干净, 无改动。"
    return "[git_status]\n" + out.rstrip()


def git_diff(args, ctx):
    """查看差异。cached=true 看暂存区; path 限定文件; stat=true 仅统计。"""
    g = _git_guard(ctx, "git_diff")
    if g:
        return g
    parts = ["git diff"]
    if args.get("cached") in (True, "true", "1"):
        parts.append("--cached")
    if args.get("stat") in (True, "true", "1"):
        parts.append("--stat")
    p = (args.get("path") or "").strip()
    if p:
        parts.append("--")
        parts.append(_resolve(ctx, p) if not p.startswith("-") else p)
    rc, out, _ = _git_run(" ".join(parts), ctx, 60)
    if not out.strip():
        return "[git_diff] 无差异。"
    return "[git_diff]\n" + _trim(out, args.get("max_chars") or 20000)


def git_log(args, ctx):
    """查看提交历史。n 限制条数(默认 20), oneline 格式。"""
    g = _git_guard(ctx, "git_log")
    if g:
        return g
    n = int((args.get("n") or 20))
    rc, out, _ = _git_run(f"git log --oneline -n {n}", ctx, 30)
    if not out.strip():
        return "[git_log] 暂无提交历史。"
    return f"[git_log] 最近 {n} 条:\n" + out.rstrip()


def git_branch(args, ctx):
    """列出分支 (a=true 含远程)。"""
    g = _git_guard(ctx, "git_branch")
    if g:
        return g
    flag = "-a" if args.get("a") in (True, "true", "1") else ""
    rc, out, _ = _git_run(f"git branch {flag}".strip(), ctx, 30)
    return "[git_branch]\n" + (out.rstrip() or "(无分支)")


def git_checkout(args, ctx):
    """切换/新建分支。ref 必填; create=true 新建分支。"""
    g = _git_guard(ctx, "git_checkout")
    if g:
        return g
    ref = (args.get("ref") or "").strip()
    if not ref:
        return "[git_checkout] 需提供 ref (分支名/commit)"
    cmd = "git checkout -b " + ref if args.get("create") in (True, "true", "1") else "git checkout " + ref
    rc, out, err = _git_run(cmd, ctx, 60)
    if rc != 0:
        return f"[git_checkout] 失败: {err.strip()[:300]}"
    return f"[git_checkout] 成功:\n{out.strip()[:300]}"


def git_stash(args, ctx):
    """贮藏工作区。action=push(默认)|list|pop|show。"""
    g = _git_guard(ctx, "git_stash")
    if g:
        return g
    action = (args.get("action") or "push").strip().lower()
    if action == "list":
        rc, out, _ = _git_run("git stash list", ctx, 20)
        return "[git_stash] 列表:\n" + (out.rstrip() or "(空)")
    if action == "show":
        rc, out, _ = _git_run("git stash show -p", ctx, 30)
        return "[git_stash] 最新贮藏差异:\n" + (out.rstrip() or "(无)")
    if action == "pop":
        rc, out, err = _git_run("git stash pop", ctx, 60)
        if rc != 0:
            return f"[git_stash] pop 失败: {err.strip()[:300]}"
        return "[git_stash] 已弹出并应用最新贮藏。"
    # push
    msg = (args.get("message") or "").strip()
    cmd = "git stash push" + (f' -m "{msg}"' if msg else "")
    rc, out, err = _git_run(cmd, ctx, 60)
    if rc != 0:
        return f"[git_stash] 失败: {err.strip()[:300]}"
    return "[git_stash] 已贮藏当前改动:\n" + (out.strip()[:300] or "(done)")


def git_pr_draft(args, ctx):
    """生成 PR 草稿(标题/描述/改动范围), 不推送。可选 path 落盘为 markdown。"""
    g = _git_guard(ctx, "git_pr_draft")
    if g:
        return g
    base = (args.get("base") or "main").strip()
    rc, cur, _ = _git_run("git rev-parse --abbrev-ref HEAD", ctx, 20)
    cur = cur.strip() or "HEAD"
    rc, diffstat, _ = _git_run(f"git diff {base}...{cur} --stat", ctx, 30)
    rc, log, _ = _git_run(f"git log {base}..{cur} --oneline", ctx, 30)
    rc, body, _ = _git_run(f"git diff {base}...{cur}", ctx, 60)
    title = f"Merge {cur} -> {base}"
    md = [f"# {title}", "", "## 改动范围", "```", (diffstat.strip() or "(无)"), "```", "",
          "## 提交列表", (log.strip() or "(无)"), "", "## 差异摘要(前 6000 字符)", "```diff",
          _trim(body, 6000), "```"]
    md = "\n".join(md)
    out_path = (args.get("path") or "").strip()
    if out_path:
        rp = _resolve(ctx, out_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(md, encoding="utf-8")
        return f"[git_pr_draft] 已生成 PR 草稿: {rp}\n\n{md[:600]}"
    return "[git_pr_draft]\n" + md


# ============================================================================
# C. 多模态真实生成 —— 对标 豆包/千问 图文音视频
# ============================================================================
def _multimodal_out(ctx):
    return os.path.join(str(_roots(ctx)[0]), "outputs", "multimodal")


def _lazy_adapters():
    try:
        from . import multimodal_adapters as ma
        return ma
    except Exception:
        return None


def image_generate(args, ctx):
    """文生图/图生图/超分 (委托 multimodal_adapters)。有图生成 key 走远程真生成, 否则本地 Pillow 真实信息图。"""
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return "[image_generate] 需提供 prompt"
    ma = _lazy_adapters()
    if ma is None:
        return "[image_generate] 适配层不可用(Pillow 缺失)。"
    mode = (args.get("mode") or "gen").strip().lower()
    img_path = (args.get("image_path") or "").strip()
    if img_path:
        img_path = str(_resolve(ctx, img_path))
    res = ma.render("image", prompt, blueprint=prompt, ctx="", out_dir=_multimodal_out(ctx),
                    mode=mode, image_path=img_path or None)
    if not res:
        return "[image_generate] 生成失败。"
    return _fmt_media(res)


def image_understand(args, ctx):
    """图像理解: 抽取尺寸/格式/主色等元信息 + 启发式描述(零依赖)。有视觉 LLM 时效果更佳。"""
    raw = (args.get("path") or "").strip()
    if not raw:
        return "[image_understand] 需提供 path"
    rp = _resolve(ctx, raw)
    if not rp.exists():
        return f"[image_understand] 文件不存在: {rp}"
    try:
        from PIL import Image
    except Exception:
        return f"[image_understand] 无法读取图像(缺 Pillow): {rp}\n提示: 安装 Pillow 后可抽取尺寸/格式/主色。"
    try:
        with Image.open(str(rp)) as im:
            w, h = im.size
            fmt = im.format
            mode = im.mode
            # 主色(缩小采样)
            small = im.convert("RGB").resize((32, 32))
            colors = small.getcolors(32 * 32) or []
            colors.sort(key=lambda c: -c[0])
            top = colors[:3]
            dom = ", ".join("#%02X%02X%02X" % c[1] for c in top)
        return (f"[image_understand] {rp.name}\n格式: {fmt}  模式: {mode}  尺寸: {w}x{h}\n"
                f"主色: {dom}\n注: 此为基于像素统计的启发式理解; 接入视觉 LLM 后可获得语义描述。")
    except Exception as e:
        return f"[image_understand] 解析失败: {e}"


def tts(args, ctx):
    """语音合成(TTS)。委托 multimodal_adapters: 优先 edge_tts 真实 MP3, 否则降级文字稿+声波占位图。"""
    text = (args.get("text") or "").strip()
    if not text:
        return "[tts] 需提供 text"
    ma = _lazy_adapters()
    if ma is None:
        return "[tts] 适配层不可用(Pillow 缺失)。"
    res = ma.render("audio", text, blueprint=text, ctx="", out_dir=_multimodal_out(ctx),
                    mode="tts", voice=(args.get("voice") or ""),
                    rate=(args.get("rate") or ""), pitch=(args.get("pitch") or ""))
    if not res:
        return "[tts] 合成失败。"
    return _fmt_media(res)


def transcribe(args, ctx):
    """语音转写(STT)。需本地语音识别引擎(whisper / SpeechRecognition), 缺失则优雅提示。"""
    raw = (args.get("path") or "").strip()
    if not raw:
        return "[transcribe] 需提供 path (音频文件)"
    rp = _resolve(ctx, raw)
    if not rp.exists():
        return f"[transcribe] 文件不存在: {rp}"
    # 优先 openai-whisper
    try:
        import whisper  # type: ignore
        model = whisper.load_model("base")
        res = model.transcribe(str(rp))
        txt = res.get("text", "")
        return f"[transcribe] (whisper)\n{txt}"
    except ImportError:
        pass
    except Exception as e:
        return f"[transcribe] whisper 转写失败: {e}"
    # 其次 SpeechRecognition
    try:
        import speech_recognition as sr  # type: ignore
        r = sr.Recognizer()
        with sr.AudioFile(str(rp)) as src:
            audio = r.record(src)
        txt = r.recognize_google(audio, language=(args.get("language") or "zh-CN"))
        return f"[transcribe] (SpeechRecognition)\n{txt}"
    except ImportError:
        pass
    except Exception as e:
        return f"[transcribe] 语音识别失败: {e}"
    return ("[transcribe] 当前环境未安装本地语音识别引擎(whisper / SpeechRecognition), 无法转写。\n"
            "提示: pip install openai-whisper 后重试用本工具; 或接入云端 STT API。")


def video_generate(args, ctx):
    """文生视频/图生视频/剪辑合成 (委托 multimodal_adapters)。有视频 key 走远程 MP4, 否则本地 Pillow 真实 GIF 动图。"""
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return "[video_generate] 需提供 prompt"
    ma = _lazy_adapters()
    if ma is None:
        return "[video_generate] 适配层不可用(Pillow 缺失)。"
    mode = (args.get("mode") or "gen").strip().lower()
    img_path = (args.get("image_path") or "").strip()
    if img_path:
        img_path = str(_resolve(ctx, img_path))
    res = ma.render("video", prompt, blueprint=prompt, ctx="", out_dir=_multimodal_out(ctx),
                    mode=mode, image_path=img_path or None)
    if not res:
        return "[video_generate] 生成失败。"
    return _fmt_media(res)


def _fmt_media(res):
    if isinstance(res, dict):
        file = res.get("file")
        note = res.get("note", "")
        mime = res.get("mime", "")
        real = res.get("real")
        line = f"[media] 已生成: {file}\n类型: {mime}  真实媒体: {real}\n说明: {note}"
        return line
    return str(res)


# ============================================================================
# D. 文档全家桶 —— 对标 千问办公/豆包 文档
# ============================================================================
def make_ppt(args, ctx):
    """生成 PPTX 演示文稿(零依赖 zip+XML 构建)。slides=列表[{title,bullets:[...]}]; 或 body 为分页 markdown。"""
    out_path = (args.get("path") or "").strip()
    if not out_path:
        return "[make_ppt] 需提供 path (输出 .pptx 路径)"
    rp = _resolve(ctx, out_path)
    title = (args.get("title") or "灵梦work 演示文稿").strip()
    slides = _parse_slides(args)
    _build_pptx(rp, title, slides)
    return f"[make_ppt] 已生成演示文稿: {rp} ({len(slides)} 页, 零依赖 OOXML)"


def _parse_slides(args):
    slides = args.get("slides")
    if isinstance(slides, list) and slides:
        out = []
        for s in slides:
            if isinstance(s, dict):
                out.append((str(s.get("title", "")), [str(b) for b in (s.get("bullets") or [])]))
            else:
                out.append((str(s), []))
        return out
    # 由 body 按 ## 分页解析
    body = args.get("body") or args.get("content") or ""
    out = []
    cur_t, cur_b = "", []
    for line in str(body).splitlines():
        if line.startswith("## "):
            if cur_t or cur_b:
                out.append((cur_t, cur_b))
            cur_t = line[3:].strip()
            cur_b = []
        elif line.startswith("# "):
            if cur_t or cur_b:
                out.append((cur_t, cur_b))
            cur_t = line[2:].strip()
            cur_b = []
        elif line.strip().startswith("- ") or line.strip().startswith("* "):
            cur_b.append(line.strip()[2:].strip())
        elif line.strip():
            cur_b.append(line.strip())
    if cur_t or cur_b:
        out.append((cur_t, cur_b))
    return out or [("幻灯片", [])]


def _build_pptx(rp, title, slides):
    from . import office as _office
    esc = _office._xml_escape if hasattr(_office, "_xml_escape") else html.escape
    n = len(slides)
    # 关系 id 分配: rId1..rIdN 为 slides, rId(N+1) 为 master
    master_rid = "rId%d" % (n + 1)
    # ---- 各 slide xml ----
    slide_xmls = []
    for i, (st, bullets) in enumerate(slides):
        shapes = []
        # 标题
        shapes.append(
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="838200" y="365125"/><a:ext cx="10515600" cy="1325563"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>%s</a:t></a:r></a:p></p:txBody></p:sp>' % esc(st))
        # 正文
        lines = "".join(
            '<a:p><a:r><a:rPr/><a:t>%s</a:t></a:r></a:p>' % esc(b) for b in bullets) or '<a:p/>'
        shapes.append(
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="838200" y="1825625"/><a:ext cx="10515600" cy="4355337"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            '<p:txBody><a:bodyPr/><a:lstStyle/>%s</p:txBody></p:sp>' % lines)
        sp_tree = ('<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/>'
                   '<p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:spPr/>%s</p:spTree>'
                   % "".join(shapes))
        xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
               'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
               '<p:cSld>%s</p:cSld><p:clrMapOvr><a:overrideClrMapping '
               'bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
               'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
               'hlink="hlink" folHlink="folHlink"/></p:clrMapOvr></p:sld>' % sp_tree)
        slide_xmls.append(xml)
    # ---- presentation.xml ----
    sld_ids = "".join(
        '<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, i + 1) for i in range(n))
    presentation = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="%s"/></p:sldMasterIdLst>'
                    '<p:sldIdLst>%s</p:sldIdLst>'
                    '<p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
                    '</p:presentation>' % (master_rid, sld_ids))
    # ---- presentation rels ----
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(n):
        rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide%d.xml"/>' % (i + 1, i + 1))
    rels.append('<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>' % master_rid)
    rels.append('</Relationships>')
    presentation_rels = "".join(rels)
    # ---- slideMaster ----
    master = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
              'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
              'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
              '<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></p:bgPr></p:bg>'
              '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
              '<p:grpSpPr/></p:spTree></p:cSld>'
              '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
              'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
              '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
              '</p:sldMaster>')
    master_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
                   '</Relationships>')
    # ---- slideLayout ----
    layout = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
              'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
              'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
              'type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>'
              '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
              '<p:grpSpPr/></p:spTree></p:cSld>'
              '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')
    layout_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
                   '</Relationships>')
    # ---- theme ----
    theme = _minimal_theme()
    # ---- content types ----
    overrides = ['<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
                 '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
                 '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
                 '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
                 '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
                 '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>']
    for i in range(n):
        overrides.append('<Override PartName="/ppt/slides/slide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % (i + 1))
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     + "".join(overrides) + '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                 '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                 '</Relationships>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>%s</dc:title></cp:coreProperties>' % esc(title))
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"/>')
    rp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(rp), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("ppt/presentation.xml", presentation)
        z.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        z.writestr("ppt/slideMasters/slideMaster1.xml", master)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels)
        z.writestr("ppt/slideLayouts/slideLayout1.xml", layout)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", layout_rels)
        z.writestr("ppt/theme/theme1.xml", theme)
        for i, xml in enumerate(slide_xmls):
            z.writestr("ppt/slides/slide%d.xml" % (i + 1), xml)
            z.writestr("ppt/slides/_rels/slide%d.xml.rels" % (i + 1),
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                       '</Relationships>')
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)


def _minimal_theme():
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme">'
            '<a:themeElements><a:clrScheme name="Office">'
            '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
            '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
            '<a:dk2><a:srgbClr val="1F3864"/></a:dk2>'
            '<a:lt2><a:srgbClr val="EEECE1"/></a:lt2>'
            '<a:accent1><a:srgbClr val="8B5CF6"/></a:accent1>'
            '<a:accent2><a:srgbClr val="10B981"/></a:accent2>'
            '<a:accent3><a:srgbClr val="F472B6"/></a:accent3>'
            '<a:accent4><a:srgbClr val="6366F1"/></a:accent4>'
            '<a:accent5><a:srgbClr val="ED7D31"/></a:accent5>'
            '<a:accent6><a:srgbClr val="FFC000"/></a:accent6>'
            '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
            '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
            '</a:clrScheme><a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri"/></a:majorFont>'
            '<a:minorFont><a:latin typeface="Calibri"/></a:minorFont></a:fontScheme>'
            '<a:fmtScheme name="Office"><a:fillStyleLst/><a:lnStyleLst/><a:effectStyleLst/><a:bgFillStyleLst/></a:fmtScheme>'
            '</a:themeElements></a:theme>')


def make_xlsx(args, ctx):
    """生成 XLSX 表格(零依赖 zip+XML)。data=二维数组或对象列表; sheet 表名。"""
    out_path = (args.get("path") or "").strip()
    if not out_path:
        return "[make_xlsx] 需提供 path (输出 .xlsx 路径)"
    rp = _resolve(ctx, out_path)
    data = args.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return "[make_xlsx] data 解析失败(需 JSON 数组)"
    if not isinstance(data, list) or not data:
        return "[make_xlsx] 需提供 data(二维数组或对象列表)"
    sheet = (args.get("sheet") or "Sheet1").strip()
    _build_xlsx(rp, sheet, data)
    return f"[make_xlsx] 已生成表格: {rp} (零依赖 OOXML, {len(data)} 行)"


def _build_xlsx(rp, sheet, data):
    # 统一为二维单元格(字符串/数值)
    rows = []
    for r in data:
        if isinstance(r, dict):
            rows.append(list(r.values()))
        elif isinstance(r, list):
            rows.append(list(r))
        else:
            rows.append([r])
    cells_xml = []
    for ri, row in enumerate(rows, 1):
        cs = []
        for ci, val in enumerate(row, 1):
            ref = "%s%d" % (_col_name(ci), ri)
            if isinstance(val, bool):
                cs.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, _xe(str(val))))
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                cs.append('<c r="%s"><v>%s</v></c>' % (ref, val))
            else:
                cs.append('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                          % (ref, _xe("" if val is None else str(val))))
        cells_xml.append('<row r="%d">%s</row>' % (ri, "".join(cs)))
    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 '<sheetData>%s</sheetData></worksheet>' % "".join(cells_xml))
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                 '</Relationships>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>' % _xe(sheet))
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '</Relationships>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>%s</dc:title></cp:coreProperties>' % _xe(sheet))
    rp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(rp), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr("docProps/core.xml", core)


def _col_name(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xe(s):
    return html.escape(str(s), quote=True)


def make_pdf(args, ctx):
    """生成 PDF 文档(零依赖最小 PDF, 支持多页文本)。title/body(markdown 或纯文本)。"""
    out_path = (args.get("path") or "").strip()
    if not out_path:
        return "[make_pdf] 需提供 path (输出 .pdf 路径)"
    rp = _resolve(ctx, out_path)
    title = (args.get("title") or "灵梦work 文档").strip()
    body = args.get("body") or args.get("content") or ""
    if isinstance(body, list):
        body = "\n".join(str(b) for b in body)
    _build_pdf(rp, title, str(body))
    return f"[make_pdf] 已生成 PDF: {rp} (零依赖)"


def _build_pdf(rp, title, body):
    lines = [title, "=" * min(40, len(title)), ""]
    for raw in body.splitlines():
        raw = raw.rstrip()
        if raw.startswith("# "):
            lines.append(raw[2:].strip().upper())
            lines.append("")
        elif raw.startswith("## "):
            lines.append(raw[3:].strip())
            lines.append("")
        elif raw.startswith("- ") or raw.startswith("* "):
            lines.append("  - " + raw[2:].strip())
        elif raw.strip():
            lines.append(raw)
    # 分页(每页 ~50 行)
    pages = [lines[i:i + 50] for i in range(0, len(lines), 50)] or [[""]]
    objs = []
    # 1 Catalog, 2 Pages, 3.. Font, 4.. Page contents, Page objects
    font_obj = 3
    page_objs = []
    content_objs = []
    next_id = 4
    for _ in pages:
        content_objs.append(next_id); next_id += 1
        page_objs.append(next_id); next_id += 1
    kids = " ".join("%d 0 R" % p for p in page_objs)
    n_pages = len(pages)
    objects = {}
    objects[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = "<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n_pages)
    objects[font_obj] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for idx, pg in enumerate(pages):
        cid = content_objs[idx]
        pid = page_objs[idx]
        stream = _pdf_text_stream(pg)
        objects[cid] = "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream.encode("latin-1", "replace")), stream)
        objects[pid] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                        "/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                        % (font_obj, cid))
    # 写文件
    rp.parent.mkdir(parents=True, exist_ok=True)
    out = []
    out.append("%PDF-1.4")
    offsets = {}
    buf = "%PDF-1.4\n"
    for oid in sorted(objects):
        offsets[oid] = len(buf.encode("latin-1", "replace"))
        buf += "%d 0 obj\n%s\nendobj\n" % (oid, objects[oid])
    xref_pos = len(buf.encode("latin-1", "replace"))
    max_id = max(objects)
    buf += "xref\n0 %d\n" % (max_id + 1)
    buf += "0000000000 65535 f \n"
    for oid in range(1, max_id + 1):
        if oid in offsets:
            buf += "%010d 00000 n \n" % offsets[oid]
        else:
            buf += "0000000000 65535 f \n"
    buf += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (max_id + 1, xref_pos))
    with open(str(rp), "wb") as f:
        f.write(buf.encode("latin-1", "replace"))


def _pdf_text_stream(lines):
    parts = ["BT", "/F1 12 Tf", "50 740 Td", "14 TL"]
    for ln in lines:
        safe = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        safe = safe[:95]
        parts.append("(%s) Tj" % safe)
        parts.append("T*")
    parts.append("ET")
    return "\n".join(parts)


def ocr(args, ctx):
    """OCR 图片转文字。需 tesseract/pytesseract; 缺失优雅提示。"""
    raw = (args.get("path") or "").strip()
    if not raw:
        return "[ocr] 需提供 path (图片)"
    rp = _resolve(ctx, raw)
    if not rp.exists():
        return f"[ocr] 文件不存在: {rp}"
    try:
        from shutil import which
        if not which("tesseract"):
            return ("[ocr] 未检测到 tesseract 引擎。请安装 Tesseract OCR 并加入 PATH, "
                    "或 pip install pytesseract; 当前无法执行 OCR。")
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
        lang = (args.get("lang") or "chi_sim+eng")
        txt = pytesseract.image_to_string(Image.open(str(rp)), lang=lang)
        return f"[ocr] 识别结果:\n{txt.strip()}" if txt.strip() else "[ocr] 未识别到文字。"
    except ImportError:
        return "[ocr] 未安装 pytesseract; 请 pip install pytesseract 并安装 Tesseract。"
    except Exception as e:
        return f"[ocr] OCR 失败: {e}"


# ============================================================================
# E. 自动化与集成 —— 对标 dsh 工作流/定时
# ============================================================================
def schedule_task(args, ctx):
    """创建定时/自动化任务(写入工作区 .lmw_schedules.json)。name/rrule/prompt 必填。"""
    name = (args.get("name") or "").strip()
    rrule = (args.get("rrule") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    if not (name and prompt):
        return "[schedule_task] 需提供 name 与 prompt"
    root = str(_roots(ctx)[0])
    fp = os.path.join(root, ".lmw_schedules.json")
    tasks = []
    if os.path.exists(fp):
        try:
            tasks = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            tasks = []
    tid = "sch_%d" % int(os.urandom(4).hex(), 16) if hasattr(os, "urandom") else "sch_%d" % len(tasks)
    entry = {
        "id": tid, "name": name, "prompt": prompt,
        "rrule": rrule or "once", "enabled": args.get("enabled") not in (False, "false", "0"),
        "created_at": _now_iso(),
    }
    tasks.append(entry)
    Path(fp).write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    return ("[schedule_task] 已创建定时任务:\n"
            f"  id: {tid}\n  name: {name}\n  rrule: {entry['rrule']}\n  enabled: {entry['enabled']}\n"
            f"存储: {fp}")


def webhook_send(args, ctx):
    """向外部 webhook 推送(JSON)。url 必填, payload 任意对象。"""
    url = (args.get("url") or "").strip()
    if not url:
        return "[webhook_send] 需提供 url"
    payload = args.get("payload")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b"{}"
    try:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": _ua()},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = resp.read().decode("utf-8", "replace")
        return f"[webhook_send] 推送成功 -> HTTP {resp.status}\n{_trim(out, 2000)}"
    except Exception as e:
        return f"[webhook_send] 推送失败: {e}"


def notify(args, ctx):
    """发送通知(落盘工作区 .lmw_notifications.json + 尽力触发系统 toast)。"""
    title = (args.get("title") or "灵梦work").strip()
    message = (args.get("message") or "").strip()
    level = (args.get("level") or "info").strip()
    root = str(_roots(ctx)[0])
    fp = os.path.join(root, ".lmw_notifications.json")
    notes = []
    if os.path.exists(fp):
        try:
            notes = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            notes = []
    note = {"title": title, "message": message, "level": level, "at": _now_iso()}
    notes.append(note)
    notes = notes[-200:]
    Path(fp).write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    # 尽力 toast(不阻塞)
    _toast(title, message)
    return f"[notify] 已记录通知: {title} - {message}\n存储: {fp}"


def _toast(title, message):
    try:
        import ctypes
        # Windows balloon toast (best-effort)
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)  # 0x40 = info icon
    except Exception:
        pass


def _now_iso():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


# ============================================================================
# F. 代码智能增强 —— 领先一代
# ============================================================================
def test_gen(args, ctx):
    """为源文件生成 pytest 单元测试脚手架(零依赖 AST 解析顶层函数/类)。可选 path 落盘。"""
    code, label = _load_source(args, ctx)
    if code.startswith("[test_gen] 错误"):
        return code
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"[test_gen] 解析失败(语法错误): {e}"
    funcs, classes = [], []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
    lines = ["import pytest", "import sys, os", "sys.path.insert(0, os.path.dirname(__file__))",
             "", f"# 自动生成的测试脚手架: {label}", ""]
    for fn in funcs:
        lines.append(f"def test_{fn}():")
        lines.append(f"    # TODO: 补充 {fn} 的断言")
        lines.append(f"    assert True")
        lines.append("")
    for cl in classes:
        lines.append(f"class Test{cl}:")
        lines.append(f"    def test_instantiate(self):")
        lines.append(f"        # TODO: 补充 {cl} 的实例化/方法断言")
        lines.append(f"        assert True")
        lines.append("")
    if not funcs and not classes:
        lines.append("# 未在顶层发现函数/类, 请检查目标文件。")
    content = "\n".join(lines)
    out_path = (args.get("path_out") or "").strip()
    if out_path:
        rp = _resolve(ctx, out_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(content, encoding="utf-8")
        return f"[test_gen] 已生成测试: {rp}\n发现函数 {len(funcs)} 个, 类 {len(classes)} 个。"
    return ("[test_gen] 测试脚手架(发现函数 %d 个, 类 %d 个):\n```python\n%s\n```"
            % (len(funcs), len(classes), content))


def explain_code(args, ctx):
    """代码解释(零依赖 AST 摘要): 行数/顶层定义/复杂度提示/导入。返回结构化说明。"""
    code, label = _load_source(args, ctx)
    if code.startswith("[explain_code] 错误"):
        return code
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"[explain_code] 语法错误, 无法解析: {e}"
    funcs, classes, imports = [], [], []
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.Import):
            for n in node.names:
                imports.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            imports.append("from %s import %s" % (node.module or "", ", ".join(a.name for a in node.names)))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.add(node.func.id)
    loc = len(code.splitlines())
    out = [f"[explain_code] {label}", f"规模: {loc} 行, {len(funcs)} 函数, {len(classes)} 类",
           f"顶层函数: {', '.join(funcs) or '(无)'}",
           f"顶层类: {', '.join(classes) or '(无)'}",
           f"导入: {', '.join(imports) or '(无)'}",
           f"调用(疑似): {', '.join(sorted(calls)) or '(无)'}"]
    return "\n".join(out)


def security_scan(args, ctx):
    """仓库安全扫描(零依赖规则): 危险函数(eval/exec)、硬编码密钥、SQL 拼接、危险导入等。"""
    base = _cwd(ctx)
    target = (args.get("path") or "").strip()
    if target:
        base = str(_resolve(ctx, target))
    patterns = [
        ("危险函数", re.compile(r"\b(eval|exec|os\.system|subprocess\.os\.system|pickle\.loads|marshal\.loads|yaml\.load)\s*\(")),
        ("硬编码密钥", re.compile(r"(?i)('|\")(api[_-]?key|secret|token|password|passwd|pwd)('|\")\s*[:=]\s*('|\")[^\s'\"]{6,}('|\")")),
        ("SQL 拼接", re.compile(r"(?i)(execute|cursor\.execute|raw)\s*\([^)]*\%|\+|f[\"']")),
        ("调试后门", re.compile(r"(?i)(print\(|pdb\.set_trace|debug=True)")),
        ("危险反序列化", re.compile(r"(?i)(pickle|marshal|yaml\.load)\(")),
        ("命令注入风险", re.compile(r"(?i)(os\.popen|subprocess\.Popen|os\.system)\(")),
    ]
    code_ext = (".py", ".js", ".ts", ".java", ".go", ".rb", ".php", ".cs", ".sh")
    findings = []
    scanned = 0
    for root, dirs, fns in os.walk(base):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", "dist", "build", ".venv", "venv")]
        for fn in fns:
            if not fn.lower().endswith(code_ext):
                continue
            fp = os.path.join(root, fn)
            try:
                text = Path(fp).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            scanned += 1
            rel = os.path.relpath(fp, _cwd(ctx))
            for cat, rx in patterns:
                for m in rx.finditer(text):
                    ln = text[:m.start()].count("\n") + 1
                    snip = text.splitlines()[ln - 1].strip()[:90] if ln <= len(text.splitlines()) else ""
                    findings.append({"file": rel, "line": ln, "type": cat, "match": snip})
    if not findings:
        return f"[security_scan] 扫描 {scanned} 个文件, 未发现明显风险模式(基于静态规则, 非全面审计)。"
    out = [f"[security_scan] 扫描 {scanned} 个文件, 命中 {len(findings)} 处风险:"]
    for f in findings[:60]:
        out.append(f"  - [{f['type']}] {f['file']}:{f['line']}  {f['match']}")
    out.append("\n注: 静态规则扫描, 需结合上下文判断; 建议对命中项逐一人工复核。")
    return "\n".join(out)


def _load_source(args, ctx):
    p = (args.get("path") or "").strip()
    inline = args.get("code") or args.get("source")
    if inline:
        return str(inline), "snippet"
    if p:
        rp = _resolve(ctx, p)
        if not rp.exists():
            return f"[test_gen] 错误: 文件不存在: {rp}", p
        return rp.read_text(encoding="utf-8", errors="replace"), p
    return "[test_gen] 错误: 需提供 path 或 code", p

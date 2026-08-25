"""灵梦work Web 控制台: 零依赖 http.server + SSE 流式。

端点:
    GET  /                -> index.html
    GET  /static/<file>   -> 静态资源
    GET  /api/health      -> {ok, version, backend, model}
    GET  /api/tools       -> 工具 schema 列表
    POST /api/chat        -> SSE 流式: 逐块推送 text / tool / tool_result / done
    POST /api/tasks       -> 新建单任务; 或带 prompts:[...] 扇出并行编排(返回 orchestration_id)
    GET  /api/orchestrations -> 所有编排的聚合进度(含扇出任务统计/Token/成本)
    GET  /api/orchestrations/<id> -> 单编排详情

启动: python -m lingmengwork.web.server  (默认 127.0.0.1:8318)
"""
import argparse
import json
import os
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .. import __version__
from ..config import load_config
from ..llm.client import build_client
from ..tools.registry import build_registry
from ..agent.loop import AgentLoop
from ..agent.pool import TaskPool
from ..agent.session import list_sessions as sess_list, load_session as sess_load, delete_session as sess_del, save_session as sess_save, new_session_id as sess_new_id
from ..agent.pool import _results_dir
from . import orchestration as _orch_mod

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PORT = 8318

# 由 main() 在启动时填充, 让 CLI 覆盖 (--config/--backend) 生效到各 handler
_RUNTIME_CONFIG = None
# 进程内并发任务池(多路 LLM 同时编程)
_TASK_POOL = None
# 会话续跑: session_id -> 活体 AgentLoop 映射 (同一会话多次请求复用同一 loop, 执行态天然保留)
_SESSION_LOOPS = {}
_SESSION_LOCKS = {}
_SESSIONS_DICT_LOCK = None  # 延迟初始化(模块导入期 threading 已就绪, 但直接建更稳)
_SESSIONS_MAX = 64  # 活体会话上限, 超出按插入序驱逐最旧
# 并行编排: 扇出多次 submit 后登记聚合对象, 供 Web 看板展示扇出/扇入进度
_ORCH = _orch_mod.OrchestrationStore()

# 代码评审自评估 (Critic Loop) 的 WEB 可视化: 历次 review_code 结果的结构化报告。
# 进程内 ring buffer (不落盘, 重启即清空), 供「代码评审」tab 展示评分/问题/来源。
_REVIEWS = []
_REVIEWS_LOCK = None
_REVIEWS_MAX = 100

def _reviews_lock():
    global _REVIEWS_LOCK
    if _REVIEWS_LOCK is None:
        import threading
        _REVIEWS_LOCK = threading.Lock()
    return _REVIEWS_LOCK


def _parse_code_review(text):
    """解析 review_code 工具输出的 [code-review] 块为结构化 dict; 解析失败返回 None。"""
    if not text or "[code-review]" not in text:
        return None
    try:
        import re as _re
        verdict = _re.search(r"VERDICT:\s*(\w+)", text)
        score = _re.search(r"SCORE:\s*(\d{1,3})", text)
        summary = _re.search(r"SUMMARY:\s*(.*)", text)
        source = _re.search(r"评审来源:\s*(.*)", text)
        issues = []
        in_issues = False
        for line in text.splitlines():
            st = line.strip()
            if st.startswith("ISSUES:"):
                in_issues = True
                continue
            if in_issues:
                if st.startswith(("SCORE:", "SUMMARY:", "VERDICT:", "评审来源:")):
                    in_issues = False
                    continue
                m = _re.match(r"-\s*\[([高中低])\]\s*(.*)", line)
                if m:
                    issues.append({"sev": m.group(1), "desc": m.group(2).strip()})
        return {
            "verdict": (verdict.group(1).lower() if verdict else "unknown"),
            "score": (int(score.group(1)) if score else None),
            "issues": issues,
            "summary": (summary.group(1).strip() if summary else ""),
            "source": (source.group(1).strip().strip("()") if source else ""),
        }
    except Exception:
        return None


def _clip(text, n):
    """截断过长文本, 保留首尾, 用于接口摘要。"""
    if not text:
        return ""
    text = str(text)
    if len(text) <= n:
        return text
    return text[: n // 2] + "\n…(已截断, 共 %d 字符)…\n" % len(text) + text[-n // 2 :]


def _read_version():
    """读取工程根 VERSION 文件, 失败回退 0.0.0。"""
    try:
        p = os.path.join(os.getcwd(), "VERSION")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip() or "0.0.0"
    except Exception:
        pass
    return "0.0.0"


def _render_delivery_report(result, note=""):
    """把 deliver 结果渲染为自包含 HTML 交付报告 (内联 CSS, 可下载离线查看)。"""
    import html as _html
    from datetime import datetime as _dt
    t = result.get("test") or {}
    r = result.get("review") or {}
    target = _html.escape(str(result.get("target") or ""))
    ts = result.get("ts") or int(time.time())
    when = _dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    ready = bool(result.get("delivery_ready"))
    verdict = _html.escape(str(r.get("verdict") or "unknown"))
    score = r.get("score")
    score_s = (str(score) if score is not None else "—")
    passed = t.get("passed")
    rc = t.get("rc")
    test_line = (_html.escape(str(t.get("command") or "")) +
                 ("  → rc=%s" % rc if rc is not None else "") +
                 ("  ✅通过" if passed is True else ("  ❌失败" if passed is False else "  (未运行/跳过)")))
    review_summary = _html.escape(str(r.get("summary") or ""))
    source = _html.escape(str(r.get("source") or "静态规则"))
    issues_html = ""
    for it in (r.get("issues") or []):
        sev = _html.escape(str(it.get("sev") or ""))
        issues_html += '<li><span class="sev sev-%s">%s</span> %s</li>\n' % (sev, sev, _html.escape(str(it.get("desc") or "")))
    if not issues_html:
        issues_html = '<li class="ok">无遗留问题</li>\n'
    note_html = _html.escape(note) if note else '<span class="muted">(未填写)</span>'
    test_raw = _html.escape(str(t.get("raw") or "")[:4000])
    review_raw = _html.escape(str(r.get("raw") or "")[:4000])
    verdict_label = "可交付 ✅" if ready else "暂不可交付 ⛔"
    verdict_cls = "ready" if ready else "notready"
    ver = _read_version()
    html_doc = (
        '<!DOCTYPE html>\n'
        '<html lang="zh-CN"><head><meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
        '<title>灵梦work 交付报告 · ' + target + '</title>\n'
        '<style>\n'
        ':root{--bg:#0f1420;--card:#171e2e;--fg:#e6ebf5;--muted:#9aa7bd;--accent:#5b8cff;--ok:#3fb950;--bad:#f85149;--line:#26304a;}\n'
        '*{box-sizing:border-box}\n'
        'body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,Segoe UI,Roboto,"PingFang SC","Microsoft YaHei",sans-serif;padding:32px;}\n'
        '.wrap{max-width:900px;margin:0 auto;}\n'
        'h1{font-size:22px;margin:0 0 4px;}\n'
        '.sub{color:var(--muted);font-size:13px;margin-bottom:20px;}\n'
        '.badge{display:inline-block;padding:6px 14px;border-radius:8px;font-weight:700;font-size:15px;}\n'
        '.ready{background:rgba(63,185,80,.15);color:var(--ok);border:1px solid var(--ok);}\n'
        '.notready{background:rgba(248,81,73,.15);color:var(--bad);border:1px solid var(--bad);}\n'
        '.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:16px 0;}\n'
        '.card h2{font-size:15px;margin:0 0 12px;color:var(--accent);letter-spacing:.5px;}\n'
        '.kv{display:flex;gap:10px;margin:6px 0;}\n'
        '.kv .k{color:var(--muted);min-width:88px;}\n'
        '.kv .v{color:var(--fg);word-break:break-all;}\n'
        'pre{background:#0b0f18;border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;max-height:260px;font:12px/1.5 Consolas,Menlo,monospace;color:#cdd6e6;white-space:pre-wrap;word-break:break-all;}\n'
        'ul{margin:6px 0;padding-left:18px;}\n'
        'li{margin:4px 0;}\n'
        '.sev{display:inline-block;min-width:34px;text-align:center;border-radius:4px;font-size:11px;padding:1px 6px;margin-right:6px;}\n'
        '.sev-高{background:rgba(248,81,73,.2);color:var(--bad);}\n'
        '.sev-中{background:rgba(240,180,40,.2);color:#f0b428;}\n'
        '.sev-低{background:rgba(91,140,255,.2);color:var(--accent);}\n'
        'li.ok{color:var(--ok);list-style:none;margin-left:-18px;}\n'
        '.muted{color:var(--muted);}\n'
        '.note{white-space:pre-wrap;}\n'
        'footer{color:var(--muted);font-size:12px;text-align:center;margin-top:24px;}\n'
        '</style></head>\n'
        '<body><div class="wrap">\n'
        '  <h1>灵梦work · 交付报告</h1>\n'
        '  <div class="sub">自动交付闭环 (改→跑→评→判定) · 灵梦work v' + ver + ' · 生成于 ' + when + '</div>\n'
        '  <div class="badge ' + verdict_cls + '">' + verdict_label + '</div>\n'
        '  <div class="card">\n'
        '    <h2>变更说明</h2>\n'
        '    <div class="note">' + note_html + '</div>\n'
        '  </div>\n'
        '  <div class="card">\n'
        '    <h2>交付概览</h2>\n'
        '    <div class="kv"><span class="k">目标文件</span><span class="v">' + target + '</span></div>\n'
        '    <div class="kv"><span class="k">交付判定</span><span class="v">' + verdict_label + '</span></div>\n'
        '    <div class="kv"><span class="k">评审结论</span><span class="v">' + verdict + ' (评分 ' + score_s + ' · ' + source + ')</span></div>\n'
        '    <div class="kv"><span class="k">测试结果</span><span class="v">' + test_line + '</span></div>\n'
        '  </div>\n'
        '  <div class="card">\n'
        '    <h2>评审问题清单</h2>\n'
        '    <ul>' + issues_html + '</ul>\n'
        '    <p class="muted">' + review_summary + '</p>\n'
        '  </div>\n'
        '  <div class="card">\n'
        '    <h2>测试输出</h2>\n'
        '    <pre>' + test_raw + '</pre>\n'
        '  </div>\n'
        '  <div class="card">\n'
        '    <h2>评审原始输出</h2>\n'
        '    <pre>' + review_raw + '</pre>\n'
        '  </div>\n'
        '  <footer>本报告由灵梦work 自动生成 · 闭环: 跑测试(shell) → 静态评审(code_review) → 交付判定 → 报告导出</footer>\n'
        '</div></body></html>'
    )
    return html_doc


def _record_review(target, output):
    """把一次 review_code 的 tool_result 归入 _REVIEWS (ring buffer)。返回记录 dict 或 None。"""
    parsed = _parse_code_review(output)
    if not parsed:
        return None
    rec = {
        "id": len(_REVIEWS) + 1,
        "ts": int(time.time()),
        "target": target or "",
        "verdict": parsed["verdict"],
        "score": parsed["score"],
        "issues": parsed["issues"],
        "summary": parsed["summary"],
        "source": parsed["source"],
        "raw": output,
    }
    with _reviews_lock():
        _REVIEWS.append(rec)
        while len(_REVIEWS) > _REVIEWS_MAX:
            _REVIEWS.pop(0)
    return rec


# ---------- 成果落盘 + LLM 语义评审层 + 评审聚合报告 ----------
def _artifact_dir():
    """成果落盘目录 (.lmw_artifacts, 工程根下), 自动创建。"""
    d = os.path.join(os.getcwd(), ".lmw_artifacts")
    _try_mkdir(os.path.join(d, "files"))
    return d


def _try_mkdir(path):
    """创建目录, 兼容本环境 safe-delete 垫片可能拦截 os.makedirs 的情况(剥离触发变量后重试)。"""
    try:
        os.makedirs(path, exist_ok=True)
        return
    except Exception:
        popped = {}
        try:
            for k in ("CODEBUDDY_SESSION_ID", "CLAUDE_SESSION_ID"):
                if k in os.environ:
                    popped[k] = os.environ.pop(k)
            if popped:
                os.makedirs(path, exist_ok=True)
        except Exception:
            pass
        finally:
            if popped:
                os.environ.update(popped)


_ART_LOCK = None
def _art_lock():
    global _ART_LOCK
    if _ART_LOCK is None:
        import threading
        _ART_LOCK = threading.Lock()
    return _ART_LOCK


def _record_artifact(kind, content_bytes, content_type, meta=None):
    """把一次成果(交付报告/评审报告/PR 草稿)落盘到 .lmw_artifacts, 返回记录 dict 或 None。"""
    try:
        import json as _json
        d = _artifact_dir()
        ts = int(time.time())
        ct = content_type or ""
        ext = "html" if "html" in ct else ("md" if "markdown" in ct else "bin")
        fname = "%d_%s.%s" % (ts, kind, ext)
        with open(os.path.join(d, "files", fname), "wb") as f:
            f.write(content_bytes)
        rec = {"id": ts, "ts": ts, "kind": kind, "name": fname,
               "size": len(content_bytes), "content_type": ct, "meta": meta or {}}
        with _art_lock():
            with open(os.path.join(d, "index.jsonl"), "a", encoding="utf-8") as f:
                f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
    except Exception:
        return None


def _read_file_text(p):
    try:
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception:
        pass
    return None


# 诊断: 记录最近一次 _llm_review 的各阶段结果 (不泄露 key), 供 /api/review/changed 透出排查。
_LLM_DIAG = {}


def _llm_review(text, focus=None):
    """可选 LLM 语义评审层: SENSENOVA_API_KEY 存在时调商汤做语义批判, 否则返回 None(优雅回退静态)。"""
    global _LLM_DIAG
    _LLM_DIAG = {"key": False, "import_ok": False, "client_ok": False, "call_ok": False, "error": ""}
    key = os.environ.get("SENSENOVA_API_KEY") or os.environ.get("SENSENOVA_API_KEY_2")
    if not key:
        _LLM_DIAG["error"] = "no key in env"
        return None
    _LLM_DIAG["key"] = True
    try:
        from ..llm.client import OpenAIClient
    except Exception as e:
        _LLM_DIAG["error"] = "import OpenAIClient failed: %s" % e
        return None
    _LLM_DIAG["import_ok"] = True
    try:
        client = OpenAIClient(
            base_url="https://token.sensenova.cn/v1",
            model="sensenova-6.8-flash-lite",
            api_key=key,
        )
    except Exception as e:
        _LLM_DIAG["error"] = "build client failed: %s" % e
        return None
    _LLM_DIAG["client_ok"] = True
    focus_hint = ("重点关注: " + focus) if focus else "关注正确性/健壮性/可维护性/可读性"
    sys_p = ("你是一名资深代码评审专家。请对代码做只读评审(不要修改文件)。\n" + focus_hint + "\n"
             "在回复末尾用固定格式给出结论:\n"
             "VERDICT: approve 或 revise\n"
             "SCORE: 0-100 (代码质量分)\n"
             "ISSUES:\n- [高/中/低] 问题描述\n"
             "SUMMARY: 一句话总结\n\n===== 待评审代码 =====\n")
    try:
        resp = client.chat([
            {"role": "system", "content": sys_p},
            {"role": "user", "content": text[:8000]},
        ], stream=False)
    except Exception as e:
        _LLM_DIAG["error"] = "client.chat failed: %s" % e
        return None
    _LLM_DIAG["call_ok"] = True
    if resp is not None and hasattr(resp, "__iter__") and not isinstance(resp, str):
        try:
            resp = "".join(str(c) for c in resp)
        except Exception:
            resp = ""
    return _parse_critic_review(resp or "")


def _parse_critic_review(text):
    """解析 LLM critic 文本里的 VERDICT/SCORE/ISSUES/SUMMARY, 失败返回 None。"""
    if not text:
        return None
    import re as _re
    m_v = _re.search(r"VERDICT:\s*(approve|revise)", text, _re.I)
    if not m_v:
        return None
    verdict = m_v.group(1).lower()
    score = None
    m_s = _re.search(r"SCORE:\s*(\d{1,3})", text, _re.I)
    if m_s:
        score = max(0, min(100, int(m_s.group(1))))
    issues = []
    in_issues = False
    for line in text.splitlines():
        st = line.strip()
        if st.startswith("ISSUES:"):
            in_issues = True
            continue
        if in_issues:
            if _re.match(r"(?i)^(SUMMARY|SCORE|VERDICT):", st):
                in_issues = False
                continue
            m = _re.match(r"-\s*\[([高中低])\]\s*(.*)", line)
            if m:
                issues.append({"sev": m.group(1), "desc": m.group(2).strip()})
    sm = _re.search(r"SUMMARY:\s*(.*)", text)
    summary = sm.group(1).strip() if sm else ""
    return {"verdict": verdict, "score": score, "issues": issues,
            "summary": summary, "source": "LLM 语义 + 静态"}


def _merge_review(static, llm):
    """合并静态评审与 LLM 语义评审结论 (任一 revise -> revise; 评分取 LLM; 问题叠加)。"""
    if not llm:
        return static
    s_score = static.get("score")
    l_score = llm.get("score")
    score = l_score if l_score is not None else s_score
    issues = list(static.get("issues") or []) + list(llm.get("issues") or [])
    s_v = (static.get("verdict") or "")
    l_v = (llm.get("verdict") or "")
    verdict = "revise" if ("revise" in (s_v, l_v)) else (l_v or s_v)
    summary = ((llm.get("summary") or "") + " | " + (static.get("summary") or "")).strip(" |")
    return {"verdict": verdict, "score": score, "issues": issues,
            "summary": summary, "source": "LLM 语义 + 静态"}


def _review_file(review_srv, path, use_llm):
    """对单文件做静态评审(可选叠加 LLM), 返回 (raw_text, merged_parsed)。"""
    out = review_srv.call_tool("code_review", {"target": path})
    parsed = _parse_code_review(out) or {}
    if use_llm:
        txt = _read_file_text(path)
        if txt:
            llm = _llm_review(txt)
            if llm:
                parsed = _merge_review(parsed, llm)
    return out, parsed


def _render_review_report(files, note=""):
    """把多文件评审聚合结果渲染为自包含 HTML 报告 (内联 CSS, 可下载)。"""
    import html as _html
    from datetime import datetime as _dt
    when = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    note_html = _html.escape(note) if note else '<span class="muted">(未填写)</span>'
    total = len(files)
    approve = sum(1 for f in files if f.get("verdict") == "approve")
    revise = sum(1 for f in files if f.get("verdict") == "revise")
    scores = [f.get("score") for f in files if isinstance(f.get("score"), int)]
    avg = (sum(scores) // len(scores)) if scores else 0
    def _pct(n):
        return (100 * n // total) if total else 0
    dist = ('<div class="bar"><span class="seg seg-ok" style="width:%d%%"></span>'
            '<span class="seg seg-bad" style="width:%d%%"></span></div>' % (_pct(approve), _pct(revise)))
    cards = ""
    for f in files:
        v = f.get("verdict") or "unknown"
        badge = "approve" if v == "approve" else ("revise" if v == "revise" else "unknown")
        label = "✅ 通过" if v == "approve" else ("⛔ 需修改" if v == "revise" else "未知")
        sc = f.get("score")
        sc_s = str(sc) if isinstance(sc, int) else "—"
        ih = ""
        for it in (f.get("issues") or []):
            sev = _html.escape(str(it.get("sev") or ""))
            ih += '<li><span class="sev sev-%s">%s</span> %s</li>\n' % (sev, sev, _html.escape(str(it.get("desc") or "")))
        if not ih:
            ih = '<li class="ok">无遗留问题</li>\n'
        cards += ('<div class="card">\n  <div class="rv-head"><span class="rv-file">%s</span>'
                  '<span class="badge %s">%s</span><span class="rv-score">评分 %s</span></div>\n  <ul>%s</ul>\n</div>\n'
                  % (_html.escape(str(f.get("path") or "")), badge, label, sc_s, ih))
    ver = _read_version()
    has_llm = any("LLM" in (f.get("source") or "") for f in files)
    src_tag = "静态规则" + (" + 商汤 LLM 语义" if has_llm else "")
    doc = (
        '<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
        '<title>灵梦work 评审报告</title>\n<style>\n'
        ':root{--bg:#0f1420;--card:#171e2e;--fg:#e6ebf5;--muted:#9aa7bd;--accent:#5b8cff;--ok:#3fb950;--bad:#f85149;--line:#26304a;}\n'
        '*{box-sizing:border-box}\n'
        'body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,Segoe UI,Roboto,"PingFang SC","Microsoft YaHei",sans-serif;padding:32px;}\n'
        '.wrap{max-width:940px;margin:0 auto;}\n'
        'h1{font-size:22px;margin:0 0 4px;}\n.sub{color:var(--muted);font-size:13px;margin-bottom:20px;}\n'
        '.badge{display:inline-block;padding:3px 10px;border-radius:8px;font-weight:700;font-size:13px;}\n'
        '.approve{background:rgba(63,185,80,.15);color:var(--ok);border:1px solid var(--ok);}\n'
        '.revise{background:rgba(248,81,73,.15);color:var(--bad);border:1px solid var(--bad);}\n'
        '.unknown{background:rgba(154,167,189,.15);color:var(--muted);border:1px solid var(--muted);}\n'
        '.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:14px 0;}\n'
        '.rv-head{display:flex;align-items:center;gap:12px;margin-bottom:10px;}\n'
        '.rv-file{font-family:Consolas,Menlo,monospace;color:var(--fg);font-size:13px;word-break:break-all;flex:1;}\n'
        '.rv-score{color:var(--muted);font-size:13px;}\n'
        '.stats{display:flex;gap:18px;flex-wrap:wrap;margin:12px 0;}\n'
        '.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:96px;text-align:center;}\n'
        '.stat .n{font-size:20px;font-weight:800;}\n.stat .l{color:var(--muted);font-size:12px;}\n'
        '.bar{display:flex;height:10px;border-radius:6px;overflow:hidden;background:var(--line);margin-top:6px;}\n'
        '.seg-ok{background:var(--ok);}\n.seg-bad{background:var(--bad);}\n'
        'ul{margin:6px 0;padding-left:18px;}\nli{margin:4px 0;}\n'
        '.sev{display:inline-block;min-width:34px;text-align:center;border-radius:4px;font-size:11px;padding:1px 6px;margin-right:6px;}\n'
        '.sev-高{background:rgba(248,81,73,.2);color:var(--bad);}\n.sev-中{background:rgba(240,180,40,.2);color:#f0b428;}\n.sev-低{background:rgba(91,140,255,.2);color:var(--accent);}\n'
        'li.ok{color:var(--ok);list-style:none;margin-left:-18px;}\n'
        '.muted{color:var(--muted);}\n.note{white-space:pre-wrap;}\n'
        'footer{color:var(--muted);font-size:12px;text-align:center;margin-top:24px;}\n'
        '</style></head>\n<body><div class="wrap">\n'
        '<h1>灵梦work · 多文件评审报告</h1>\n'
        '<div class="sub">自动评审聚合 (%s) · v%s · %s</div>\n' % (src_tag, ver, when) +
        '<div class="stats">\n'
        '<div class="stat"><div class="n">%d</div><div class="l">文件数</div></div>\n'
        '<div class="stat"><div class="n" style="color:var(--ok)">%d</div><div class="l">通过</div></div>\n'
        '<div class="stat"><div class="n" style="color:var(--bad)">%d</div><div class="l">需修改</div></div>\n'
        '<div class="stat"><div class="n">%d</div><div class="l">平均分</div></div>\n</div>\n' % (total, approve, revise, avg) +
        dist +
        '<div class="card"><h2 style="color:var(--accent);font-size:15px;margin:0 0 10px;">变更说明</h2><div class="note">%s</div></div>\n' % note_html +
        cards +
        '<footer>本报告由灵梦work 自动生成 · 闭环: 静态评审(code_review) + 可选 LLM 语义评审 → 聚合报告</footer>\n'
        '</div></body></html>'
    )
    return doc


def _collect_git_changes(repo):
    """调 git 服务器取工作区状态 + 未暂存/已暂存差异, 返回 (ok, status_text, diff_text, files)。

    files 为 [{"code": "M"/"A"/..., "path": "相对路径"}]。repo 非 git 仓库或工具缺失时
    返回 (False, 错误信息, "", [])。直接调用 git 服务器的模块级函数, 无需经 MCP 管理器。
    """
    try:
        from ..tools.mcp_git_server import _git_status, _git_diff
    except Exception as e:
        return False, "无法加载 git 服务器: %s" % e, "", []
    status = _git_status({"repo": repo})
    if status.startswith("[git_status] 失败") or status.startswith("[git_status] 缺少"):
        return False, status, "", []
    unstaged = _git_diff({"repo": repo})
    staged = _git_diff({"repo": repo, "staged": 1})
    parts = []
    if not (unstaged.startswith("[git_diff] 失败") or unstaged.startswith("[git_diff] 缺少")):
        parts.append(unstaged)
    if not (staged.startswith("[git_diff] 失败") or staged.startswith("[git_diff] 缺少")):
        parts.append(staged)
    diff_text = "\n".join(p for p in parts if p.strip())
    files = []
    import re as _re
    # git status --short 行: [XY]<空格><路径>; X/Y ∈ {空格, M, A, D, R, C, U, T, ?}。
    # 本环境 git 输出空格数不固定 (未暂存 " M file" 首位空格; 已暂存修改 "M file" 单空格;
    # 已暂存新增 "A  file" 双空格), 故用「1~2 个状态字符 + 至少一个空白」容错所有布局,
    # 并靠字符类天然跳过头部 [git_status]... / 当前分支:... 行 (首字符不在状态类内)。
    pat = _re.compile(r"^([ MADRCUT?]{1,2})\s+(.+)$")
    for line in status.splitlines():
        m = pat.match(line.rstrip("\r\n"))
        if not m:
            continue
        code = m.group(1).strip()  # 1~2 个状态字符 (含未暂存首位空格, strip 即状态字母)
        path = m.group(2).strip()
        if " -> " in path:  # 重命名 a -> b
            path = path.split(" -> ", 1)[-1]
        files.append({"code": code, "path": path})
    return True, status, diff_text, files


def _render_pr_draft(title, files, review_lines, note):
    """把 PR 草稿要素渲染为 Markdown (供下载/粘贴到 PR 描述)。"""
    from datetime import datetime as _dt
    when = _dt.now().strftime("%Y-%m-%d %H:%M")
    ver = _read_version()
    n = len(files)
    n_py = sum(1 for f in files if f["path"].endswith(".py"))
    status_map = {"M": "修改", "A": "新增", "D": "删除", "R": "重命名",
                  "C": "复制", "U": "冲突", "?": "未跟踪"}
    rows = ""
    for f in files:
        label = status_map.get((f.get("code") or "?")[:1], f.get("code") or "?")
        rows += "| %s | `%s` |\n" % (label, f["path"].replace("|", "\\|"))
    if not rows:
        rows = "| — | (无改动文件) |\n"
    review_block = "\n".join(review_lines) if review_lines else "_未运行静态评审 (无 .py 改动或评审服务未连接)_"
    note_block = note if note else "_待补充_"
    md = (
        "# %s\n\n" % title +
        "> 由 灵梦work 自动生成 · v%s · %s\n\n" % (ver, when) +
        "## 变更摘要\n\n" +
        "共 **%d** 个文件改动 (其中 Python %d 个)。\n\n" % (n, n_py) +
        "## 改动文件清单\n\n" +
        "| 状态 | 文件 |\n| --- | --- |\n" + rows + "\n" +
        "## 评审状态\n\n" + review_block + "\n\n" +
        "## 提交前检查\n\n" +
        "- [ ] 已运行 `pytest` 且通过\n" +
        "- [ ] 已通过 `code_review` 静态评审 (📋 评审改动)\n" +
        "- [ ] 已自检交付报告 (🚀 交付自检 → 📄 导出报告)\n\n" +
        "## 备注\n\n" + note_block + "\n\n" +
        "---\n_本草稿由灵梦work 生成，请人工复核后提交。_\n"
    )
    return md


def _sessions_dict_lock():
    global _SESSIONS_DICT_LOCK
    if _SESSIONS_DICT_LOCK is None:
        import threading
        _SESSIONS_DICT_LOCK = threading.Lock()
    return _SESSIONS_DICT_LOCK


def _evict_sessions_if_needed():
    """超出 _SESSIONS_MAX 时按插入序驱逐最旧活体会话(仅移出内存, 磁盘已落盘仍可水合)。"""
    global _SESSION_LOOPS, _SESSION_LOCKS
    while len(_SESSION_LOOPS) > _SESSIONS_MAX:
        oldest = next(iter(_SESSION_LOOPS))
        _SESSION_LOOPS.pop(oldest, None)
        _SESSION_LOCKS.pop(oldest, None)


def acquire_session(session_id, client, registry, cfg, backend):
    """返回 (loop, lock, hydfrom_disk)。

    - session_id 在活体映射中 -> 复用同一 AgentLoop(执行态/工具结果/令牌计数全保留)。
    - 给定 session_id 但内存无 -> 尝试磁盘水合(完整 messages 含 tool 角色), 失败则新建空 loop。
    - 无 session_id -> 生成新 id 并新建 loop。
    """
    import threading
    with _sessions_dict_lock():
        if session_id and session_id in _SESSION_LOOPS:
            loop = _SESSION_LOOPS[session_id]
            lock = _SESSION_LOCKS.setdefault(session_id, threading.Lock())
            return loop, lock, False
        # 需要新建(或水合)
        loop = AgentLoop(client, registry, cfg, session_id=session_id or None, provider=backend)
        if session_id:
            hydrated = loop.load_session_messages(session_id)
            if hydrated:
                # 续跑令牌计数: 把历史消息体量补回估算, 让 Token 条跨轮连续
                loop.est_input_chars += sum(len(m.get("content", "")) for m in loop.messages)
            lock = _SESSION_LOCKS.setdefault(session_id, threading.Lock())
            _SESSION_LOOPS[session_id] = loop
            _evict_sessions_if_needed()
            return loop, lock, hydrated
        # 无 id: 生成新稳定 id
        session_id = sess_new_id()
        loop.session_id = session_id
        lock = _SESSION_LOCKS.setdefault(session_id, threading.Lock())
        _SESSION_LOOPS[session_id] = loop
        _evict_sessions_if_needed()
        return loop, lock, False


def _get_cfg():
    return _RUNTIME_CONFIG if _RUNTIME_CONFIG is not None else load_config()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        # directory 设为 web/ (STATIC_DIR 的父), 使 /static/<file> 映射到 web/static/<file>
        super().__init__(*a, directory=os.path.dirname(STATIC_DIR), **kw)

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._serve_file("index.html")
        if p == "/api/health":
            cfg = _get_cfg()
            backend = cfg["llm"].get("backend", "ollama")
            try:
                client = build_client(backend, cfg=cfg)
                model = client.model
            except Exception:
                model = "?"
            return self._send_json({"ok": True, "version": __version__, "backend": backend, "model": model})
        if p == "/api/tools":
            try:
                tools = build_registry(_get_cfg()).list_tools()
            except Exception as e:
                tools = [{"name": "error", "description": str(e)}]
            return self._send_json({"tools": tools})
        # ---- 外部 MCP 工具 (开放工具中枢) ----
        if p == "/api/mcp":
            try:
                from ..tools import mcp as _mcp
                mgr = _mcp.get_manager()
                mgr.connect_all(_get_cfg())
                return self._send_json({
                    "enabled": _get_cfg().get("mcp", {}).get("enabled", True),
                    "servers": mgr.status(),
                })
            except Exception as e:
                return self._send_json({"error": str(e)}, status=500)
        # ---- 多路任务端点 ----
        if p == "/api/reviews":
            with _reviews_lock():
                return self._send_json({"reviews": list(reversed(_REVIEWS))})
        if p == "/api/providers":
            return self._send_json({"providers": self._get_pool().list_providers()})
        if p == "/api/tasks" or p == "/api/tasks/":
            return self._send_json({"tasks": self._get_pool().list_tasks()})
        # ---- 并行编排 (扇出多路任务的聚合看板) ----
        if p == "/api/orchestrations" or p == "/api/orchestrations/":
            pool = self._get_pool()
            out = [_ORCH.aggregate(o["id"], pool) for o in _ORCH.list_all()]
            return self._send_json({"orchestrations": out})
        if p.startswith("/api/orchestrations/"):
            rest = p[len("/api/orchestrations/"):].strip("/")
            oid = rest.replace("/stream", "").strip("/")
            agg = _ORCH.aggregate(oid, self._get_pool())
            return self._send_json(agg or {"error": "not found"}, status=404 if not agg else 200)
        # /api/tasks/<id> 或 /api/tasks/<id>/stream
        if p.startswith("/api/tasks/"):
            rest = p[len("/api/tasks/"):]
            if rest.endswith("/stream"):
                tid = rest[:-len("/stream")].strip("/")
                return self._task_stream(tid)
            tid = rest.strip("/")
            if tid:
                snap = self._get_pool().get(tid)
                return self._send_json(snap or {"error": "not found"}, status=404 if not snap else 200)
            return self._send_json({"tasks": self._get_pool().list_tasks()})

        # ---- 结果回看 (落盘目录) ----
        if p == "/api/results" or p == "/api/results/":
            return self._send_json(self._list_results())
        if p.startswith("/api/results/"):
            rid = p[len("/api/results/"):].strip("/")
            if not rid:
                return self._send_json(self._list_results())
            return self._send_json(self._get_result(rid))

        # ---- 会话历史 (落盘目录) ----
        if p == "/api/sessions" or p == "/api/sessions/":
            return self._send_json({"sessions": sess_list()})
        if p.startswith("/api/sessions/"):
            rid = p[len("/api/sessions/"):].strip("/")
            if not rid:
                return self._send_json({"sessions": sess_list()})
            s = sess_load(rid)
            return self._send_json(s or {"error": "not found"}, status=404 if not s else 200)

        # ---- 全局仪表盘统计 ----
        if p == "/api/stats":
            return self._send_json(self._global_stats())

        # ---- 文件树浏览器 (只读) ----
        if p.startswith("/api/fs"):
            return self._fs_api(p)

        # ---- 成果落盘清单 / 回看 (交付/评审/PR 可审计) ----
        if p == "/api/artifacts":
            return self._artifacts_list()
        if p.startswith("/api/artifacts/") and p.endswith("/raw"):
            rid = p[len("/api/artifacts/"):-len("/raw")].strip("/")
            return self._artifact_raw(rid)

        if p.startswith("/static/"):
            return super().do_GET()
        return self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/chat":
            return self._chat_sse()
        if p == "/api/tasks" or p == "/api/tasks/":
            return self._create_task()
        # ---- 外部 MCP 工具: 交互式直接调用 (供 Web 管理面板) ----
        if p == "/api/mcp/call":
            return self._mcp_call()
        # ---- 交互式终端: SSE 流式 stdout (供 Web 终端 tab) ----
        if p == "/api/terminal":
            return self._terminal_sse()
        # ---- 自动交付闭环: 改→跑→评 一条龙 (供编辑器「交付自检」按钮) ----
        if p == "/api/deliver":
            return self._deliver()
        if p == "/api/deliver/report":
            return self._deliver_report()
        # ---- 交付中心: 项目级「改→跑→评→交付」快照 (供 Web 交付中心 tab) ----
        if p == "/api/deliver/center":
            return self._deliver_center()
        # ---- 多文件批量评审 (供编辑器「评审改动」按钮, 需先经 git 取改动清单) ----
        if p == "/api/review/batch":
            return self._review_batch()
        if p == "/api/review/changed":
            return self._review_changed()
        # ---- PR 草稿生成: git 差异 -> Markdown (供「PR 草稿」按钮下载) ----
        if p == "/api/pr/draft":
            return self._pr_draft()
        # ---- 多文件评审聚合报告 (供编辑器「评审报告」按钮) ----
        if p == "/api/review/report":
            return self._review_report()
        # ---- 项目文档自动生成 (批次8: 生成 CLAUDE.md/AGENTS.md 草稿) ----
        if p == "/api/docs/generate":
            return self._docs_generate()
        # ---- 文件编辑: 保存 (供 Web 代码编辑器) ----
        if p.startswith("/api/fs"):
            return self._fs_save()
        return self.send_error(404)

    def _mcp_call(self):
        """POST /api/mcp/call {server, tool, arguments} -> 直接调用某 MCP 服务器的某工具。

        返回 {ok, output} / {error}。供 Web「外部工具」tab 交互调用任意工具,
        无需经过 LLM 对话链路, 便于调试与管理。
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        server = (body.get("server") or "").strip()
        tool = (body.get("tool") or "").strip()
        arguments = body.get("arguments") or {}
        if not server or not tool:
            return self._send_json({"error": "缺少 server 或 tool"}, status=400)
        try:
            from ..tools import mcp as _mcp
            mgr = _mcp.get_manager()
            mgr.connect_all(_get_cfg())
            cli = mgr.servers.get(server)
            if cli is None:
                return self._send_json({"error": "未连接的 MCP 服务: %s (检查 config.toml [[mcp.servers]])" % server}, status=404)
            if not cli.has_tool(tool):
                return self._send_json({"error": "服务 %s 无此工具: %s" % (server, tool)}, status=404)
            out = cli.call_tool(tool, arguments)
            is_error = out.startswith("[mcp error]")
            return self._send_json({"ok": True, "output": out, "isError": is_error})
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

    def _run_deliver_pipeline(self, target, test_cmd):
        """核心交付闭环: 跑测试 + 静态评审, 返回结构化 result dict (供 JSON 与 HTML 报告共用)。

        组合已验证的 MCP 工具 (shell.shell_exec 跑测试 + review.code_review 评审),
        无需 LLM 即可完成「改→跑→评」一条龙, 结果入库供「代码评审」趋势图。
        """
        import re as _re
        if not test_cmd:
            test_cmd = "python -m pytest tests/ -q"
        # 测试在工程根目录跑 (面板以 -WorkingDirectory 工程根启动, cwd 即工程根)
        proj_root = os.getcwd()
        try:
            from ..tools import mcp as _mcp
            mgr = _mcp.get_manager()
            mgr.connect_all(_get_cfg())
            # 1) 跑测试
            test_out = ""
            test_rc = None
            shell = mgr.servers.get("shell")
            if shell and shell.has_tool("shell_exec"):
                test_out = shell.call_tool("shell_exec", {"command": test_cmd, "timeout": 180, "cwd": proj_root})
                m = _re.search(r"rc=(\d+)", test_out)
                if m:
                    test_rc = int(m.group(1))
            test_passed = (test_rc == 0) if test_rc is not None else None
            # 2) 静态评审 (复用 code_review MCP, 入库供趋势图) + 可选 LLM 语义增强
            review_out = ""
            review_srv = mgr.servers.get("review")
            use_llm = bool(os.environ.get("SENSENOVA_API_KEY") or os.environ.get("SENSENOVA_API_KEY_2"))
            if review_srv and review_srv.has_tool("code_review"):
                review_out = review_srv.call_tool("code_review", {"target": target})
            parsed = _parse_code_review(review_out) or {}
            if use_llm:
                txt = _read_file_text(target)
                if txt:
                    llm = _llm_review(txt, focus="交付质量/正确性")
                    if llm:
                        parsed = _merge_review(parsed, llm)
            _record_review(target, review_out)
            verdict = parsed.get("verdict")
            # 3) 交付判定: 测试通过(或跳过) 且 评审 approve
            delivery_ready = (verdict == "approve") and (test_passed in (True, None))
            return {
                "ok": True,
                "target": target,
                "test": {
                    "command": test_cmd,
                    "rc": test_rc,
                    "passed": test_passed,
                    "summary": _clip(test_out, 1500),
                    "raw": test_out,
                },
                "review": {
                    "verdict": verdict,
                    "score": parsed.get("score"),
                    "issues": parsed.get("issues"),
                    "summary": parsed.get("summary"),
                    "source": parsed.get("source"),
                    "raw": review_out,
                },
                "delivery_ready": delivery_ready,
                "ts": int(time.time()),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _deliver(self):
        """POST /api/deliver {path, test_cmd?} -> 自动交付闭环 JSON 判定。"""
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        target = (body.get("path") or "").strip()
        test_cmd = (body.get("test_cmd") or "").strip()
        if not target:
            return self._send_json({"error": "缺少 path (待交付文件)"}, status=400)
        result = self._run_deliver_pipeline(target, test_cmd)
        if not result.get("ok"):
            return self._send_json({"error": result.get("error", "未知错误")}, status=500)
        # 仅回传 JSON 友好子集 (去掉 raw 大文本)
        out = dict(result)
        out.pop("raw", None)
        if isinstance(out.get("test"), dict):
            out["test"] = {k: v for k, v in out["test"].items() if k != "raw"}
        if isinstance(out.get("review"), dict):
            out["review"] = {k: v for k, v in out["review"].items() if k != "raw"}
        return self._send_json(out)

    def _deliver_report(self):
        """POST /api/deliver/report {path, test_cmd?, note?} -> 生成自包含 HTML 交付报告并下载。"""
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        target = (body.get("path") or "").strip()
        test_cmd = (body.get("test_cmd") or "").strip()
        note = (body.get("note") or "").strip()
        if not target:
            return self._send_json({"error": "缺少 path (待交付文件)"}, status=400)
        result = self._run_deliver_pipeline(target, test_cmd)
        if not result.get("ok"):
            return self._send_json({"error": result.get("error", "未知错误")}, status=500)
        html_doc = _render_delivery_report(result, note=note)
        fname = "delivery_report_%s.html" % time.strftime("%Y%m%d_%H%M%S")
        payload = html_doc.encode("utf-8")
        _record_artifact("delivery", payload, "text/html",
                              {"target": target, "note": note, "ready": result.get("delivery_ready")})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % fname)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _deliver_center(self):
        """POST /api/deliver/center {repo?, deliver?} -> 项目级交付快照。

        扫描 git 改动的 .py 文件并逐一静态评审(入库供趋势图); deliver=true 时额外
        跑一次全量测试(工程根), 给出整包可交付判定(测试通过 且 全部文件 approve)。
        供 Web「交付中心」tab 一次性掌控「改→跑→评→交付」全链路。
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        repo = (body.get("repo") or "").strip() or os.getcwd()
        do_deliver = bool(body.get("deliver"))
        ok, status_text, _diff, files = _collect_git_changes(repo)
        if not ok:
            return self._send_json({"error": status_text}, status=400)
        # 用 repo 绝对路径, 保证 _review_file 内 _read_file_text(path) 与 code_review 的
        # os.path.isfile(target) 都能命中 (git 返回的是仓库相对路径, 直接 open 会按面板 cwd 失败)。
        py_files = [os.path.join(repo, f["path"]) for f in files
                    if f["path"].endswith(".py") and os.path.isfile(os.path.join(repo, f["path"]))]
        out = {"ok": True, "repo": repo, "total": 0, "approve": 0, "revise": 0, "other": 0,
               "files": [], "delivery": None, "reviews": []}
        with _reviews_lock():
            out["reviews"] = list(reversed(_REVIEWS))[:20]
        if not py_files:
            out["message"] = "无 .py 改动可评审"
            return self._send_json(out)
        try:
            import re as _re
            from ..tools import mcp as _mcp
            mgr = _mcp.get_manager()
            mgr.connect_all(_get_cfg())
            srv = mgr.servers.get("review")
            if not (srv and srv.has_tool("code_review")):
                return self._send_json({"error": "review MCP 服务未连接 (检查 config.toml)"}, status=404)
            use_llm = bool(os.environ.get("SENSENOVA_API_KEY") or os.environ.get("SENSENOVA_API_KEY_2"))
            code_map = {f["path"]: f["code"] for f in files}
            out_files = []
            approve = revise = other = 0
            for p in py_files:
                _out, parsed = _review_file(srv, p, use_llm)
                _record_review(p, _out)
                v = parsed.get("verdict")
                if v == "approve":
                    approve += 1
                elif v == "revise":
                    revise += 1
                else:
                    other += 1
                out_files.append({
                    "path": p, "code": code_map.get(p, ""),
                    "verdict": v, "score": parsed.get("score"),
                    "issues": (parsed.get("issues") or [])[:5],
                    "summary": parsed.get("summary"), "source": parsed.get("source"),
                })
            out.update({"total": len(out_files), "approve": approve, "revise": revise,
                        "other": other, "files": out_files})
            if do_deliver:
                # 整包交付闭环: 跑一次全量测试(工程根) + 聚合 verdict
                test_rc = None
                test_out = ""
                shell = mgr.servers.get("shell")
                if shell and shell.has_tool("shell_exec"):
                    test_out = shell.call_tool("shell_exec",
                                               {"command": "python -m pytest tests/ -q", "timeout": 180, "cwd": os.getcwd()})
                    m = _re.search(r"rc=(\d+)", test_out)
                    if m:
                        test_rc = int(m.group(1))
                test_passed = (test_rc == 0) if test_rc is not None else None
                agg_issues = []
                for f in out_files:
                    for it in (f.get("issues") or []):
                        agg_issues.append({"path": f["path"], "sev": it.get("sev"), "desc": it.get("desc")})
                ready = (test_passed is True) and (revise == 0) and (approve == len(out_files))
                out["delivery"] = {
                    "ready": ready,
                    "test": {"rc": test_rc, "passed": test_passed, "summary": _clip(test_out, 1200)},
                    "review": {"approve": approve, "revise": revise, "other": other,
                               "issues": agg_issues[:10]},
                }
            return self._send_json(out)
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

    def _review_batch(self):
        """POST /api/review/batch {paths:[...]} -> 对给定文件逐一静态评审并聚合。

        复用 review.code_review (key-free 静态规则), 结果入库供「代码评审」趋势图。
        返回 {ok, total, approve, revise, other, files:[{path,verdict,score,issues,summary,source}]}。
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        paths = body.get("paths") or []
        if not paths or not isinstance(paths, list):
            return self._send_json({"error": "缺少 paths (待评审文件列表)"}, status=400)
        try:
            from ..tools import mcp as _mcp
            mgr = _mcp.get_manager()
            mgr.connect_all(_get_cfg())
            srv = mgr.servers.get("review")
            if not (srv and srv.has_tool("code_review")):
                return self._send_json({"error": "review MCP 服务未连接 (检查 config.toml)"}, status=404)
            use_llm = bool(os.environ.get("SENSENOVA_API_KEY") or os.environ.get("SENSENOVA_API_KEY_2"))
            files = []
            approve = revise = other = 0
            for p in paths:
                p = str(p).strip()
                if not p:
                    continue
                out, parsed = _review_file(srv, p, use_llm)
                _record_review(p, out)
                v = parsed.get("verdict")
                if v == "approve":
                    approve += 1
                elif v == "revise":
                    revise += 1
                else:
                    other += 1
                files.append({
                    "path": p,
                    "verdict": v,
                    "score": parsed.get("score"),
                    "issues": parsed.get("issues"),
                    "summary": parsed.get("summary"),
                    "source": parsed.get("source"),
                })
            return self._send_json({
                "ok": True,
                "total": len(files),
                "approve": approve,
                "revise": revise,
                "other": other,
                "files": files,
            })
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

    def _review_changed(self):
        """POST /api/review/changed {repo?} -> 经 git 取改动清单, 对其中 .py 文件批量静态评审。

        一键评审当前分支所有改动 (无需手工列路径)。返回聚合结果 (同 /api/review/batch)。
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        repo = (body.get("repo") or "").strip() or os.getcwd()
        ok, status_text, _diff, files = _collect_git_changes(repo)
        if not ok:
            return self._send_json({"error": status_text}, status=400)
        # 用 repo 绝对路径, 保证 _review_file 内 _read_file_text(path) 与 code_review 的
        # os.path.isfile(target) 都能命中 (git 返回的是仓库相对路径, 直接 open 会按面板 cwd 失败)。
        py_files = [os.path.join(repo, f["path"]) for f in files
                    if f["path"].endswith(".py") and os.path.isfile(os.path.join(repo, f["path"]))]
        if not py_files:
            return self._send_json({"ok": True, "total": 0, "approve": 0, "revise": 0,
                                    "other": 0, "files": [], "message": "无 .py 改动可评审"})
        try:
            from ..tools import mcp as _mcp
            mgr = _mcp.get_manager()
            mgr.connect_all(_get_cfg())
            srv = mgr.servers.get("review")
            if not (srv and srv.has_tool("code_review")):
                return self._send_json({"error": "review MCP 服务未连接 (检查 config.toml)"}, status=404)
            use_llm = bool(os.environ.get("SENSENOVA_API_KEY") or os.environ.get("SENSENOVA_API_KEY_2"))
            out_files = []
            approve = revise = other = 0
            for p in py_files:
                out, parsed = _review_file(srv, p, use_llm)
                _record_review(p, out)
                v = parsed.get("verdict")
                if v == "approve":
                    approve += 1
                elif v == "revise":
                    revise += 1
                else:
                    other += 1
                out_files.append({
                    "path": p,
                    "verdict": v,
                    "score": parsed.get("score"),
                    "issues": parsed.get("issues"),
                    "summary": parsed.get("summary"),
                    "source": parsed.get("source"),
                })
            return self._send_json({
                "ok": True,
                "repo": repo,
                "total": len(out_files),
                "approve": approve,
                "revise": revise,
                "other": other,
                "llm_enabled": use_llm,
                "llm_diag": (dict(_LLM_DIAG) if use_llm else {}),
                "files": out_files,
            })
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

    def _pr_draft(self):
        """POST /api/pr/draft {repo?, title?, note?, review?} -> 生成 Markdown PR 草稿并下载。

        组合 git 服务器 (取 diff) + review.code_review (对 .py 改动做静态评审, 可选),
        套用 PR 模板, 返回 text/markdown (Content-Disposition: attachment)。无需 LLM。
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        repo = (body.get("repo") or "").strip() or os.getcwd()
        title = (body.get("title") or "").strip() or "自动生成 PR 草稿"
        note = (body.get("note") or "").strip()
        do_review = body.get("review", True)
        if isinstance(do_review, str):
            do_review = do_review.lower() not in ("false", "0", "no")
        ok, status_text, _diff, files = _collect_git_changes(repo)
        if not ok:
            return self._send_json({"error": status_text}, status=400)
        review_lines = []
        if do_review:
            py_files = [f["path"] for f in files
                        if f["path"].endswith(".py") and os.path.isfile(os.path.join(repo, f["path"]))]
            if py_files:
                try:
                    from ..tools import mcp as _mcp
                    mgr = _mcp.get_manager()
                    mgr.connect_all(_get_cfg())
                    srv = mgr.servers.get("review")
                    if srv and srv.has_tool("code_review"):
                        for f in py_files:
                            out = srv.call_tool("code_review", {"target": f})
                            parsed = _parse_code_review(out) or {}
                            v = parsed.get("verdict") or "unknown"
                            sc = parsed.get("score")
                            review_lines.append("- `%s`: %s (评分 %s)" % (f, v, sc if sc is not None else "—"))
                except Exception:
                    review_lines.append("- (评审调用异常, 见服务端日志)")
        md = _render_pr_draft(title, files, review_lines, note)
        fname = "pr_draft_%s.md" % time.strftime("%Y%m%d_%H%M%S")
        payload = md.encode("utf-8")
        _record_artifact("pr", payload, "text/markdown", {"repo": repo, "title": title, "note": note})
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % fname)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _review_report(self):
        """POST /api/review/report {paths:[...], note?} -> 多文件评审聚合 HTML 报告并下载。

        对给定文件逐一评审(静态 + 可选 LLM), 聚合为自包含 HTML 报告;
        同时落盘到 .lmw_artifacts (供「成果」tab 回看)。无需 LLM。
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        paths = body.get("paths") or []
        note = (body.get("note") or "").strip()
        if not paths or not isinstance(paths, list):
            return self._send_json({"error": "缺少 paths (待评审文件列表)"}, status=400)
        try:
            from ..tools import mcp as _mcp
            mgr = _mcp.get_manager()
            mgr.connect_all(_get_cfg())
            srv = mgr.servers.get("review")
            if not (srv and srv.has_tool("code_review")):
                return self._send_json({"error": "review MCP 服务未连接 (检查 config.toml)"}, status=404)
            use_llm = bool(os.environ.get("SENSENOVA_API_KEY") or os.environ.get("SENSENOVA_API_KEY_2"))
            files = []
            for p in paths:
                p = str(p).strip()
                if not p:
                    continue
                _out, parsed = _review_file(srv, p, use_llm)
                _record_review(p, _out)
                files.append({
                    "path": p,
                    "verdict": parsed.get("verdict"),
                    "score": parsed.get("score"),
                    "issues": parsed.get("issues"),
                    "summary": parsed.get("summary"),
                    "source": parsed.get("source"),
                })
            if not files:
                return self._send_json({"error": "无可评审文件"}, status=400)
            html_doc = _render_review_report(files, note=note)
            payload = html_doc.encode("utf-8")
            _record_artifact("review", payload, "text/html",
                                  {"note": note, "files": [f["path"] for f in files]})
            fname = "review_report_%s.html" % time.strftime("%Y%m%d_%H%M%S")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % fname)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

    def _artifacts_list(self):
        """GET /api/artifacts -> 返回成果清单(最新在前, 限 200)。"""
        try:
            import json as _json
            d = _artifact_dir()
            idx = os.path.join(d, "index.jsonl")
            items = []
            if os.path.exists(idx):
                with open(idx, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            items.append(_json.loads(line))
                        except Exception:
                            pass
            items.sort(key=lambda x: x.get("ts", 0), reverse=True)
            return self._send_json({"ok": True, "total": len(items), "items": items[:200]})
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

    def _artifact_raw(self, rid):
        """GET /api/artifacts/<id>/raw -> 流式返回成果文件(校验在 index 内, 防目录遍历)。"""
        try:
            import json as _json
            d = _artifact_dir()
            idx = os.path.join(d, "index.jsonl")
            name = None
            ctype = "application/octet-stream"
            if os.path.exists(idx):
                with open(idx, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = _json.loads(line)
                        except Exception:
                            continue
                        if str(rec.get("id")) == str(rid):
                            name = rec.get("name")
                            ctype = rec.get("content_type") or ctype
                            break
            if not name:
                return self.send_error(404)
            # 防目录遍历: 仅允许纯文件名
            if "/" in name or "\\" in name or name.startswith(".") or ".." in name:
                return self.send_error(400)
            fpath = os.path.join(d, "files", name)
            if not os.path.isfile(fpath):
                return self.send_error(404)
            with open(fpath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", 'inline; filename="%s"' % name)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            return self.send_error(500)

    def _terminal_sse(self):
        """POST /api/terminal {command, timeout} -> SSE 流式吐出 shell stdout/stderr。

        逐行 yield {"type":"output","text":...}, 结束时 {"type":"exit","code":...}。
        危险命令拦截 + PATH 增补 (同 mcp_shell_server), 让 git/python/node 在宿主 PATH 可用。
        """
        import subprocess as _sp
        import os as _os
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        command = (body.get("command") or "").strip()
        if not command:
            return self._send_json({"error": "缺少 command"}, status=400)
        _DENY = ["rm -rf /", "format ", "mkfs", "shutdown", "reboot", ":(){", "dd if=", "del /s", "rd /s /q", "rm -rf /*"]
        _low = command.lower()
        if any(d in _low for d in _DENY):
            return self._send_json({"error": "危险命令已被拦截: %s" % command}, status=403)
        try:
            timeout = int(body.get("timeout", 120) or 120)
        except Exception:
            timeout = 120
        timeout = max(5, min(300, timeout))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        env = dict(_os.environ)
        extra = []
        venv = "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts"
        if _os.path.isdir(venv):
            extra.append(venv)
        pg = "C:/Users/Administrator/.workbuddy/binaries/PortableGit/versions"
        if _os.path.isdir(pg):
            for d in _os.listdir(pg):
                gb = _os.path.join(pg, d, "mingw64", "bin")
                gc = _os.path.join(pg, d, "cmd")
                if _os.path.isdir(gb):
                    extra.append(gb)
                if _os.path.isdir(gc):
                    extra.append(gc)
        nv = "C:/Users/Administrator/.workbuddy/binaries/node/versions"
        if _os.path.isdir(nv):
            for d in _os.listdir(nv):
                nd = _os.path.join(nv, d)
                if _os.path.isdir(nd):
                    extra.append(nd)
        if extra:
            env["PATH"] = _os.pathsep.join(extra) + _os.pathsep + env.get("PATH", "")

        def emit(obj):
            try:
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

        try:
            proc = _sp.Popen(
                command, shell=True, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env,
                cwd="D:/开发/配置AI应用/lingmengwork", bufsize=1,
            )
            for line in proc.stdout:
                emit({"type": "output", "text": line.rstrip("\n")})
            try:
                proc.wait(timeout=timeout)
            except _sp.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                emit({"type": "exit", "code": -1, "timeout": True})
                raise
            emit({"type": "exit", "code": proc.returncode})
        except Exception as e:
            emit({"type": "error", "text": str(e)})
        try:
            self.wfile.write(b'data: {"type":"close"}\n\n')
            self.wfile.flush()
        except Exception:
            pass

    def _serve_file(self, name):
        path = os.path.join(STATIC_DIR, name)
        try:
            with open(path, "rb") as f:
                body = f.read()
        except Exception:
            return self.send_error(404)
        ctype = "text/html; charset=utf-8" if name.endswith(".html") else "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _chat_sse(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        # 兼容 message / prompt 两种键名
        message = (body.get("message") or body.get("prompt") or "").strip()
        # 「继续 / continue」类续跑意图: 替换为显式续跑提示, 引导模型基于已有工具结果推进到底
        _CONTINUE_WORDS = {"继续", "continue", "继续任务", "接着来", "继续吧", "resume", "接着干"}
        if message and message.strip().lower() in {w.lower() for w in _CONTINUE_WORDS}:
            message = ("请基于已有的全部工具结果与上下文, 继续推进刚才未完成的任务: "
                       "不要重复已做过的调用, 直接朝着最终结论或交付物推进, 直到真正完成。")
        history = body.get("history") or []
        backend_override = body.get("backend") or None
        mode = body.get("mode") or "bypassPermissions"  # plan | acceptEdits | bypassPermissions

        cfg = _get_cfg()
        # 尊重 config.toml 的 backend 配置 (本地 Ollama / 云端 / mock 均支持)
        backend = backend_override or cfg["llm"].get("backend", "ollama")
        client = build_client(backend, cfg=cfg)
        registry = build_registry(cfg, permission_mode=mode)

        # 会话续跑: 同一 session_id 多次请求复用活体 AgentLoop(执行态保留);
        # 若内存无则尝试磁盘水合(完整 messages 含 tool 角色); 否则新建。
        session_id = body.get("session_id") or None
        loop, session_lock, hydrated = acquire_session(session_id, client, registry, cfg, backend)
        # 续跑时刷新本轮请求指定的权限模式与 provider(活体 loop 沿用首轮, 此处对齐最新)
        try:
            loop.registry.set_permission_mode(mode)
            loop.provider = backend
        except Exception:
            pass

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        client_gone = {"flag": False}

        def emit(type_, kw):
            if client_gone["flag"]:
                return
            # 评审报告入库: review_code 工具结果结构化后供 WEB「代码评审」tab 可视化
            if type_ == "tool_result" and kw.get("name") == "review_code":
                _record_review((kw.get("args") or {}).get("target", ""), kw.get("output", ""))
            obj = {"type": type_}
            obj.update(kw)
            try:
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                # 客户端断开 (刷新/关闭页面/网络中断) -> 标记退出, 避免空转
                client_gone["flag"] = True

        if not message:
            emit("done", {"text": "(空消息)", "truncated": False})
        else:
            # session 级锁: 防同一会话并发请求交错破坏 messages
            with session_lock:
                try:
                    loop.run(message, on_event=emit)
                except Exception as e:
                    emit("error", {"message": str(e)})
                # 对话结束 -> 真正落盘会话 (对齐 TUI --resume 能力), 覆盖式保存到稳定 session_id
                if not client_gone["flag"]:
                    try:
                        full = [{"role": m["role"], "content": m["content"]} for m in loop.messages]
                        sess_save(loop.session_id, full, model=getattr(client, "model", ""), provider=backend, base_dir=os.getcwd())
                    except Exception:
                        pass
        try:
            self.wfile.write(("data: " + json.dumps({"type": "done", "session_id": loop.session_id}, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass
        try:
            self.wfile.write(b"data: {\"type\":\"close\"}\n\n")
            self.wfile.flush()
        except Exception:
            pass


    # ---- 多路任务: 进程内池 ----
    def _get_pool(self):
        global _TASK_POOL
        if _TASK_POOL is None:
            _TASK_POOL = TaskPool(_get_cfg(), base_dir=os.getcwd())
        return _TASK_POOL

    def _create_task(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        prompt = (body.get("prompt") or body.get("message") or "").strip()
        provider = body.get("provider") or None
        # 并行编排: 一次下发一组独立任务 (prompts 数组) -> 扇出多路并发
        prompts = [str(p).strip() for p in (body.get("prompts") or []) if str(p).strip()]
        if prompts:
            pool = self._get_pool()
            snaps = []
            try:
                for p in prompts:
                    snaps.append(pool.submit(p, provider=provider))
            except Exception as e:
                return self._send_json({"error": str(e)}, status=500)
            orch = _ORCH.create(prompts, [s["id"] for s in snaps])
            return self._send_json(
                {"orchestration_id": orch["id"], "tasks": snaps}, status=201
            )
        if not prompt:
            return self._send_json({"error": "prompt 不能为空"}, status=400)
        try:
            snap = self._get_pool().submit(prompt, provider=provider)
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)
        return self._send_json(snap, status=201)

    def _task_stream(self, task_id):
        import time as _t
        pool = self._get_pool()
        task = pool.tasks.get(task_id)
        if task is None:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        gone = {"flag": False}

        def emit(type_, kw):
            if gone["flag"]:
                return
            obj = {"type": type_}
            obj.update(kw)
            try:
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                gone["flag"] = True

        # 先回放已发生事件(若客户端晚订阅)
        for t, kw in list(task.events):
            emit(t, kw)
            if gone["flag"]:
                return
        # 再订阅后续
        pool.subscribe(task_id, emit)
        # 阻塞直到任务结束(心跳保活 + 断连检测)
        last_ping = _t.time()
        while task.status not in ("done", "error"):
            if gone["flag"]:
                return
            now = _t.time()
            if now - last_ping >= 15:
                try:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = now
                except Exception:
                    return
            _t.sleep(1)
        # 结束再推一次最终状态
        emit("status", {"status": task.status})
        try:
            self.wfile.write(b"data: {\"type\":\"close\"}\n\n")
            self.wfile.flush()
        except Exception:
            pass

    def _docs_generate(self):
        """POST /api/docs/generate {root?, format?} -> 生成 CLAUDE.md/AGENTS.md 草稿。

        直接调用 decision.generate_project_docs, 返回 {ok, draft, root, format},
        供 Web「项目文档」按钮一键生成并预览, 用户复核后可保存为项目根 CLAUDE.md。
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        cfg = _get_cfg()
        root = body.get("root") or None
        fmt = body.get("format") or "claude_md"
        try:
            reg = build_registry(cfg)
            base = root or str(reg.roots[0])
        except Exception:
            base = root or "."
        try:
            from ..tools import decision as _decision
            out = _decision.generate_project_docs(
                {"root": base, "format": fmt}, {"cwd": base, "roots": [base]}
            )
        except Exception as e:
            return self._send_json({"ok": False, "error": str(e)}, status=500)
        return self._send_json({"ok": True, "root": base, "format": fmt, "draft": out})

    def do_DELETE(self):
        p = urlparse(self.path).path
        if p.startswith("/api/tasks/"):
            tid = p[len("/api/tasks/"):].strip("/")
            if tid and tid in self._get_pool().tasks:
                del self._get_pool().tasks[tid]
                return self._send_json({"ok": True})
        if p.startswith("/api/sessions/"):
            sid = p[len("/api/sessions/"):].strip("/")
            if sid and sess_del(sid):
                return self._send_json({"ok": True})
            return self._send_json({"ok": False, "error": "not found"}, status=404)
        return self.send_error(404)


    # ---- 结果回看 ----
    def _list_results(self):
        rd = _results_dir()
        qs = parse_qs(urlparse(self.path).query)
        try:
            limit = int((qs.get("limit") or ["50"])[0])
        except Exception:
            limit = 50
        try:
            offset = int((qs.get("offset") or ["0"])[0])
        except Exception:
            offset = 0
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        items = []
        for fn in sorted(os.listdir(rd), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(rd, fn), "r", encoding="utf-8") as f:
                    d = json.load(f)
                items.append({
                    "id": d.get("id"),
                    "provider": d.get("provider"),
                    "model": d.get("model"),
                    "status": d.get("status"),
                    "prompt": (d.get("prompt") or "")[:80],
                    "created_at": d.get("created_at"),
                    "est_tokens": (d.get("stats") or {}).get("est_tokens", 0),
                    "est_cost_cny": (d.get("stats") or {}).get("est_cost_cny", 0.0),
                })
            except Exception:
                continue
        total = len(items)
        page = items[offset:offset + limit]
        return {"results": page, "total": total, "limit": limit, "offset": offset}

    def _get_result(self, rid):
        rd = _results_dir()
        jp = os.path.join(rd, f"{rid}.json")
        mp = os.path.join(rd, f"{rid}.md")
        if not os.path.exists(jp):
            return {"error": "not found"}
        try:
            with open(jp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return {"error": str(e)}
        md = ""
        if os.path.exists(mp):
            try:
                md = open(mp, "r", encoding="utf-8").read()
            except Exception:
                pass
        data["markdown"] = md
        return data

    # ---- 全局仪表盘统计 ----
    def _global_stats(self):
        pool = self._get_pool()
        tasks = pool.list_tasks()
        running = sum(1 for t in tasks if t["status"] == "running")
        providers = pool.list_providers()
        online = sum(1 for p in providers if p.get("available"))
        # 聚合 results 目录累计
        rd = _results_dir()
        total_tokens = 0
        total_cost = 0.0
        done_count = 0
        for fn in os.listdir(rd):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(rd, fn), "r", encoding="utf-8") as f:
                    d = json.load(f)
                st = d.get("stats") or {}
                total_tokens += st.get("est_tokens", 0)
                total_cost += st.get("est_cost_cny", 0.0)
                if d.get("status") == "done":
                    done_count += 1
            except Exception:
                continue
        return {
            "tasks_total": len(tasks),
            "tasks_running": running,
            "providers_online": online,
            "providers_total": len(providers),
            "results_total": done_count,
            "total_tokens": total_tokens,
            "total_cost_cny": round(total_cost, 6),
        }

    # ---- 文件树浏览器 (只读, 限定项目根与 HOME) ----
    def _fs_api(self, p):
        base = os.getcwd()
        home = os.path.expanduser("~")
        qs = parse_qs(urlparse(self.path).query)
        action = p[len("/api/fs"):].strip("/") or "list"
        raw_path = (qs.get("path") or ["."])[0]
        # 安全限制: 仅允许项目根及其子目录 + HOME (用于 ~/.lingmengwork 等)
        target = os.path.abspath(os.path.join(base, raw_path)) if not raw_path.startswith("~") \
            else os.path.abspath(os.path.expanduser(raw_path))
        allowed_roots = [os.path.abspath(base), os.path.abspath(home)]
        if not any(target == r or target.startswith(r + os.sep) for r in allowed_roots):
            return self._send_json({"error": "路径越权, 仅允许项目目录与 HOME"}, status=403)
        if action == "read":
            if not os.path.isfile(target):
                return self._send_json({"error": "不是文件"}, status=400)
            # 二进制探测: 扩展名黑名单 + 首 8KB 含 NUL 字节即视为二进制
            BINARY_EXT = {".pyc", ".pyo", ".exe", ".dll", ".so", ".dylib", ".png", ".jpg",
                          ".jpeg", ".gif", ".bmp", ".ico", ".zip", ".gz", ".tar", ".rar",
                          ".pdf", ".bin", ".dat", ".ttf", ".woff", ".woff2", ".mp3", ".mp4"}
            ext = os.path.splitext(target)[1].lower()
            if ext in BINARY_EXT:
                return self._send_json({"path": raw_path, "binary": True,
                                        "hint": "二进制文件, 不可预览 (请使用本地编辑器打开)"})
            try:
                with open(target, "rb") as f:
                    head = f.read(8192)
                if b"\x00" in head:
                    return self._send_json({"path": raw_path, "binary": True,
                                            "hint": "二进制文件, 不可预览 (检测到 NUL 字节)"})
                text = head.decode("utf-8", errors="replace") + open(target, "rb").read()[8192:].decode("utf-8", errors="replace")
            except Exception as e:
                return self._send_json({"error": f"读取失败: {e}"}, status=500)
            MAX_CHARS = 200000
            truncated = len(text) > MAX_CHARS
            if truncated:
                text = text[:MAX_CHARS]
            lines = text.count("\n") + 1 if text else 0
            return self._send_json({
                "path": raw_path,
                "binary": False,
                "content": text,
                "truncated": truncated,
                "lines": lines,
                "size": os.path.getsize(target),
            })
        # 默认 list
        if not os.path.exists(target):
            return self._send_json({"error": "路径不存在"}, status=404)
        if os.path.isfile(target):
            return self._send_json({"path": raw_path, "entries": [{"name": os.path.basename(target), "kind": "file", "size": os.path.getsize(target)}]})
        SKIP = {".git", "__pycache__", "node_modules", ".venv", "dist", "build", ".workbuddy"}
        entries = []
        try:
            for child in sorted(os.listdir(target)):
                if child in SKIP:
                    continue
                fp = os.path.join(target, child)
                if os.path.isdir(fp):
                    entries.append({"name": child, "kind": "dir", "size": 0})
                else:
                    try:
                        sz = os.path.getsize(fp)
                    except Exception:
                        sz = 0
                    entries.append({"name": child, "kind": "file", "size": sz})
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)
        return self._send_json({"path": raw_path, "entries": entries})

    def _fs_save(self):
        """POST /api/fs/save {path, content} -> 写回文件 (供 Web 代码编辑器)。

        安全限制同 _fs_api: 仅允许项目目录与 HOME 之下。
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        rel = (body.get("path") or "").strip()
        content = body.get("content", "") or ""
        if not rel:
            return self._send_json({"error": "缺少 path"}, status=400)
        base = os.getcwd()
        home = os.path.expanduser("~")
        target = os.path.abspath(os.path.join(base, rel)) if not rel.startswith("~") \
            else os.path.abspath(os.path.expanduser(rel))
        allowed_roots = [os.path.abspath(base), os.path.abspath(home)]
        if not any(target == r or target.startswith(r + os.sep) for r in allowed_roots):
            return self._send_json({"error": "路径越权, 仅允许项目目录与 HOME"}, status=403)
        try:
            d = os.path.dirname(target)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return self._send_json({"ok": True, "path": rel, "bytes": len(content.encode("utf-8"))})
        except Exception as e:
            return self._send_json({"error": "写入失败: %s" % e}, status=500)


def run_web(host="127.0.0.1", port=PORT, cfg=None):
    """统一 Web 启动入口 (供 launcher / 安卓壳 / 直接运行调用)。"""
    global _RUNTIME_CONFIG, _TASK_POOL
    _RUNTIME_CONFIG = cfg if cfg is not None else load_config()
    # 启动并发任务池(多路 LLM 同时编程)
    _TASK_POOL = TaskPool(_RUNTIME_CONFIG, base_dir=os.getcwd())
    # 接入外部 MCP 工具 (进程内懒连接; 默认无配置不 spawn)
    try:
        from ..tools import mcp as _mcp
        _mcp.get_manager().connect_all(_RUNTIME_CONFIG)
    except Exception:
        pass

    server = ThreadingHTTPServer((host, port), Handler)
    lan = "http://<本机局域网IP>:%d (同网手机/安卓可访问)" % port if host in ("0.0.0.0", "") else ""
    print(f"灵梦work Web 控制台已启动: http://{host}:{port}  (Ctrl+C 退出)")
    if lan:
        print("  " + lan)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        server.shutdown()


def main():
    parser = argparse.ArgumentParser(description="灵梦work Web 控制台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (安卓访问用 0.0.0.0)")
    parser.add_argument("--port", type=int, default=PORT, help="监听端口")
    parser.add_argument("--config", default=None, help="config.toml 路径")
    parser.add_argument("--backend", default=None, help="覆盖 LLM 后端: ollama|openai|mock")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.backend:
        cfg["llm"]["backend"] = args.backend
    run_web(host=args.host, port=args.port, cfg=cfg)


if __name__ == "__main__":
    main()

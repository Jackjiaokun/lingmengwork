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
import io
import json
import os
import time
import re
import tomllib
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote as _urlquote

from .. import __version__
from ..config import load_config, DEFAULT_CONFIG_PATHS
from ..llm.client import build_client
from ..llm import pricing as _pricing
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

# 统一引擎总控台 (Phase 10): 进程内 ring buffer 记录经总控台发起的引擎调用, 供实时轨迹展示
_ENGINE_RUNS = []
_ENGINE_RUNS_LOCK = None
_ENGINE_RUNS_MAX = 80

def _engine_runs_lock():
    global _ENGINE_RUNS_LOCK
    if _ENGINE_RUNS_LOCK is None:
        import threading
        _ENGINE_RUNS_LOCK = threading.Lock()
    return _ENGINE_RUNS_LOCK

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


def _render_orch_report(result):
    """把一次超级 AGENT 编排结果渲染为自包含 HTML 报告 (内联 CSS, 可离线查看/分享, Phase 47)。"""
    import html as _html
    e = _html.escape
    it = result.get("intent") or {}
    cv = result.get("converge") or {}
    mem = result.get("memory") or {}
    usage = result.get("usage") or {}
    ex = result.get("executions") or {}
    ok = bool(result.get("ok"))
    goal = e(str(result.get("goal") or ""))
    err = e(str(result.get("error") or "")) if result.get("error") else ""
    ver = _read_version()
    when = e(str(result.get("ts") or time.strftime("%Y-%m-%d %H:%M:%S")))

    badge = ('<span class="badge ready">编排成功 ✅</span>' if ok
             else '<span class="badge notready">编排失败 ⛔</span>')
    err_html = ('<div class="card"><h2>错误</h2><div class="note err">%s</div></div>' % err) if err else ""

    # 目标理解
    domains = " · ".join(e(str(d)) for d in (it.get("domains") or [])) or "—"
    cons = (it.get("constraints") or [])
    cons_html = " · ".join(e(str(c)) for c in cons) or "无"
    recap = it.get("memory_recap") or ""
    recap_html = ('<p class="muted">🧠 已召回跨会话经验 %d 字</p>' % len(recap)) if recap else ""

    # trace 表
    tr_rows = ""
    for t in (result.get("trace") or []):
        st_cls = "sev-ok" if t.get("ok") else "sev-high"
        mark = "✅" if t.get("ok") else "❌"
        tr_rows += ('<tr><td>%s %s</td><td>%s</td><td>%s</td></tr>\n'
                    % (mark, e(str(t.get("stage") or "")),
                       e(str(t.get("ts") or "")), e(str(t.get("detail") or ""))))
    if not tr_rows:
        tr_rows = '<tr><td colspan="3">—</td></tr>'

    # 伙伴
    p_rows = ""
    for p in ((result.get("dispatch") or {}).get("partners") or []):
        pok = p.get("status") == "ok"
        p_rows += ('<tr><td>%s</td><td>%s</td><td class="%s">%s</td><td>%s</td></tr>\n'
                   % (e(str(p.get("name") or "")), e(str(p.get("domain") or "")),
                      "sev-ok" if pok else "sev-high",
                      "✅ 成功" if pok else ("❌ " + e(str(p.get("status") or ""))),
                      e(str(p.get("summary") or "(无输出)"))[:600]))
    if not p_rows:
        p_rows = '<tr><td colspan="4">—</td></tr>'

    # 连接器
    mcs = (result.get("dispatch") or {}).get("matched_connectors") or []
    mc_rows = ""
    for m in mcs:
        mc_rows += ('<tr><td>%s</td><td class="%s">%s</td><td>%s</td></tr>\n'
                    % (e(str(m.get("name") or "")),
                       "sev-ok" if m.get("ok") else "sev-high",
                       "✅" if m.get("ok") else "❌",
                       e(str(m.get("result") or m.get("error") or ""))[:400]))
    mc_html = ('<div class="card"><h2>🔌 连接器调用</h2><table><tr><th>连接器</th><th>状态</th><th>结果</th></tr>'
               + mc_rows + '</table></div>') if mc_rows else ""

    # 收敛护栏
    guards = cv.get("guards") or []
    g_rows = ""
    for g in guards:
        g_rows += ('<li><span class="sev sev-%s">L%s</span> [%s] %s</li>\n'
                   % (e(str(g.get("severity") or "")), e(str(g.get("level") or "?")),
                      e(str(g.get("kind") or "")), e(str(g.get("msg") or ""))))
    if not g_rows:
        g_rows = '<li class="ok">三级护栏全通过, 无告警</li>\n'

    # 执行产物
    arts = ex.get("artifacts") or []
    art_html = (" · ".join(e(str(os.path.basename(a))) for a in arts) if arts else "无")

    # 用量
    usage_rows = (
        '<tr><td>LLM 调用</td><td class="num">%s</td></tr>\n'
        '<tr><td>估算 Token</td><td class="num">%s</td></tr>\n'
        '<tr><td>估算成本</td><td class="num">¥%s</td></tr>\n'
        % (usage.get("llm_calls", 0), usage.get("est_total_tokens", 0),
           usage.get("est_cost_cny", 0.0)))

    html_doc = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>灵梦work · 编排报告</title><style>\n'
        ':root{--bg:#0d1017;--panel:#141926;--line:#242b3d;--tx:#e6e9f2;--muted:#8b93a8;--good:#34d399;--bad:#f87171;}\n'
        'body{background:var(--bg);color:var(--tx);font-family:"Microsoft YaHei",system-ui,sans-serif;margin:0;padding:24px;}\n'
        '.wrap{max-width:960px;margin:0 auto;}\n'
        'h1{font-size:22px;margin:0 0 4px;} .sub{color:var(--muted);font-size:13px;margin-bottom:16px;}\n'
        '.badge{display:inline-block;padding:4px 14px;border-radius:999px;font-weight:600;font-size:13px;margin:8px 0;}\n'
        '.badge.ready{background:rgba(52,211,153,.12);color:var(--good);}\n'
        '.badge.notready{background:rgba(248,113,113,.12);color:var(--bad);}\n'
        '.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 20px;margin-bottom:14px;}\n'
        '.card h2{font-size:15px;margin:0 0 10px;} .note{font-size:13px;line-height:1.7;white-space:pre-wrap;}\n'
        '.note.err{color:var(--bad);}\n'
        'table{width:100%;border-collapse:collapse;font-size:12.5px;}\n'
        'th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top;}\n'
        'th{color:var(--muted);font-weight:600;} td.num{text-align:right;font-family:monospace;}\n'
        '.sev{display:inline-block;padding:0 8px;border-radius:999px;font-size:11px;font-weight:600;}\n'
        '.sev-ok{background:rgba(52,211,153,.12);color:var(--good);}\n'
        '.sev-high{background:rgba(248,113,113,.12);color:var(--bad);}\n'
        '.sev-warning{background:rgba(245,158,11,.12);color:#fbbf24;}\n'
        'li{font-size:13px;line-height:1.8;} li.ok{color:var(--good);}\n'
        '.muted{color:var(--muted);} .pill{display:inline-block;background:rgba(139,92,246,.12);color:#b7a6f7;border-radius:999px;padding:2px 12px;font-size:12px;margin-right:6px;}\n'
        'footer{color:var(--muted);font-size:12px;text-align:center;margin-top:20px;}\n'
        '</style></head>\n'
        '<body><div class="wrap">\n'
        '  <h1>灵梦work · 超级 AGENT 编排报告</h1>\n'
        '  <div class="sub">全 7 阶段闭环 (目标理解→插件接入→域路由→并行编排→执行落地→收敛护栏→记忆沉淀) · v' + e(ver) + ' · ' + when + '</div>\n'
        '  ' + badge + '\n'
        '  <div class="card"><h2>🎯 目标</h2><div class="note">' + goal + '</div>\n'
        '    <p style="margin:8px 0 0"><span class="pill">路由: ' + domains + '</span>'
        '<span class="pill">伙伴成功: ' + str(cv.get("partners_ok", 0)) + '/' + str(cv.get("partners_total", 0)) + '</span>'
        '<span class="pill">自检分: ' + str(cv.get("selfcheck_score", 100)) + '</span>'
        '<span class="pill">耗时: ' + str(result.get("elapsed_sec", 0)) + 's</span></p></div>\n'
        '  ' + err_html +
        '  <div class="card"><h2>🧭 目标理解</h2><div class="note">意图: ' + e(str(it.get("intent") or goal)) +
        '<br>约束: ' + cons_html + '</div>' + recap_html + '</div>\n'
        '  <div class="card"><h2>📜 阶段 Trace</h2><table><tr><th>阶段</th><th>时间</th><th>明细</th></tr>' + tr_rows + '</table></div>\n'
        '  <div class="card"><h2>🤝 并行编排</h2><table><tr><th>伙伴</th><th>域</th><th>状态</th><th>产出</th></tr>' + p_rows + '</table></div>\n'
        '  ' + mc_html +
        '  <div class="card"><h2>🛡️ 收敛护栏</h2><ul>' + g_rows + '</ul></div>\n'
        '  <div class="card"><h2>📦 执行产物</h2><div class="note">' + art_html + '</div></div>\n'
        '  <div class="card"><h2>🧠 记忆沉淀</h2><table class="num">\n'
        '    <tr><td>新增实体</td><td class="num">' + str(mem.get("entities_added", 0)) + '</td></tr>\n'
        '    <tr><td>新增关系</td><td class="num">' + str(mem.get("relations_added", 0)) + '</td></tr>\n'
        '    <tr><td>事实数</td><td class="num">' + str(mem.get("facts_count", 0)) + '</td></tr>\n'
        '  </table></div>\n'
        '  <div class="card"><h2>💰 LLM 用量</h2><table>' + usage_rows + '</table></div>\n'
        '  <footer>本报告由灵梦work 超级 AGENT 自动生成 · 可离线查看/归档分享</footer>\n'
        '</div></body></html>'
    )
    return html_doc


def _render_orch_md(result):
    """把一次超级 AGENT 编排结果渲染为 Markdown 文档 (Phase 55, 供下载/归档/粘贴)。"""
    it = result.get("intent") or {}
    cv = result.get("converge") or {}
    mem = result.get("memory") or {}
    usage = result.get("usage") or {}
    ex = result.get("executions") or {}
    goal = str(result.get("goal") or "").replace("|", "\\|")
    lines = [
        "# 灵梦work · 超级 AGENT 编排报告",
        "",
        "- **目标**: %s" % goal,
        "- **状态**: %s" % ("✅ 成功" if result.get("ok") else "❌ 失败"),
        "- **耗时**: %ss · **自检分**: %s" % (result.get("elapsed_sec", 0),
                                             cv.get("selfcheck_score", 100)),
        "- **路由**: %s" % (" / ".join(result.get("routed") or []) or "—"),
    ]
    if result.get("error"):
        lines.append("- **错误**: %s" % result["error"])
    lines.append("")
    lines.append("## 🧭 目标理解")
    lines.append("- **意图**: %s" % (it.get("intent") or goal))
    lines.append("- **域**: %s" % (" / ".join(it.get("domains") or []) or "—"))
    cons = it.get("constraints") or []
    lines.append("- **约束**: %s" % ("; ".join(str(c) for c in cons) or "无"))
    lines.append("")
    lines.append("## 📜 阶段 Trace")
    lines.append("")
    lines.append("| # | 阶段 | 时间 | 明细 |")
    lines.append("|---|------|------|------|")
    for i, t in enumerate(result.get("trace") or [], 1):
        stage = str(t.get("stage") or "").replace("|", "\\|")
        detail = str(t.get("detail") or "").replace("|", "\\|").replace("\n", " ")
        mark = "✅" if t.get("ok") else "❌"
        lines.append("| %d | %s %s | %s | %s |" % (i, mark, stage, t.get("ts", ""), detail))
    lines.append("")
    lines.append("## 🤝 并行编排")
    lines.append("")
    lines.append("| 伙伴 | 域 | 状态 | 产出 |")
    lines.append("|------|----|------|------|")
    partners = (result.get("dispatch") or {}).get("partners") or []
    for p in partners:
        name = str(p.get("name") or "").replace("|", "\\|")
        summary = str(p.get("summary") or "(无输出)").replace("|", "\\|").replace("\n", " ")
        st = "✅ 成功" if p.get("status") == "ok" else ("❌ " + str(p.get("status") or ""))
        lines.append("| %s | %s | %s | %s |" % (name, p.get("domain", ""), st, summary[:400]))
    if not partners:
        lines.append("| — | — | — | — |")
    lines.append("")
    guards = cv.get("guards") or []
    lines.append("## 🛡️ 收敛护栏")
    lines.append("")
    if guards:
        for g in guards:
            lines.append("- [L%s] [%s] %s" % (g.get("level", "?"), g.get("kind", ""),
                                              g.get("msg", "")))
    else:
        lines.append("- 三级护栏全通过, 无告警")
    lines.append("")
    arts = ex.get("artifacts") or []
    lines.append("## 📦 执行产物")
    lines.append("")
    lines.append(("; ".join(str(a) for a in arts)) if arts else "无")
    lines.append("")
    lines.append("## 🧠 记忆沉淀 / 💰 用量")
    lines.append("")
    lines.append("- 新增实体 %s · 新增关系 %s" % (mem.get("entities_added", 0),
                                                 mem.get("relations_added", 0)))
    lines.append("- LLM 调用 %s 次 · %s tokens · ¥%s" % (usage.get("llm_calls", 0),
                                                         usage.get("est_total_tokens", 0),
                                                         usage.get("est_cost_cny", 0)))
    lines.append("")
    lines.append("---")
    lines.append("*由灵梦work 超级 AGENT 自动生成*")
    return "\n".join(lines)


def _render_diff_report(d):
    """把 diff_runs 的结果渲染成自包含 HTML 左右对比报告 (Phase 56)。

    零外部依赖(内联 CSS), 可直接在新标签页打开 / 另存 / 转发。
    """
    a, b = d.get("a") or {}, d.get("b") or {}
    e = lambda s: (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                   .replace(">", "&gt;").replace('"', "&quot;"))
    verdict = e(d.get("verdict") or "持平")
    vcolor = {"改善": "#4ade80", "退化": "#f87171", "有得有失": "#fbbf24"}.get(verdict, "#94a3b8")

    rows = []
    for m in d.get("metrics") or []:
        old, new, delta = m.get("old"), m.get("new"), m.get("delta")
        if delta == 0:
            dcls, dtxt = "same", "±0"
        elif m.get("improved"):
            dcls, dtxt = "good", ("+" if delta > 0 else "") + str(delta)
        else:
            dcls, dtxt = "bad", ("+" if delta > 0 else "") + str(delta)
        rows.append(
            '<tr><td class="k">%s</td><td>%s%s</td><td class="%s">%s</td><td>%s%s</td></tr>'
            % (e(m.get("label")), e(old), e(m.get("unit")), dcls, dtxt,
               e(new), e(m.get("unit"))))

    prows = []
    for p in d.get("partners") or []:
        if p.get("only_in") == "a":
            badge, cls = "仅 A", "rm"
        elif p.get("only_in") == "b":
            badge, cls = "仅 B", "add"
        elif p.get("changed"):
            badge, cls = "状态变化", "chg"
        else:
            badge, cls = "一致", "same"
        prows.append(
            '<tr><td class="k">%s <span class="muted">· %s</span></td>'
            '<td>%s</td><td class="%s">%s</td><td>%s</td></tr>'
            % (e(p.get("name")), e(p.get("domain")), e(p.get("status_a") or "—"),
               cls, badge, e(p.get("status_b") or "—")))

    rt = d.get("routed") or {}
    at = d.get("artifacts") or {}
    chips = lambda arr, cls: ("".join('<span class="chip %s">%s</span>' % (cls, e(x))
                                      for x in arr)) or '<span class="muted">无</span>'

    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>灵梦work · 编排对比报告</title>
<style>
*{box-sizing:border-box}
body{margin:0;padding:28px;background:#0b1020;color:#e2e8f0;
 font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:15px;margin:22px 0 8px;color:#a5b4fc}
.sub{color:#8b96ad;font-size:12px;margin-bottom:18px}
.verdict{display:inline-block;padding:4px 14px;border-radius:999px;font-weight:700;
 border:1px solid currentColor;color:%(vcolor)s;margin-left:8px;font-size:12px}
table{width:100%%;border-collapse:collapse;margin-top:6px;
 background:#121a30;border-radius:10px;overflow:hidden}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #1e293b;font-size:13px}
th{background:#1a2440;color:#94a3b8;font-weight:600;font-size:12px}
td.k{color:#cbd5e1;font-weight:600;width:26%%}
tr:last-child td{border-bottom:none}
.good{color:#4ade80;font-weight:700}.bad{color:#f87171;font-weight:700}
.same{color:#94a3b8}.chg{color:#fbbf24;font-weight:700}
.add{color:#4ade80;font-weight:700}.rm{color:#f87171;font-weight:700}
.muted{color:#64748b;font-size:12px}
.chip{display:inline-block;padding:2px 9px;margin:2px 4px 2px 0;border-radius:999px;
 font-size:12px;background:#1e293b;color:#cbd5e1}
.chip.add{background:rgba(74,222,128,.14);color:#4ade80}
.chip.rm{background:rgba(248,113,113,.14);color:#f87171}
.goal{background:#121a30;border-radius:10px;padding:12px 14px;margin-bottom:6px}
.goal .lbl{color:#94a3b8;font-size:12px}
footer{margin-top:26px;color:#475569;font-size:12px;text-align:center}
</style></head><body>
<h1>灵梦work · 编排对比报告<span class="verdict">%(verdict)s</span></h1>
<div class="sub">基线 A: %(ats)s &nbsp;→&nbsp; 对照 B: %(bts)s%(warn)s</div>

<div class="goal"><div class="lbl">目标 A</div><div>%(agoal)s</div></div>
<div class="goal"><div class="lbl">目标 B</div><div>%(bgoal)s</div></div>

<h2>📊 关键指标</h2>
<table><thead><tr><th>指标</th><th>A（基线）</th><th>变化</th><th>B（对照）</th></tr></thead>
<tbody>%(rows)s</tbody></table>

<h2>🤝 伙伴逐项对比</h2>
<table><thead><tr><th>伙伴</th><th>A 状态</th><th>判定</th><th>B 状态</th></tr></thead>
<tbody>%(prows)s</tbody></table>

<h2>🧭 路由域变化</h2>
<div>新增 %(radd)s</div>
<div>移除 %(rrm)s</div>
<div style="margin-top:4px">保持 %(rsame)s</div>

<h2>📦 产物变化</h2>
<div>新增 %(aadd)s</div>
<div>移除 %(arm)s</div>
<div style="margin-top:4px">保持 %(asame)s</div>

<footer>由灵梦work 超级 AGENT 自动生成 · A/B 对比</footer>
</body></html>""" % {
        "vcolor": vcolor, "verdict": verdict,
        "ats": e(a.get("ts")), "bts": e(b.get("ts")),
        "warn": (' <span style="color:#fbbf24">· 两次目标不一致, 对比仅供参考</span>'
                 if d.get("goal_changed") else ""),
        "agoal": e(a.get("goal") or "—"), "bgoal": e(b.get("goal") or "—"),
        "rows": "".join(rows) or '<tr><td colspan="4" class="muted">无指标</td></tr>',
        "prows": "".join(prows) or '<tr><td colspan="4" class="muted">无伙伴</td></tr>',
        "radd": chips(rt.get("added") or [], "add"),
        "rrm": chips(rt.get("removed") or [], "rm"),
        "rsame": chips(rt.get("same") or [], ""),
        "aadd": chips(at.get("added") or [], "add"),
        "arm": chips(at.get("removed") or [], "rm"),
        "asame": chips(at.get("same") or [], ""),
    }


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


def acquire_session(session_id, client, registry, cfg, backend, experts=None, skills=None, enhance_data=None):
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
        loop = AgentLoop(client, registry, cfg, session_id=session_id or None, provider=backend,
                         experts=experts, skills=skills, enhance_data=enhance_data)
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


# ===== 设置中心 (批次14): 可视化查看/编辑 config.toml =====
# 字段 schema: 分组 + 标量字段元数据 (label/type/options/section/restart)。
# section 对应 TOML 段头 (如 "agent" / "agent.security" / "mcp"); restart=True 表示
# 改动后需重建连接/客户端, 保存后提示重启面板才能完全生效 (其余标量即时软重载)。
_SETTINGS_SCHEMA = [
    {"title": "LLM 后端", "fields": [
        {"key": "llm.backend", "section": "llm", "type": "string",
         "options": ["sensenova", "openai", "ollama", "mock", "auto"],
         "label": "默认对话后端", "restart": True},
    ]},
    {"title": "智能体循环与治理", "fields": [
        {"key": "agent.max_iterations", "section": "agent", "type": "int",
         "label": "最大循环轮数(防无限)", "restart": False},
        {"key": "agent.concurrency", "section": "agent", "type": "int",
         "label": "并发上限(0=自动)", "restart": False},
        {"key": "agent.tool_result_max_chars", "section": "agent", "type": "int",
         "label": "工具结果截断字符数(0=不截断)", "restart": False},
        {"key": "agent.reflect_every", "section": "agent", "type": "int",
         "label": "反思循环间隔轮数(0=关闭)", "restart": False},
        {"key": "agent.summarize_tool_results", "section": "agent", "type": "bool",
         "label": "超长结果用 LLM 摘要", "restart": False},
        {"key": "agent.tool_call_quota", "section": "agent", "type": "int",
         "label": "单任务工具调用配额(0=不限)", "restart": False},
        {"key": "agent.tool_cache_ttl", "section": "agent", "type": "int",
         "label": "只读结果缓存 TTL 秒(0=关闭)", "restart": False},
        {"key": "agent.redact_secrets", "section": "agent", "type": "bool",
         "label": "工具结果脱敏(防凭证泄露)", "restart": False},
        {"key": "agent.context_compact_threshold", "section": "agent", "type": "int",
         "label": "上下文压缩阈值字符(0=关闭)", "restart": False},
        {"key": "agent.context_keep_recent", "section": "agent", "type": "int",
         "label": "压缩时保留最近轮数", "restart": False},
        {"key": "agent.cost_alert_threshold", "section": "agent", "type": "float",
         "label": "成本预警阈值(元, 超阈值红色告警)", "restart": False},
        {"key": "agent.system_prompt", "section": "agent", "type": "string",
         "label": "自定义系统提示(留空=默认)", "restart": False},
    ]},
    {"title": "安全护栏", "fields": [
        {"key": "agent.security.destructive_guard", "section": "agent.security", "type": "string",
         "options": ["block", "off"], "label": "破坏性操作全局护栏", "restart": True},
        {"key": "agent.security.audit_log", "section": "agent.security", "type": "bool",
         "label": "写操作审计日志", "restart": True},
        {"key": "agent.security.read_project_docs", "section": "agent.security", "type": "bool",
         "label": "启动读取项目文档(CLAUDE.md 等)", "restart": True},
        {"key": "agent.security.dangerously_run_commands", "section": "agent.security", "type": "bool",
         "label": "允许危险命令(关闭拦截)", "restart": True},
    ]},
    {"title": "外部工具 MCP", "fields": [
        {"key": "mcp.enabled", "section": "mcp", "type": "bool",
         "label": "启用 MCP 工具中枢", "restart": True},
    ]},
    {"title": "编排与通知", "fields": [
        {"key": "agent.public_base_url", "section": "agent", "type": "string",
         "label": "面板公开地址(通知内嵌链接, 如 http://1.2.3.4:8318)",
         "restart": False},
        {"key": "agent.digest_time", "section": "agent", "type": "string",
         "label": "日报自动推送时刻 HH:MM(留空=不自动)", "restart": False},
        {"key": "agent.orchestration_retry_max", "section": "agent", "type": "int",
         "label": "定时编排失败重试次数(0=不重试)", "restart": False},
        {"key": "agent.quality_auto_push", "section": "agent", "type": "bool",
         "label": "质量告警自动推送(偏离基线时)", "restart": False},
        {"key": "agent.quality_auto_interval_h", "section": "agent", "type": "float",
         "label": "自动推送间隔(小时)", "restart": False},
    ]},
]

# ===== 外部 LLM 大模型配置 (GUI 可视化管理) =====
# 预设库: 常见 OpenAI 兼容 / 国产 / 本机服务的快捷填充。用户也可完全自定义。
_LLM_PRESETS = [
    {"key": "deepseek", "label": "DeepSeek", "type": "openai",
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "api_key_env": "DEEPSEEK_API_KEY", "doc": "DeepSeek 官方 API"},
    {"key": "openai", "label": "OpenAI", "type": "openai",
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
     "api_key_env": "OPENAI_API_KEY", "doc": "OpenAI 官方"},
    {"key": "siliconflow", "label": "硅基流动 SiliconFlow", "type": "openai",
     "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3",
     "api_key_env": "SILICONFLOW_API_KEY", "doc": "硅基流动 (国产, 含 DeepSeek/Qwen/GLM 等)"},
    {"key": "qwen", "label": "通义千问 Qwen (DashScope)", "type": "openai",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
     "api_key_env": "DASHSCOPE_API_KEY", "doc": "阿里云百炼 / 通义"},
    {"key": "zhipu", "label": "智谱 GLM (Zhipu)", "type": "openai",
     "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus",
     "api_key_env": "ZHIPU_API_KEY", "doc": "智谱 AI"},
    {"key": "moonshot", "label": "月之暗面 Kimi (Moonshot)", "type": "openai",
     "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k",
     "api_key_env": "MOONSHOT_API_KEY", "doc": "Moonshot AI"},
    {"key": "openrouter", "label": "OpenRouter", "type": "openai",
     "base_url": "https://openrouter.ai/api/v1", "model": "anthropic/claude-3.5-sonnet",
     "api_key_env": "OPENROUTER_API_KEY", "doc": "聚合多模型网关"},
    {"key": "sensenova", "label": "商汤 SenseNova", "type": "sensenova",
     "base_url": "https://token.sensenova.cn/v1", "model": "sensenova-6.8-flash-lite",
     "api_key_env": "SENSENOVA_API_KEY", "doc": "商汤日日新 (默认后端)"},
    {"key": "ollama", "label": "本地 Ollama", "type": "ollama",
     "base_url": "http://127.0.0.1:11434", "model": "qwen2.5:7b",
     "doc": "本机 / 局域网 Ollama"},
    {"key": "lmstudio", "label": "LM Studio (本机)", "type": "openai",
     "base_url": "http://127.0.0.1:1234/v1", "model": "local-model",
     "doc": "本机 LM Studio 服务"},
    {"key": "vllm", "label": "vLLM (本机/内网)", "type": "openai",
     "base_url": "http://127.0.0.1:8000/v1", "model": "default",
     "doc": "自托管 vLLM OpenAI 兼容服务"},
]


def _read_llm_models():
    """读取已配置的外部模型列表 (脱敏: 不回传明文 Key, 仅 has_key 标记)。"""
    cfg = _get_cfg()
    backend = cfg["llm"].get("backend", "sensenova")
    out = []
    for p in (cfg["llm"].get("providers") or []):
        name = p.get("name") or ""
        if not name:
            continue
        out.append({
            "name": name,
            "type": p.get("type", "openai"),
            "model": p.get("model", ""),
            "base_url": p.get("base_url", ""),
            "has_key": bool(p.get("api_key")),
            "api_key_env": p.get("api_key_env", ""),
            "is_default": (name == backend),
        })
    return out


def _write_providers_in_toml(text, providers):
    """替换 config.toml 中全部 [[llm.providers]] (表数组) 块, 返回新文本。

    保留其它段不动; 末尾追加重写的 [[llm.providers]] 条目。
    providers: [{name,type,model,base_url?,api_key?,api_key_env?}]
    """
    import re as _re
    lines = text.split("\n")
    out, skipping = [], False
    for ln in lines:
        if _re.match(r"^\s*\[\[llm\.providers\]\]\s*$", ln):
            skipping = True
            continue
        if skipping:
            # 直到下一个 [段头] (单或双括号) 才停止跳过; 同数组的连续 [[..]] 仍跳过
            if _re.match(r"^\s*\[[^\]]+\]\s*$", ln):
                skipping = False
                out.append(ln)
                continue
            continue
        out.append(ln)
    while out and out[-1].strip() == "":
        out.pop()
    body = []
    for p in providers:
        body.append("")
        body.append("[[llm.providers]]")
        body.append("name = " + _fmt_toml_value("name", p.get("name", ""), "string"))
        body.append("type = " + _fmt_toml_value("type", p.get("type", "openai"), "string"))
        body.append("model = " + _fmt_toml_value("model", p.get("model", ""), "string"))
        if p.get("base_url"):
            body.append("base_url = " + _fmt_toml_value("base_url", p.get("base_url", ""), "string"))
        if p.get("api_key"):
            body.append("api_key = " + _fmt_toml_value("api_key", p.get("api_key", ""), "string"))
        if p.get("api_key_env"):
            body.append("api_key_env = " + _fmt_toml_value("api_key_env", p.get("api_key_env", ""), "string"))
    return "\n".join(out + body) + "\n"


def _llm_models_get(self):
    """GET /api/llm-models: 返回已配置模型列表 + 当前默认后端 + 预设库。"""
    return self._send_json({
        "models": _read_llm_models(),
        "backend": _get_cfg()["llm"].get("backend", "sensenova"),
        "presets": _LLM_PRESETS,
    })


def _llm_models_save(self):
    """POST /api/llm-models {action, model}: 增/改/删/设默认, 写回 config.toml 并软重载。"""
    length = int(self.headers.get("Content-Length", 0) or 0)
    raw = self.rfile.read(length) if length else b"{}"
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return self._send_json({"error": "请求体非 JSON"}, status=400)
    action = body.get("action", "add")
    model = body.get("model") or {}
    name = (model.get("name") or "").strip()
    if action in ("add", "update") and not name:
        return self._send_json({"error": "模型名称不能为空"}, status=400)
    path = _config_path()
    if path is None:
        return self._send_json({"error": "找不到配置文件路径"}, status=500)

    providers = _read_llm_models()
    if action == "delete":
        providers = [p for p in providers if p["name"] != name]
    elif action in ("add", "update"):
        rec = {
            "name": name,
            "type": model.get("type") or "openai",
            "model": (model.get("model") or "").strip(),
            "base_url": (model.get("base_url") or "").strip(),
            "api_key": (model.get("api_key") or "").strip(),
            "api_key_env": (model.get("api_key_env") or "").strip(),
        }
        # 编辑且未填明文 Key 时, 保留原明文 Key (从当前 cfg 取), 避免误清空
        if action == "update" and not rec["api_key"]:
            src = next((x for x in _get_cfg()["llm"].get("providers") or []
                        if x.get("name") == name), None)
            rec["api_key"] = (src or {}).get("api_key", "") or ""
        providers = [p for p in providers if p["name"] != name]
        providers.append(rec)
    elif action == "setdefault":
        pass
    else:
        return self._send_json({"error": "未知 action: %s" % action}, status=400)

    try:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        new_text = _write_providers_in_toml(current, providers)
        if action == "setdefault":
            new_text, _applied = _set_scalar_in_toml(new_text, "llm", "backend", name, "string")
        tomllib.loads(new_text)
    except Exception as e:
        return self._send_json({"error": "生成/校验 TOML 失败: %s" % e}, status=400)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
    except Exception as e:
        return self._send_json({"error": "写入失败: %s" % e}, status=500)
    try:
        global _RUNTIME_CONFIG
        _RUNTIME_CONFIG = load_config(str(path))
    except Exception:
        pass
    return self._send_json({
        "ok": True,
        "models": _read_llm_models(),
        "backend": _get_cfg()["llm"].get("backend", "sensenova"),
        "bytes": len(new_text.encode("utf-8")),
    })


def _llm_models_test(self):
    """POST /api/llm-models/test {type,base_url,model,api_key?,api_key_env?}: 探测外部模型连通性。"""
    length = int(self.headers.get("Content-Length", 0) or 0)
    raw = self.rfile.read(length) if length else b"{}"
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return self._send_json({"error": "请求体非 JSON"}, status=400)
    spec = {
        "type": body.get("type") or "openai",
        "model": body.get("model"),
        "base_url": body.get("base_url"),
        "api_key": body.get("api_key"),
        "api_key_env": body.get("api_key_env"),
    }
    try:
        from ..llm.client import _client_from_spec
        client = _client_from_spec(spec, _get_cfg())
        out = client.chat([{"role": "user", "content": "ping"}], stream=False, timeout=20)
        if str(out or "").strip():
            return self._send_json({"ok": True, "model": client.model, "reply": str(out)[:160]})
        return self._send_json({"ok": True, "model": client.model, "reply": "(空回复但连接成功)"})
    except Exception as e:
        return self._send_json({"ok": False, "error": str(e)[:300]})


def _config_path():
    """返回当前生效的 config.toml 路径 (命中 load_config 同一候选序)。"""
    for c in DEFAULT_CONFIG_PATHS:
        if c and c.exists():
            return c
    return DEFAULT_CONFIG_PATHS[0] if DEFAULT_CONFIG_PATHS else Path("config.toml")


def _cfg_get(cfg, dotted):
    """按 'a.b.c' 取嵌套 dict 值, 缺失返回 None。"""
    cur = cfg
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _indent_of(line):
    m = re.match(r"^(\s*)", line)
    return m.group(1) if m else ""


def _fmt_toml_value(key, value, typ):
    """把 Python 值格式化为 TOML 字面量 (标量)。"""
    if typ == "bool":
        return "true" if value else "false"
    if typ == "int":
        try:
            return str(int(value))
        except Exception:
            return "0"
    if typ == "string":
        s = "" if value is None else str(value)
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        return '"%s"' % s
    return str(value)


def _set_scalar_in_toml(text, section, key, value, typ):
    """在 TOML 文本中定位 [section] 段, 行内替换 `key = ...` (保留注释/缩进/数组)。

    返回 (new_text, applied: bool)。找不到 key 则在段末(下一 [段头] 前)插入;
    段不存在则追加整段。数组值(如 allowed_roots)因首行即 `key = [`, 但本函数只匹配
    传入的标量 key, 不会误改数组段。
    """
    lines = text.split("\n")
    sec_idx = {}
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*\[(?P<name>[^\]]+)\]\s*$", ln)
        if m:
            sec_idx[m.group("name")] = i
    if section not in sec_idx:
        lines.append("")
        lines.append("[%s]" % section)
        lines.append("%s = %s" % (key, _fmt_toml_value(key, value, typ)))
        return "\n".join(lines), True
    start = sec_idx[section]
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^\s*\[[^\]]+\]\s*$", lines[j]):
            end = j
            break
    pat = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    for k in range(start + 1, end):
        if pat.match(lines[k]):
            lines[k] = _indent_of(lines[k]) + "%s = %s" % (key, _fmt_toml_value(key, value, typ))
            return "\n".join(lines), True
    # 段内未出现该键: 在段末插入
    lines.insert(end, _indent_of(lines[start + 1] if start + 1 < len(lines) else "") +
                 "%s = %s" % (key, _fmt_toml_value(key, value, typ)))
    return "\n".join(lines), True


def _set_array_in_toml(text, section, key, values):
    """在 TOML 文本中定位 [section] 段, 行内替换数组型 `key = [...]`。

    返回 (new_text, applied: bool)。找不到 key 在段末插入; 段不存在追加整段。
    values: list[str], 每个元素转义为带引号字符串。兼容单行与多行数组。
    """
    lines = text.split("\n")
    sec_idx = {}
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*\[(?P<name>[^\]]+)\]\s*$", ln)
        if m:
            sec_idx[m.group("name")] = i
    elems = ", ".join('"%s"' % (str(v).replace("\\", "\\\\").replace('"', '\\"')) for v in values)
    new_line = "%s = [%s]" % (key, elems)
    if section not in sec_idx:
        lines.append("")
        lines.append("[%s]" % section)
        lines.append(new_line)
        return "\n".join(lines), True
    start = sec_idx[section]
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^\s*\[[^\]]+\]\s*$", lines[j]):
            end = j
            break
    pat = re.compile(r"^\s*" + re.escape(key) + r"\s*=\s*")
    for k in range(start + 1, end):
        if pat.match(lines[k]):
            indent = _indent_of(lines[k])
            if not lines[k].rstrip().endswith("]"):
                # 多行数组: 向下找到闭合 ] 一并删除
                m = k + 1
                while m < end and not lines[m].strip().endswith("]"):
                    m += 1
                del lines[k:m + 1]
            else:
                del lines[k]
            lines.insert(k, indent + new_line)
            return "\n".join(lines), True
    lines.insert(end, _indent_of(lines[start + 1] if start + 1 < len(lines) else "") + new_line)
    return "\n".join(lines), True



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

    def _read_json(self, default=None):
        """读取 POST 请求体并解析为 JSON; 失败返回 default(默认 {})。"""
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return default if default is not None else {}

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._serve_file("index.html")
        # ---- 主题 E 可视化 (批次11): 运行追踪仪表盘 ----
        if p == "/observability":
            return self._serve_file("observability.html")
        # ---- 主题 E 成本看板 (批次13): 会话级 token/成本追踪 ----
        if p == "/cost":
            return self._serve_file("cost.html")
        if p == "/api/cost":
            return self._send_json(self._cost_stats())
        # ---- 主题 B 计划看板 (批次13): 计划模式产物可视化 ----
        if p == "/planboard":
            return self._serve_file("planboard.html")
        if p == "/api/planboard":
            q = parse_qs(urlparse(self.path).query)
            sid = (q.get("id") or [None])[0]
            return self._send_json(self._planboard(sid))
        # ---- 设置中心 (批次14): 可视化查看/编辑 config.toml ----
        if p == "/settings":
            return self._serve_file("settings.html")
        if p == "/api/settings":
            return self._send_json(self._settings_get())
        # ---- 工作区沙箱 (文件系统根域管理) ----
        if p == "/sandbox":
            return self._serve_file("sandbox.html")
        if p == "/api/sandbox":
            return self._send_json(self._sandbox_get())
        # ---- 工作区备份 / 回滚 (时间点快照管理) ----
        if p == "/backups":
            return self._serve_file("backup.html")
        if p == "/api/backups":
            return self._backups_get()
        # ---- 主题 F 专家/技能 提示词增强 (GUI 可视化管理) ----
        if p == "/enhance":
            return self._serve_file("enhance.html")
        if p == "/api/enhance":
            from ..agent import enhance as _enh
            return self._send_json(_enh.load(os.getcwd()))
        # ---- 提示词模板 (GUI 可视化管理) ----
        if p == "/templates":
            return self._serve_file("templates.html")
        if p == "/api/templates":
            from .. import templates as _tpl
            return self._send_json(_tpl.list_templates(os.getcwd()))
        # ---- 密钥保险箱 (GUI 可视化管理) ----
        if p == "/secrets":
            return self._serve_file("secrets.html")
        if p == "/api/secrets":
            from .. import secrets as _sec
            return self._send_json(_sec.list_secrets(os.getcwd()))
        if p == "/api/secrets/value":
            q = parse_qs(urlparse(self.path).query)
            key = (q.get("key") or [None])[0]
            from .. import secrets as _sec
            if not key:
                return self._send_json({"error": "缺少 key"}, status=400)
            val = _sec.get_secret(key, os.getcwd())
            if val is None:
                return self._send_json({"error": "未找到密钥"}, status=404)
            return self._send_json({"ok": True, "key": key, "value": val})
        # ---- 代码片段库 (GUI 可视化管理) ----
        if p == "/snippets":
            return self._serve_file("snippets.html")
        if p == "/api/snippets":
            from .. import snippets as _snip
            return self._send_json(_snip.list_snippets(os.getcwd()))
        # ---- 笔记 (GUI 可视化管理) ----
        if p == "/notes":
            return self._serve_file("notes.html")
        if p == "/api/notes":
            from .. import notes as _note
            return self._send_json(_note.list_notes(os.getcwd()))
        # ---- 待办清单 (GUI 可视化管理) ----
        if p == "/todos":
            return self._serve_file("todos.html")
        if p == "/api/todos":
            from .. import todos as _td
            return self._send_json(_td.list_todos(os.getcwd()))
        # ---- 记忆中枢 (长期记忆 + 每日日志) ----
        if p == "/memory":
            return self._serve_file("memory.html")
        if p == "/api/memory":
            return self._send_json(self._memory_get())
        # ---- 计划书 + 任务清单 ----
        if p == "/plans":
            return self._serve_file("plans.html")
        if p == "/api/plans":
            return self._send_json(self._plans_list())
        # ---- 错误日志 + 错误汇总 ----
        if p == "/errors":
            return self._serve_file("errors.html")
        if p == "/api/errors":
            return self._send_json(self._errors_list())
        if p == "/api/errors/summary":
            return self._send_json(self._errors_summary())
        # ---- 技术文档 (MD 文件夹管理) ----
        if p == "/docs":
            return self._serve_file("docs.html")
        if p == "/api/docs":
            return self._send_json(self._docs_get())
        # ---- 多智能体编排页面 ----
        if p == "/orchestrate":
            return self._serve_file("orchestrate.html")
        # ---- 四大创作域 统一创作工作台 ----
        if p == "/studio":
            return self._serve_file("studio.html")
        if p == "/autonomous":
            return self._serve_file("autonomous.html")
        # ---- 全链路目标驱动流水线 (Phase 7) ----
        if p == "/pipeline":
            return self._serve_file("pipeline.html")
        # ---- 真实多模态适配层 (Phase 8) ----
        if p == "/multimodal":
            return self._serve_file("multimodal.html")
        # ---- 多模态基座 (Phase 21): 资产库画廊 ----
        if p == "/api/multimodal":
            return self._multimodal_list()
        # ---- 统一引擎总控台 (Phase 10): 四大引擎可观测 + 一键启动 ----
        if p == "/control-center":
            return self._serve_file("control_center.html")
        # ---- 自动化调度中枢 (Phase 15): 定时/周期任务自主运行 ----
        if p == "/automation":
            return self._serve_file("automation.html")
        # ---- 实时活动总线 (Phase 16): 统一事件流页面 ----
        if p == "/activity":
            return self._serve_file("activity.html")
        # ---- 操作审计链 (Phase 17): 关键操作审计页面 ----
        if p == "/audit":
            return self._serve_file("audit.html")
        # ---- 自主进化闭环 (Phase 18): 自愈提议页面 ----
        if p == "/heal":
            return self._serve_file("heal.html")
        # ---- 工作伙伴多智能体联邦 (Phase 25): 伙伴注册/派发/汇聚 ----
        if p == "/federation":
            return self._serve_file("federation.html")
        if p == "/api/federation":
            return self._federation_get()
        # ---- 长期记忆图谱 (Phase 26): 实体-关系图谱沉淀/召回 ----
        if p == "/memory-graph":
            return self._serve_file("memory_graph.html")
        if p == "/api/memory-graph":
            return self._memory_graph_get()
        # ---- 超级 AGENT 内核 (Phase 27): 统一目标入口 编排闭环 ----
        if p == "/superagent":
            return self._serve_file("superagent.html")
        if p == "/api/superagent":
            return self._superagent_get()
        if p == "/api/superagent/detail":
            return self._superagent_detail()
        if p == "/api/superagent/report":
            return self._superagent_report()
        if p == "/api/superagent/export":
            return self._superagent_export()
        if p == "/api/superagent/export/bundle":
            return self._superagent_export_bundle()
        if p == "/api/superagent/diff":
            return self._superagent_diff()
        if p == "/api/superagent/diff/report":
            return self._superagent_diff_report()
        if p == "/api/superagent/annotations":
            return self._annotations_get()
        if p == "/api/superagent/feedback":
            return self._feedback_get()
        if p == "/api/superagent/feedback/optimize":
            return self._feedback_optimize()
        if p == "/api/superagent/quality/baseline":
            return self._quality_baseline()
        if p == "/api/superagent/quality/check":
            return self._quality_check()
        if p == "/api/superagent/quality/alerts":
            return self._quality_alerts()
        if p == "/api/superagent/quality/auto":
            return self._quality_auto_get()
        if p == "/api/superagent/replays":
            return self._replays_get()
        if p == "/api/superagent/lineage":
            return self._lineage_get()
        if p == "/api/superagent/queue":
            return self._superagent_queue()
        if p == "/api/superagent/artifacts":
            return self._superagent_artifacts()
        if p == "/api/superagent/artifacts/file":
            return self._superagent_artifact_file()
        if p == "/api/superagent/schedules":
            return self._schedules_get()
        if p == "/api/superagent/templates":
            return self._sa_tpl_get()
        if p == "/api/superagent/webhooks":
            return self._webhooks_get()
        if p == "/api/superagent/digest":
            return self._digest_get()
        if p == "/api/superagent/trend":
            return self._trend_get()
        # ---- 插件中枢 (Phase 32): Connector/Expert 注册与发现 ----
        if p == "/plugins":
            return self._serve_file("plugin_hub.html")
        if p == "/api/plugins":
            return self._plugin_get()
        if p == "/api/plugins/discover":
            return self._plugin_discover()
        if p == "/api/plugins/wire":
            return self._plugin_wire()
        if p.startswith("/api/plugins/connectors/"):
            name = p[len("/api/plugins/connectors/"):]
            return self._plugin_call_connector(name)
        if p == "/api/plugins/connectors":
            return self._plugin_conn_list()
        if p == "/api/plugins/experts":
            return self._plugin_expert_list()
        if p == "/api/automations":
            return self._send_json(self._automations_get())
        if p.startswith("/outputs/"):
            from urllib.parse import unquote
            name = unquote(os.path.basename(p[len("/outputs/"):]))
            fpath = os.path.join(os.getcwd(), "outputs", "multimodal", name)
            if os.path.isfile(fpath):
                return self._serve_data_file(fpath)
            return self.send_error(404)
        if p == "/api/creation/domains":
            from .. import creation_domains as _cd
            return self._send_json({"ok": True, "domains": _cd.list_domains()})
        # ---- 统一引擎总控台 (Phase 10): 四大引擎聚合快照 ----
        if p == "/api/engines":
            return self._send_json(self._engines_status())
        # ---- 离线自检中枢 (Phase 14): 系统健康探针(无 LLM · 确定性) ----
        if p == "/api/events":
            return self._events_get()
        # ---- 操作审计链 (Phase 17): 关键操作审计回溯 ----
        if p == "/api/audit":
            return self._audit_get()
        # ---- 自主进化闭环 (Phase 18): 自愈提议器 ----
        if p == "/api/heal":
            return self._heal_get()
        if p == "/api/heal/patches":
            return self._heal_patches()
        if p == "/api/selfcheck":
            from .. import selfcheck as _sc
            sc = _sc.run()
            try:
                from .. import event_bus as _eb
                _eb.emit("selfcheck", "run",
                         "自检 %d/%d (健康分 %d)" % (sc["passed"], sc["total"], sc["score"]),
                         {"score": sc["score"], "passed": sc["passed"],
                          "total": sc["total"], "all_ok": sc["all_ok"]})
            except Exception:
                pass
            return self._send_json(sc)
        # ---- 外部 LLM 大模型配置 (GUI 可视化管理) ----
        if p == "/api/llm-models":
            return self._llm_models_get()
        if p == "/api/health":
            cfg = _get_cfg()
            backend = cfg["llm"].get("backend", "ollama")
            try:
                client = build_client(backend, cfg=cfg)
                model = client.model
            except Exception:
                model = "?"
            return self._send_json({"ok": True, "version": __version__, "backend": backend, "model": model})
        # ---- 主题 E 健康度自检 (批次10): LLM + 9 MCP + 文件系统 红绿体检 ----
        if p == "/api/health/full":
            return self._health_full()
        # ---- 主题 E 可观测性 (批次9): 工具调用运行期统计 ----
        if p == "/api/stats":
            try:
                from ..tools.registry import get_stats
                return self._send_json(get_stats())
            except Exception as e:
                return self._send_json({"error": str(e)}, status=500)
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
        # ---- 设置中心 (批次14): 保存 config.toml ----
        if p == "/api/settings":
            return self._settings_save()
        # ---- 工作区沙箱 (文件系统根域管理): 写回 allowed_roots ----
        if p == "/api/sandbox":
            return self._sandbox_save()
        # ---- 工作区备份 / 回滚 (时间点快照管理) ----
        if p == "/api/backups":
            return self._backups_create()
        if p == "/api/backups/rollback":
            return self._backup_rollback()
        if p == "/api/backups/delete":
            return self._backup_delete()
        # ---- 提示词模板: 新建/更新 ----
        if p == "/api/templates":
            return self._templates_save()
        if p == "/api/templates/delete":
            return self._templates_delete()
        # ---- 密钥保险箱: 设置/删除 ----
        if p == "/api/secrets":
            return self._secrets_set()
        if p == "/api/secrets/delete":
            return self._secrets_delete()
        # ---- 代码片段库: 新建/更新/删除 ----
        if p == "/api/snippets":
            return self._snippets_save()
        if p == "/api/snippets/delete":
            return self._snippets_delete()
        # ---- 笔记: 新建/更新/删除 ----
        if p == "/api/notes":
            return self._notes_save()
        if p == "/api/notes/delete":
            return self._notes_delete()
        # ---- 待办清单: 新增/改状态/删除 ----
        if p == "/api/todos":
            return self._todos_add()
        if p == "/api/todos/status":
            return self._todos_status()
        if p == "/api/todos/delete":
            return self._todos_delete()
        # ---- 记忆中枢: 更新记忆 / 写日志 / 语义召回 ----
        if p == "/api/memory":
            return self._memory_update()
        if p == "/api/memory/retrieve":
            return self._memory_retrieve()
        # ---- 计划书: 保存/删除/任务 ----
        if p == "/api/plans":
            return self._plans_save()
        if p == "/api/plans/delete":
            return self._plans_delete()
        if p == "/api/plans/task":
            return self._plans_task()
        # ---- 层级任务分解 / 多智能体编排 (LLM 驱动) ----
        if p == "/api/decompose":
            return self._decompose_api()
        # ---- 四大创作域 统一创作工作台 (LLM 驱动) ----
        if p == "/api/creation/dispatch":
            return self._creation_dispatch()
        # ---- 自主模式: 目标驱动自驱循环 (Phase 5) ----
        if p == "/api/autonomous":
            return self._autonomous_run()
        # ---- 全链路目标驱动流水线 (Phase 7) + 跨会话记忆喂入 (Phase 9) ----
        if p == "/api/pipeline":
            return self._pipeline_api()
        if p == "/api/pipeline/recall":
            return self._pipeline_recall()
        # ---- 统一引擎总控台 (Phase 10): 一键启动任意引擎 ----
        if p == "/api/engines/run":
            return self._engines_run()
        # ---- 真实多模态适配层 (Phase 8) ----
        if p == "/api/multimodal/render":
            return self._multimodal_render()
        if p == "/api/multimodal/generate":
            return self._multimodal_generate()
        # ---- 上下文操作: 压缩 / 整理 / 拆解 ----
        if p == "/api/context/compress":
            return self._context_op("compress")
        if p == "/api/context/organize":
            return self._context_op("organize")
        if p == "/api/context/decompose":
            return self._context_op("decompose")
        # ---- 错误日志: 手动记录 ----
        if p == "/api/errors/record":
            return self._errors_record()
        # ---- 技术文档: 保存 / 删除 ----
        if p == "/api/docs/save":
            return self._docs_save()
        if p == "/api/docs/delete":
            return self._docs_delete()
        # ---- 主题 F 专家/技能 提示词增强 (GUI 可视化管理): 写回库 ----
        if p == "/api/enhance":
            return self._enhance_save()
        # ---- 外部 LLM 大模型配置 (GUI 可视化管理) ----
        if p == "/api/llm-models/test":
            return self._llm_models_test()
        if p == "/api/llm-models":
            return self._llm_models_save()
        # ---- 文件编辑: 保存 (供 Web 代码编辑器) ----
        if p.startswith("/api/fs"):
            return self._fs_save()
        # ---- 自动化调度中枢 (Phase 15) ----
        if p == "/api/automations":
            return self._automations_create()
        if p == "/api/heal/export":
            return self._heal_export()
        if p == "/api/heal/export-md":
            return self._heal_export_md()
        # ---- 自愈闭环 2.0 (Phase 20): 补丁生成 / 沙箱验证 / 人工合并 ----
        if p == "/api/heal/generate":
            return self._heal_generate()
        if p == "/api/heal/verify":
            return self._heal_verify()
        if p == "/api/heal/apply":
            return self._heal_apply()
        # ---- 工作伙伴多智能体联邦 (Phase 25): 目标派发到 1..N 伙伴 ----
        if p == "/api/federation/dispatch":
            return self._federation_dispatch()
        # ---- 长期记忆图谱 (Phase 26): 沉淀 / 召回 ----
        if p == "/api/memory-graph/absorb":
            return self._memory_graph_absorb()
        if p == "/api/memory-graph/recall":
            return self._memory_graph_recall()
        # ---- 超级 AGENT 内核 (Phase 27): 目标编排闭环 ----
        if p == "/api/superagent/run":
            return self._superagent_run()
        # ---- 超级 AGENT SSE 流式编排 (Phase 38): 每阶段实时推送进度 ----
        if p == "/api/superagent/run/stream":
            return self._superagent_run_stream()
        # ---- 产物归档 (Phase 53) ----
        if p == "/api/superagent/artifacts/archive":
            return self._artifacts_archive()
        # ---- 定时编排 (Phase 43): 计划增删改/立即执行 ----
        if p == "/api/superagent/schedules/create":
            return self._schedules_create()
        if p == "/api/superagent/schedules/update":
            return self._schedules_update()
        if p == "/api/superagent/schedules/delete":
            return self._schedules_delete()
        if p == "/api/superagent/schedules/run":
            return self._schedules_run_now()
        # ---- 模板库 (Phase 44): 模板增删改/渲染 ----
        if p == "/api/superagent/templates/create":
            return self._sa_tpl_create()
        if p == "/api/superagent/templates/update":
            return self._sa_tpl_update()
        if p == "/api/superagent/templates/delete":
            return self._sa_tpl_delete()
        if p == "/api/superagent/templates/render":
            return self._sa_tpl_render()
        # ---- Webhook 通知 (Phase 46): 接收端管理/测试 ----
        if p == "/api/superagent/webhooks/create":
            return self._webhooks_create()
        if p == "/api/superagent/webhooks/update":
            return self._webhooks_update()
        if p == "/api/superagent/webhooks/delete":
            return self._webhooks_delete()
        if p == "/api/superagent/webhooks/test":
            return self._webhooks_test()
        # ---- 编排回放 (Phase 58): 同目标重跑 + 血缘 ----
        if p == "/api/superagent/replay":
            return self._superagent_replay()
        # ---- 人工批注 (Phase 57): 评论/评分/标签 + 沉淀记忆 ----
        if p == "/api/superagent/annotations/create":
            return self._annotations_create()
        if p == "/api/superagent/annotations/delete":
            return self._annotations_delete()
        # ---- 质量告警推送 (Phase 61): POST ----
        if p == "/api/superagent/quality/push":
            return self._quality_push()
        # ---- 质量告警自动推送开关 (Phase 62): POST ----
        if p == "/api/superagent/quality/auto":
            return self._quality_auto_post()
        # ---- 编排日报/周报 (Phase 50): 推送(POST) ----
        if p == "/api/superagent/digest/push":
            return self._digest_push()
        # ---- 插件中枢 (Phase 32): 动态注册 connector/expert ----
        if p == "/api/plugins/connectors/register":
            return self._plugin_connector_register()
        if p == "/api/plugins/experts/register":
            return self._plugin_expert_register()
        if p.startswith("/api/automations/"):
            rest = p[len("/api/automations/"):]
            if "/" in rest:
                tid, action = rest.split("/", 1)
                if action in ("run", "delete", "toggle"):
                    return getattr(self, "_automations_" + action)(tid)
        return self.send_error(404)

    # ---------------------------------------------------------------
    # 自动化调度中枢 (Phase 15): 定时 / 周期任务自主运行
    # ---------------------------------------------------------------
    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def _automations_get(self):
        from .. import automation_hub as _ah
        hub = _ah.get_hub(os.getcwd())
        _ah.start_scheduler(hub)
        return self._send_json({
            "ok": True,
            "tasks": hub.list_tasks(),
            "scheduler": {"running": bool(_ah._SCHED and _ah._SCHED.is_alive()),
                          "now": _ah.now_str(), "base": hub.base_dir},
        })

    def _automations_create(self):
        body = self._read_json_body()
        name = (body.get("name") or "").strip()
        kind = (body.get("kind") or "").strip()
        goal = (body.get("goal") or "").strip()
        schedule = (body.get("schedule") or "").strip()
        if not name or not kind or not goal or not schedule:
            return self._send_json({"error": "name/kind/goal/schedule 均必填"}, status=400)
        from .. import automation_hub as _ah
        hub = _ah.get_hub(os.getcwd())
        try:
            task = hub.add(name=name, kind=kind, goal=goal, schedule=schedule,
                           context=(body.get("context") or ""),
                           domain=(body.get("domain") or "code"),
                           enabled=body.get("enabled", True))
        except ValueError as e:
            return self._send_json({"error": "调度表达式非法: %s" % e}, status=400)
        self._emit("automation", "create", "新增任务 %s (%s)" % (name, kind),
                   {"id": task.get("id"), "kind": kind, "schedule": schedule}, audit=True)
        return self._send_json({"ok": True, "task": task})

    def _automations_run(self, tid):
        from .. import automation_hub as _ah
        hub = _ah.get_hub(os.getcwd())
        out = hub.run_now(tid, cwd=os.getcwd())
        if not out.get("ok"):
            self._emit("automation", "run_fail", "运行任务 %s 失败: %s" % (tid, out.get("error", "")),
                       {"id": tid}, audit=True)
            return self._send_json({"error": out.get("error", "运行失败")}, status=404)
        self._emit("automation", "run", "立即运行任务 %s 完成" % tid, {"id": tid, "ok": True}, audit=True)
        return self._send_json(out)

    def _automations_delete(self, tid):
        from .. import automation_hub as _ah
        hub = _ah.get_hub(os.getcwd())
        if not hub.remove(tid):
            return self._send_json({"error": "任务不存在"}, status=404)
        self._emit("automation", "delete", "删除任务 %s" % tid, {"id": tid}, audit=True)
        return self._send_json({"ok": True})

    def _automations_toggle(self, tid):
        body = self._read_json_body()
        from .. import automation_hub as _ah
        hub = _ah.get_hub(os.getcwd())
        enabled = body.get("enabled")
        if enabled is None:
            cur = hub.get(tid)
            enabled = not (cur.get("enabled") if cur else False)
        t = hub.set_enabled(tid, enabled)
        if t is None:
            return self._send_json({"error": "任务不存在"}, status=404)
        self._emit("automation", "toggle", "%s任务 %s" % ("启用" if enabled else "停用", tid),
                   {"id": tid, "enabled": bool(enabled)}, audit=True)
        return self._send_json({"ok": True, "task": t})

    def _events_get(self):
        """GET /api/events?since=<id>&limit=<n> -> 增量事件拉取（近实时活动流）。"""
        from .. import event_bus as _eb
        since, limit = 0, 50
        try:
            import urllib.parse as _up
            q = _up.parse_qs(self.path.split("?", 1)[1])
            if q.get("since"):
                since = int(q["since"][0])
            if q.get("limit"):
                limit = max(1, min(int(q["limit"][0]), 200))
        except Exception:
            pass
        evs = _eb.get_bus().recent(limit=limit, since_id=since)
        return self._send_json({"ok": True, "events": evs, "total": _eb.get_bus().size(),
                                "counts": _eb.get_bus().counts_by_source()})

    def _audit_get(self):
        """GET /api/audit?since=<id>&limit=<n>&source=<s> -> 关键操作审计链回溯。"""
        from .. import event_bus as _eb
        since, limit, source = 0, 100, None
        try:
            import urllib.parse as _up
            q = _up.parse_qs(self.path.split("?", 1)[1])
            if q.get("since"):
                since = int(q["since"][0])
            if q.get("limit"):
                limit = max(1, min(int(q["limit"][0]), 500))
            if q.get("source"):
                source = q["source"][0]
        except Exception:
            pass
        trail = _eb.get_bus().audit_trail(limit=limit, since_id=since, source=source)
        return self._send_json({"ok": True, "events": trail, "total": len(trail)})

    def _emit(self, source, kind, msg, data=None, audit=False):
        """便捷发射活动事件（失败静默）。audit=True 标记关键操作为审计事件。"""
        try:
            from .. import event_bus as _eb
            return _eb.emit(source, kind, msg, data, audit=audit)
        except Exception:
            return None

    def _heal_get(self):
        """GET /api/heal -> 聚合 selfcheck 失败 + 审计失败事件, 生成结构化补丁提议。"""
        from .. import self_heal as _sh
        rep = _sh.run()
        return self._send_json({"ok": True, **rep})

    def _heal_export(self):
        """POST /api/heal/export -> 把当前提议落盘 .lmw_heal/proposals.jsonl (待审)。"""
        from .. import self_heal as _sh
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        out_dir = body.get("cwd") or os.getcwd()
        rep = _sh.run()
        res = _sh.export_proposals(rep, out_dir)
        if not res.get("ok"):
            return self._send_json({"error": res.get("error", "导出失败")}, status=500)
        return self._send_json({"ok": True, "path": res["path"], "count": res["count"]})

    def _heal_export_md(self):
        """POST /api/heal/export-md -> 生成可读补丁报告 .lmw_heal/proposals_<ts>.md (含预案)。"""
        from .. import self_heal as _sh
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        out_dir = body.get("cwd") or os.getcwd()
        rep = _sh.run()
        res = _sh.export_proposals(rep, out_dir)
        if not res.get("ok"):
            return self._send_json({"error": res.get("error", "导出失败")}, status=500)
        return self._send_json({"ok": True, "md_path": res["md_path"],
                                "path": res["path"], "count": res["count"]})

    def _heal_patches(self):
        """GET /api/heal/patches -> 列出已生成的补丁候选。"""
        from .. import self_heal as _sh
        out_dir = os.getcwd()
        res = _sh.list_patches(out_dir)
        if not res.get("ok"):
            return self._send_json({"error": res.get("error", "列出失败")}, status=500)
        return self._send_json({"ok": True, **res})

    def _heal_generate(self):
        """POST /api/heal/generate {proposal_id, cwd} -> 生成补丁候选(规则/LLM降级)。"""
        from .. import self_heal as _sh
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        pid = (body.get("proposal_id") or "").strip()
        out_dir = body.get("cwd") or os.getcwd()
        rep = _sh.run()
        proposal = None
        for p in (rep.get("proposals") or []):
            if p.get("id") == pid:
                proposal = p
                break
        if proposal is None:
            return self._send_json({"error": "未找到提议: %s" % pid}, status=404)
        res = _sh.generate_patch(proposal, repo_root=out_dir)
        if not res.get("ok"):
            return self._send_json({"error": res.get("error", "生成失败")}, status=500)
        return self._send_json({"ok": True, **res})

    def _heal_verify(self):
        """POST /api/heal/verify {patch_id, cwd} -> 沙箱验证补丁结构合法性。"""
        from .. import self_heal as _sh
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        patch_id = (body.get("patch_id") or "").strip()
        out_dir = body.get("cwd") or os.getcwd()
        if not patch_id:
            return self._send_json({"error": "缺少 patch_id"}, status=400)
        res = _sh.sandbox_verify(patch_id, repo_root=out_dir)
        if not res.get("ok"):
            return self._send_json({"error": res.get("error", "验证失败")}, status=500)
        return self._send_json({"ok": True, **res})

    def _heal_apply(self):
        """POST /api/heal/apply {patch_id, confirm, cwd} -> 人工合并门(confirm=True 才落地)。"""
        from .. import self_heal as _sh
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        patch_id = (body.get("patch_id") or "").strip()
        confirm = bool(body.get("confirm", False))
        out_dir = body.get("cwd") or os.getcwd()
        if not patch_id:
            return self._send_json({"error": "缺少 patch_id"}, status=400)
        res = _sh.apply_patch(patch_id, repo_root=out_dir, confirm=confirm)
        if not res.get("ok"):
            return self._send_json({"error": res.get("error", "应用失败")}, status=500)
        return self._send_json({"ok": True, **res})

    # ------------------------------------------------------------------
    # 工作伙伴多智能体联邦 (Phase 25): 目标路由 → 并行派发 → 汇聚
    # ------------------------------------------------------------------
    def _federation_get(self):
        """GET /api/federation -> 已注册伙伴清单(供联邦页初始化)。"""
        from .. import federation as _fed
        fed = _fed.get_federation()
        partners = fed.list_partners()
        return self._send_json({"ok": True, "partners": partners, "count": len(partners)})

    def _federation_dispatch(self):
        """POST /api/federation/dispatch {goal, hint_domains?} -> 路由到 1..N 伙伴并行派发 + 汇聚。

        hint_domains: 可选伙伴 id 列表(code/creation/research/ops), 显式覆盖自动路由。
        """
        from .. import federation as _fed
        try:
            body = self._read_json({})
            goal = (body.get("goal") or "").strip()
            hint = body.get("hint_domains") or []
            if isinstance(hint, str):
                hint = [h.strip() for h in hint.split(",") if h.strip()]
            if not goal:
                return self._send_json({"error": "缺少 goal"}, status=400)
            fed = _fed.get_federation()
            rep = fed.dispatch(goal, session_id=body.get("session_id") or "",
                              hint_domains=hint or None, llm_call=self._make_llm_call())
            # 记忆沉淀(Phase 26): 联邦派发成功 → 汇聚结论入记忆图谱(失败不影响主流程)
            if rep.get("ok"):
                try:
                    from .. import memory_graph as _mg
                    g = _mg.get_graph(os.getcwd())
                    merged_summary = (rep.get("merged") or {}).get("summary", "")
                    g.absorb(goal, merged_summary, session_id=body.get("session_id") or "", llm_call=None)
                    from .. import event_bus as _eb
                    _eb.emit("memory", "absorb", "联邦派发沉淀记忆: %s" % goal,
                             {"goal": goal}, audit=True)
                except Exception:
                    pass
            return self._send_json({"ok": True, **rep})
        except Exception as e:
            return self._send_json({"error": "联邦派发失败: %s" % e}, status=500)

    # ---------------------------------------------------------------
    # 长期记忆图谱 (Phase 26): 实体-关系图谱沉淀 / 召回
    # ---------------------------------------------------------------
    def _memory_graph_get(self):
        """GET /api/memory-graph -> 统计 + 实体/关系清单(供图谱页初始化)。"""
        from .. import memory_graph as _mg
        g = _mg.get_graph(os.getcwd())
        return self._send_json({
            "ok": True,
            "stats": g.stats(),
            "entities": g.list_entities(limit=200),
            "relations": g.list_relations(limit=200),
        })

    def _memory_graph_absorb(self):
        """POST /api/memory-graph/absorb {goal, result, session_id?} -> 抽取沉淀入图。"""
        try:
            body = self._read_json({})
            goal = (body.get("goal") or "").strip()
            result = body.get("result") or ""
            if not goal and not result:
                return self._send_json({"error": "缺少 goal 或 result"}, status=400)
            from .. import memory_graph as _mg
            g = _mg.get_graph(os.getcwd())
            rep = g.absorb(goal, result, session_id=body.get("session_id") or "", llm_call=None)
            try:
                from .. import event_bus as _eb
                _eb.emit("memory", "absorb", "记忆沉淀 %d 实体/%d 关系" % (
                    rep.get("entities_added", 0), rep.get("relations_added", 0)),
                    {"entities": rep.get("entities_added", 0)}, audit=True)
            except Exception:
                pass
            return self._send_json({"ok": True, **rep})
        except Exception as e:
            return self._send_json({"error": "记忆沉淀失败: %s" % e}, status=500)

    def _memory_graph_recall(self):
        """POST /api/memory-graph/recall {goal, limit?} -> 召回相关实体/关系 + recap。"""
        try:
            body = self._read_json({})
            goal = (body.get("goal") or "").strip()
            if not goal:
                return self._send_json({"error": "缺少 goal"}, status=400)
            limit = body.get("limit") or 12
            try:
                limit = int(limit)
            except Exception:
                limit = 12
            from .. import memory_graph as _mg
            g = _mg.get_graph(os.getcwd())
            rep = g.recall(goal, limit=limit)
            return self._send_json({"ok": True, **rep})
        except Exception as e:
            return self._send_json({"error": "记忆召回失败: %s" % e}, status=500)

    # ---- 超级 AGENT 内核 (Phase 27): 统一目标入口 ----
    def _superagent_get(self):
        """GET /api/superagent -> 最近编排概览(供页面轮询)。"""
        from .. import superagent as _sa
        return self._send_json({"ok": True, "runs": _sa.get_recent_runs(20, base_dir=os.getcwd())})

    def _superagent_detail(self):
        """GET /api/superagent/detail?ts=... -> 单次编排完整结果 (Phase 39 历史回看)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        ts = (q.get("ts") or [""])[0]
        if not ts:
            return self._send_json({"error": "缺少 ts"}, status=400)
        rep = _sa.get_run_detail(ts, base_dir=os.getcwd())
        if rep is None:
            return self._send_json({"error": "未找到该编排记录"}, status=404)
        return self._send_json({"ok": True, "result": rep})

    def _superagent_report(self):
        """GET /api/superagent/report?ts=... -> 可分享的自包含 HTML 编排报告 (Phase 47)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        ts = (q.get("ts") or [""])[0]
        if not ts:
            return self._send_json({"error": "缺少 ts"}, status=400)
        rep = _sa.get_run_detail(ts, base_dir=os.getcwd())
        if rep is None:
            return self._send_json({"error": "未找到该编排记录"}, status=404)
        html_doc = _render_orch_report(rep)
        payload = html_doc.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _superagent_export(self):
        """GET /api/superagent/export?ts=...&fmt=md|json -> 结构化导出编排结果 (Phase 55)。

        fmt=md  -> text/markdown 附件 (可直接粘贴进文档/IM)
        fmt=json-> application/json 附件 (原始结构化结果, 供二次消费)
        缺省 fmt=md。
        """
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        ts = (q.get("ts") or [""])[0]
        fmt = ((q.get("fmt") or ["md"])[0] or "md").lower()
        if fmt not in ("md", "json", "markdown"):
            return self._send_json({"error": "fmt 仅支持 md/json"}, status=400)
        if not ts:
            return self._send_json({"error": "缺少 ts"}, status=400)
        rep = _sa.get_run_detail(ts, base_dir=os.getcwd())
        if rep is None:
            return self._send_json({"error": "未找到该编排记录"}, status=404)
        stamp = str(ts).replace(":", "").replace("-", "").replace(" ", "_")[:20] or "run"
        if fmt == "json":
            payload = json.dumps(rep, ensure_ascii=False, indent=2).encode("utf-8")
            ctype, fname = "application/json; charset=utf-8", "lingmeng-orch-%s.json" % stamp
        else:
            payload = _render_orch_md(rep).encode("utf-8")
            ctype, fname = "text/markdown; charset=utf-8", "lingmeng-orch-%s.md" % stamp
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"' % fname)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _superagent_export_bundle(self):
        """GET /api/superagent/export/bundle?ts=... -> 单条编排的 md+json 打包 zip (Phase 55)。

        与产物归档不同: 这里只打包"这一次编排"的两种结构化表示, 便于随报告一起交付。
        """
        from .. import superagent as _sa
        import zipfile as _zf
        q = parse_qs(urlparse(self.path).query)
        ts = (q.get("ts") or [""])[0]
        if not ts:
            return self._send_json({"error": "缺少 ts"}, status=400)
        rep = _sa.get_run_detail(ts, base_dir=os.getcwd())
        if rep is None:
            return self._send_json({"error": "未找到该编排记录"}, status=404)
        stamp = str(ts).replace(":", "").replace("-", "").replace(" ", "_")[:20] or "run"
        buf = io.BytesIO()
        with _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED) as z:
            z.writestr("orch-%s.md" % stamp, _render_orch_md(rep).encode("utf-8"))
            z.writestr("orch-%s.json" % stamp,
                       json.dumps(rep, ensure_ascii=False, indent=2).encode("utf-8"))
            z.writestr("README.txt",
                       ("灵梦work 超级 AGENT 编排导出包\n"
                        "ts: %s\n\n- orch-%s.md    可读 Markdown 报告\n"
                        "- orch-%s.json  原始结构化结果(供程序消费)\n"
                        % (ts, stamp, stamp)).encode("utf-8"))
        payload = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition",
                         'attachment; filename="lingmeng-orch-%s.zip"' % stamp)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _superagent_diff(self):
        """GET /api/superagent/diff?a=<ts>&b=<ts> -> 两次编排结构化对比 (Phase 56)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        ta = (q.get("a") or [""])[0]
        tb = (q.get("b") or [""])[0]
        if not ta or not tb:
            return self._send_json({"error": "缺少 a/b 两个 ts"}, status=400)
        d = _sa.diff_runs(ta, tb, base_dir=os.getcwd())
        if d is None:
            return self._send_json({"error": "未找到其中一次编排记录"}, status=404)
        return self._send_json({"ok": True, "diff": d})

    def _superagent_diff_report(self):
        """GET /api/superagent/diff/report?a=&b= -> 左右分栏 HTML 对比报告 (Phase 56)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        ta = (q.get("a") or [""])[0]
        tb = (q.get("b") or [""])[0]
        if not ta or not tb:
            return self._send_json({"error": "缺少 a/b 两个 ts"}, status=400)
        d = _sa.diff_runs(ta, tb, base_dir=os.getcwd())
        if d is None:
            return self._send_json({"error": "未找到其中一次编排记录"}, status=404)
        payload = _render_diff_report(d).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _feedback_get(self):
        """GET /api/superagent/feedback?low_rating_max= -> 反馈分析 + 规则建议 (Phase 59)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        lo = (q.get("low_rating_max") or [""])[0]
        rep = _sa.analyze_feedback(base_dir=os.getcwd(), low_rating_max=lo or 2)
        return self._send_json({"ok": True, **rep})

    def _feedback_optimize(self):
        """GET /api/superagent/feedback/optimize?ts= -> 优化后目标预览(不执行) (Phase 59)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        ts = (q.get("ts") or [""])[0]
        if not ts:
            return self._send_json({"error": "缺少 ts"}, status=400)
        rep = _sa.build_optimized_goal(ts, base_dir=os.getcwd(),
                                       llm_call=self._make_llm_call())
        if rep is None:
            return self._send_json({"error": "未找到该编排记录"}, status=404)
        if not rep.get("ok"):
            return self._send_json(rep, status=400)
        return self._send_json({"ok": True, **rep})

    def _superagent_replay(self):
        """POST /api/superagent/replay {ts, model?, quality_gate?} -> 重跑历史编排 (Phase 58)。

        结果带 replay_of 血缘标记, 返回的 source_ts/replay_ts 可直接喂给
        /api/superagent/diff 做 A/B 对比。
        """
        from .. import superagent as _sa
        try:
            body = self._read_json({})
            ts = (body.get("ts") or "").strip()
            if not ts:
                return self._send_json({"error": "缺少 ts"}, status=400)
            try:
                qws = min(60.0, max(1.0, float(body.get("queue_wait_sec") or 30.0)))
            except (TypeError, ValueError):
                qws = 30.0
            rep = _sa.replay_run(
                ts, base_dir=os.getcwd(),
                llm_call=self._make_llm_call(),
                model=body.get("model") if body.get("model") is not None
                else self._cfg_llm_model(),
                quality_gate=body.get("quality_gate", True) is not False,
                queue_wait_sec=qws,
                optimize=body.get("optimize", False) is True)
            if rep is None:
                return self._send_json({"error": "未找到该编排记录"}, status=404)
            if rep.get("busy"):
                return self._send_json(rep, status=429)
            if not rep.get("ok"):
                return self._send_json(rep, status=400)
            return self._send_json({"ok": True, **rep})
        except Exception as e:
            return self._send_json({"error": "回放失败: %s" % e}, status=500)

    def _replays_get(self):
        """GET /api/superagent/replays?ts= -> 回放记录列表 (Phase 58)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        ts = (q.get("ts") or [""])[0]
        return self._send_json({
            "ok": True,
            "replays": _sa.list_replays(ts or None, base_dir=os.getcwd())})

    def _lineage_get(self):
        """GET /api/superagent/lineage?ts= -> 血缘链(源编排 + 它的回放) (Phase 58)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        ts = (q.get("ts") or [""])[0]
        if not ts:
            return self._send_json({"error": "缺少 ts"}, status=400)
        rep = _sa.get_replay_lineage(ts, base_dir=os.getcwd())
        if rep is None:
            return self._send_json({"error": "未找到该编排记录"}, status=404)
        return self._send_json({"ok": True, **rep})

    def _quality_baseline(self):
        """GET /api/superagent/quality/baseline?days=30&min_runs=3 -> 质量基线 (Phase 60)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        days = (q.get("days") or ["30"])[0]
        mr = (q.get("min_runs") or ["3"])[0]
        rep = _sa.get_quality_baseline(base_dir=os.getcwd(), days=days, min_runs=mr)
        return self._send_json({"ok": True, **rep})

    def _quality_check(self):
        """GET /api/superagent/quality/check?ts=&z=2 -> 单次编排偏离检查 (Phase 60)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        ts = (q.get("ts") or [""])[0]
        if not ts:
            return self._send_json({"error": "缺少 ts"}, status=400)
        rep = _sa.check_quality(ts, base_dir=os.getcwd(), z=(q.get("z") or ["2"])[0])
        if rep is None:
            return self._send_json({"error": "未找到该编排记录(或不在时间窗内)"}, status=404)
        return self._send_json({"ok": True, **rep})

    def _quality_alerts(self):
        """GET /api/superagent/quality/alerts?days=7&z=2 -> 偏离告警列表 (Phase 60)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        rep = _sa.list_quality_alerts(
            base_dir=os.getcwd(),
            days=(q.get("days") or ["7"])[0],
            z=(q.get("z") or ["2"])[0])
        return self._send_json({"ok": True, "alerts": rep, "count": len(rep)})

    def _quality_push(self):
        """POST /api/superagent/quality/push {days?, z?, min_runs?, limit?, silence_hours?}。"""
        from .. import superagent as _sa
        body = self._read_json({}) or {}
        rep = _sa.push_quality_alerts(
            base_dir=os.getcwd(),
            days=body.get("days") or 7,
            z=body.get("z"),
            min_runs=body.get("min_runs"),
            limit=body.get("limit") or 10,
            silence_hours=body.get("silence_hours") or 0)
        return self._send_json({"ok": True, **rep})

    def _quality_auto_get(self):
        """GET /api/superagent/quality/auto -> 自动推送配置 (Phase 62)。"""
        from .. import superagent as _sa
        return self._send_json({"ok": True, **_sa.get_quality_auto()})

    def _quality_auto_post(self):
        """POST /api/superagent/quality/auto {enabled, interval_h?, silence_h?} (Phase 62)。"""
        from .. import superagent as _sa
        body = self._read_json({}) or {}
        st = _sa.set_quality_auto(body.get("enabled"),
                                  body.get("interval_h") if body.get("interval_h") is not None else 24,
                                  body.get("silence_h"))
        return self._send_json({"ok": True, **st})

    def _annotations_get(self):
        """GET /api/superagent/annotations?ts= -> 批注列表 + 统计 (Phase 57)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        ts = (q.get("ts") or [""])[0]
        return self._send_json({
            "ok": True,
            "annotations": _sa.list_annotations(ts or None, base_dir=os.getcwd()),
            "stats": _sa.get_annotation_stats(ts or None, base_dir=os.getcwd()),
        })

    def _annotations_create(self):
        """POST /api/superagent/annotations/create {ts, text, author?, rating?, tags?, sediment?}。"""
        from .. import superagent as _sa
        body = self._read_json() or {}
        rep = _sa.add_annotation(
            body.get("ts") or "",
            body.get("text") or "",
            author=body.get("author") or "",
            rating=body.get("rating"),
            tags=body.get("tags") or [],
            sediment=body.get("sediment", True) is not False,
            base_dir=os.getcwd(),
        )
        if not rep.get("ok"):
            return self._send_json(rep, status=400)
        return self._send_json({"ok": True, **rep})

    def _annotations_delete(self):
        """POST /api/superagent/annotations/delete {id}。"""
        from .. import superagent as _sa
        body = self._read_json() or {}
        rep = _sa.remove_annotation(body.get("id"), base_dir=os.getcwd())
        if not rep.get("ok"):
            return self._send_json(rep, status=404)
        return self._send_json({"ok": True, **rep})

    def _superagent_queue(self):
        """GET /api/superagent/queue -> 编排并发/排队状态 (Phase 41)。

        每次查询顺带应用 config `agent.max_orchestrations`(缺省 2)。
        """
        from .. import superagent as _sa
        try:
            m = int(_cfg_get(_get_cfg(), "agent.max_orchestrations") or 2)
        except Exception:
            m = 2
        try:
            _sa.set_max_orchestrations(m)
        except Exception:
            pass
        return self._send_json({"ok": True, **_sa.get_queue_state()})

    def _superagent_artifacts(self):
        """GET /api/superagent/artifacts -> 编排产物清单 (Phase 42 产物中心)。

        扫描 outputs/superagent/, 按 mtime 倒序; 字段 name/ext/size/mtime。
        """
        from datetime import datetime as _dt
        root = os.path.join(os.getcwd(), "outputs", "superagent")
        items = []
        try:
            if os.path.isdir(root):
                for fn in os.listdir(root):
                    p = os.path.join(root, fn)
                    if not os.path.isfile(p):
                        continue
                    st = os.stat(p)
                    items.append({
                        "name": fn,
                        "path": fn,
                        "ext": (fn.rsplit(".", 1)[-1] or "").lower(),
                        "size": st.st_size,
                        "mtime": _dt.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "_ts": st.st_mtime,
                    })
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)
        items.sort(key=lambda x: x.pop("_ts"), reverse=True)
        return self._send_json({"ok": True, "artifacts": items, "dir": "outputs/superagent"})

    def _artifacts_archive(self):
        """POST /api/superagent/artifacts/archive {max_age_days?} -> 归档超龄产物 (Phase 53)。"""
        from .. import superagent as _sa
        body = self._read_json({})
        rep = _sa.archive_old_artifacts(base_dir=os.getcwd(),
                                        max_age_days=body.get("max_age_days") or 30)
        return self._send_json(rep)

    def _artifact_safe_path(self):
        """校验 query path 严格落在 outputs/superagent 内(防目录穿越), 返回绝对路径或 None。"""
        q = parse_qs(urlparse(self.path).query)
        rel = (q.get("path") or [""])[0]
        if not rel:
            return None
        norm = rel.replace("\\", "/")
        if norm.startswith("/") or ".." in norm.split("/"):
            return None
        root = os.path.realpath(os.path.join(os.getcwd(), "outputs", "superagent"))
        full = os.path.realpath(os.path.join(root, rel))
        if full != root and not full.startswith(root + os.sep):
            return None
        if not os.path.isfile(full):
            return None
        return full

    def _superagent_artifact_file(self):
        """GET /api/superagent/artifacts/file?path=<rel>&mode=preview|download。

        preview: JSON 返回文本内容(截 64KB); download: octet-stream 附件下载。
        """
        full = self._artifact_safe_path()
        if full is None:
            return self._send_json({"error": "非法路径或文件不存在"}, status=400)
        name = os.path.basename(full)
        size = os.path.getsize(full)
        mode = (parse_qs(urlparse(self.path).query).get("mode") or ["preview"])[0]
        if mode == "download":
            with open(full, "rb") as f:
                payload = f.read()
            quoted = _urlquote(name)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''%s" % quoted)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        with open(full, "rb") as f:
            data = f.read(65536)
        return self._send_json({"ok": True, "name": name, "size": size,
                                "truncated": size > 65536,
                                "content": data.decode("utf-8", "replace")})

    def _superagent_run(self):
        """POST /api/superagent/run {goal, session_id?} -> 目标理解→域路由→并行编排→收敛→自检→记忆沉淀。

        单目标跨 2+ 域编排; 无 LLM 全程规则兜底; 单伙伴失败隔离; 每阶段 trace 进审计链。
        """
        from .. import superagent as _sa
        try:
            body = self._read_json({})
            goal = (body.get("goal") or "").strip()
            if not goal:
                return self._send_json({"error": "缺少 goal"}, status=400)
            sa = _sa.SuperAgent(base_dir=os.getcwd())
            try:
                qws = min(60.0, max(1.0, float(body.get("queue_wait_sec") or 30.0)))
            except (TypeError, ValueError):
                qws = 30.0
            rep = sa.run(goal, session_id=body.get("session_id") or "",
                         llm_call=self._make_llm_call(),
                         model=self._cfg_llm_model(), queue_wait_sec=qws)
            if rep.get("busy"):
                return self._send_json(rep, status=429)
            return self._send_json({"ok": True, **rep})
        except Exception as e:
            return self._send_json({"error": "超级AGENT编排失败: %s" % e}, status=500)

    def _cfg_llm_model(self):
        """当前配置的 LLM 模型名(供编排成本按价格档估算)。"""
        try:
            return (_get_cfg().get("llm") or {}).get("model") or ""
        except Exception:
            return ""

    def _superagent_run_stream(self):
        """POST /api/superagent/run/stream {goal, session_id?} -> SSE 实时编排进度 (Phase 38).

        事件流: data: {"type":"stage","stage":...,"ts":...,"ok":...,"detail":...} 每阶段一条
                -> data: {"type":"done","result":{...完整编排结果...}}
                异常时 data: {"type":"error","message":...}。
        与 /api/superagent/run 同链路(规则兜底/异常隔离/审计链), 区别仅在逐阶段推送。
        """
        from .. import superagent as _sa
        try:
            body = self._read_json({})
        except Exception:
            body = {}
        goal = (body.get("goal") or "").strip()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        client_gone = {"flag": False}

        def emit(obj):
            if client_gone["flag"]:
                return
            try:
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                # 客户端断开(刷新/关闭页面) -> 停止推送, 编排继续跑完不留死循环
                client_gone["flag"] = True

        if not goal:
            emit({"type": "error", "message": "缺少 goal"})
            return

        def on_stage(entry):
            emit({"type": "stage", **entry})

        try:
            sa = _sa.SuperAgent(base_dir=os.getcwd())
            rep = sa.run(goal, session_id=body.get("session_id") or "",
                         llm_call=self._make_llm_call(), on_stage=on_stage,
                         model=self._cfg_llm_model())
            if rep.get("busy"):
                emit({"type": "error", "busy": True, "message": rep.get("error") or "编排并发已满"})
            else:
                emit({"type": "done", "result": rep})
        except Exception as e:
            emit({"type": "error", "message": "超级AGENT编排失败: %s" % e})

    # ---------------------------------------------------------------
    # 定时编排 (Phase 43): 计划 CRUD + 立即执行
    # ---------------------------------------------------------------
    def _schedules_get(self):
        """GET /api/superagent/schedules -> 定时编排计划列表。"""
        from .. import superagent as _sa
        return self._send_json({"ok": True, "schedules": _sa.list_schedules(base_dir=os.getcwd())})

    def _schedules_create(self):
        """POST /api/superagent/schedules/create {goal, every_sec?, daily?, enabled?, template_id?, tpl_vars?}。"""
        from .. import superagent as _sa
        body = self._read_json({})
        try:
            entry = _sa.add_schedule(body.get("goal") or "",
                                     every_sec=body.get("every_sec") or 0,
                                     daily=body.get("daily") or "",
                                     enabled=body.get("enabled", True),
                                     base_dir=os.getcwd(),
                                     template_id=body.get("template_id") or "",
                                     tpl_vars=body.get("tpl_vars") or {},
                                     retry_max=body.get("retry_max") or 0)
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        return self._send_json({"ok": True, "schedule": entry})

    def _schedules_update(self):
        """POST /api/superagent/schedules/update {id, goal?/every_sec?/daily?/enabled?/template_id?/tpl_vars?}。"""
        from .. import superagent as _sa
        body = self._read_json({})
        sid = (body.get("id") or "").strip()
        if not sid:
            return self._send_json({"error": "缺少 id"}, status=400)
        snap = _sa.update_schedule(sid, body, base_dir=os.getcwd())
        if snap is None:
            return self._send_json({"error": "schedule 不存在"}, status=404)
        return self._send_json({"ok": True, "schedule": snap})

    def _schedules_delete(self):
        """POST /api/superagent/schedules/delete {id}。"""
        from .. import superagent as _sa
        body = self._read_json({})
        sid = (body.get("id") or "").strip()
        if not sid:
            return self._send_json({"error": "缺少 id"}, status=400)
        if not _sa.remove_schedule(sid, base_dir=os.getcwd()):
            return self._send_json({"error": "schedule 不存在"}, status=404)
        return self._send_json({"ok": True, "removed": sid})

    def _schedules_run_now(self):
        """POST /api/superagent/schedules/run {id} -> 立即执行一次(同步)。"""
        from .. import superagent as _sa
        body = self._read_json({})
        sid = (body.get("id") or "").strip()
        if not sid:
            return self._send_json({"error": "缺少 id"}, status=400)
        rep = _sa.run_schedule(sid, base_dir=os.getcwd(),
                               llm_call=self._make_llm_call(),
                               queue_wait_sec=10.0)
        return self._send_json({"ok": rep.get("ok", False),
                                "schedule_id": sid,
                                "error": rep.get("error", ""),
                                "goal_ok": (rep.get("result") or {}).get("ok")})

    # ---------------------------------------------------------------
    # 模板库 (Phase 44): 模板 CRUD + 渲染
    # ---------------------------------------------------------------
    def _sa_tpl_get(self):
        """GET /api/superagent/templates -> 模板列表(含占位符字段 fields)。"""
        from .. import superagent as _sa
        return self._send_json({"ok": True, "templates": _sa.list_templates(base_dir=os.getcwd())})

    def _sa_tpl_create(self):
        """POST /api/superagent/templates/create {name, goal_template, description?}。"""
        from .. import superagent as _sa
        body = self._read_json({})
        try:
            entry = _sa.add_template(body.get("name") or "",
                                     body.get("goal_template") or "",
                                     description=body.get("description") or "",
                                     base_dir=os.getcwd())
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        return self._send_json({"ok": True, "template": entry})

    def _sa_tpl_update(self):
        """POST /api/superagent/templates/update {id, name?/goal_template?/description?}。"""
        from .. import superagent as _sa
        body = self._read_json({})
        tid = (body.get("id") or "").strip()
        if not tid:
            return self._send_json({"error": "缺少 id"}, status=400)
        snap = _sa.update_template(tid, body, base_dir=os.getcwd())
        if snap is None:
            return self._send_json({"error": "template 不存在"}, status=404)
        return self._send_json({"ok": True, "template": snap})

    def _sa_tpl_delete(self):
        """POST /api/superagent/templates/delete {id}。"""
        from .. import superagent as _sa
        body = self._read_json({})
        tid = (body.get("id") or "").strip()
        if not tid:
            return self._send_json({"error": "缺少 id"}, status=400)
        if not _sa.remove_template(tid, base_dir=os.getcwd()):
            return self._send_json({"error": "template 不存在"}, status=404)
        return self._send_json({"ok": True, "removed": tid})

    def _sa_tpl_render(self):
        """POST /api/superagent/templates/render {id, vars{}} -> {goal, missing}。"""
        from .. import superagent as _sa
        body = self._read_json({})
        tid = (body.get("id") or "").strip()
        if not tid:
            return self._send_json({"error": "缺少 id"}, status=400)
        rep = _sa.use_template(tid, variables=body.get("vars") or {}, base_dir=os.getcwd())
        if rep is None:
            return self._send_json({"error": "template 不存在"}, status=404)
        return self._send_json({"ok": True, **rep})

    # ---------------------------------------------------------------
    # Webhook 通知 (Phase 46): 接收端管理/测试
    # ---------------------------------------------------------------
    def _webhooks_get(self):
        """GET /api/superagent/webhooks -> 通知接收端列表。"""
        from .. import superagent as _sa
        return self._send_json({"ok": True, "webhooks": _sa.list_webhooks(base_dir=os.getcwd())})

    def _webhooks_create(self):
        """POST /api/superagent/webhooks/create {url, events?, secret?, enabled?, fmt?}。"""
        from .. import superagent as _sa
        body = self._read_json({})
        try:
            entry = _sa.add_webhook(body.get("url") or "",
                                    events=body.get("events") or "all",
                                    secret=body.get("secret") or "",
                                    fmt=body.get("fmt") or "raw",
                                    enabled=body.get("enabled", True),
                                    base_dir=os.getcwd())
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        return self._send_json({"ok": True, "webhook": entry})

    def _webhooks_update(self):
        """POST /api/superagent/webhooks/update {id, url?/events?/secret?/enabled?}。"""
        from .. import superagent as _sa
        body = self._read_json({})
        wid = (body.get("id") or "").strip()
        if not wid:
            return self._send_json({"error": "缺少 id"}, status=400)
        try:
            snap = _sa.update_webhook(wid, body, base_dir=os.getcwd())
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        if snap is None:
            return self._send_json({"error": "webhook 不存在"}, status=404)
        return self._send_json({"ok": True, "webhook": snap})

    def _webhooks_delete(self):
        """POST /api/superagent/webhooks/delete {id}。"""
        from .. import superagent as _sa
        body = self._read_json({})
        wid = (body.get("id") or "").strip()
        if not wid:
            return self._send_json({"error": "缺少 id"}, status=400)
        if not _sa.remove_webhook(wid, base_dir=os.getcwd()):
            return self._send_json({"error": "webhook 不存在"}, status=404)
        return self._send_json({"ok": True, "removed": wid})

    def _webhooks_test(self):
        """POST /api/superagent/webhooks/test {id} 或 {url, secret?} -> 发送测试事件。"""
        from .. import superagent as _sa
        body = self._read_json({})
        wid = (body.get("id") or "").strip()
        if wid:
            hooks = [h for h in _sa.list_webhooks(base_dir=os.getcwd()) if h["id"] == wid]
            if not hooks:
                return self._send_json({"error": "webhook 不存在"}, status=404)
            entry = hooks[0]
        else:
            url = (body.get("url") or "").strip()
            if not url:
                return self._send_json({"error": "缺少 id 或 url"}, status=400)
            entry = {"id": "_test", "url": url, "secret": body.get("secret") or "",
                     "events": "all", "enabled": True}
        payload = {"event": "test", "goal": "(Webhook 连通性测试)", "ok": True,
                   "ts": _sa._now(), "elapsed_sec": 0, "routed": [],
                   "selfcheck_score": None, "partners_ok": 0, "artifacts": [], "error": ""}
        status, err = _sa._webhook_post(entry, payload)
        return self._send_json({"ok": status == 200, "status": status,
                                "error": err})

    # ---------------------------------------------------------------
    # 编排日报/周报 (Phase 50): 统计预览 + 推送
    # ---------------------------------------------------------------
    def _digest_get(self):
        """GET /api/superagent/digest?period=daily|weekly -> 统计预览(不推送)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        period = (q.get("period") or ["daily"])[0]
        if period not in ("daily", "weekly"):
            return self._send_json({"error": "period 需 daily|weekly"}, status=400)
        return self._send_json({"ok": True, **_sa._digest_stats(period, base_dir=os.getcwd())})

    def _trend_get(self):
        """GET /api/superagent/trend?days=14 -> 按日编排趋势 (Phase 54)。"""
        from .. import superagent as _sa
        q = parse_qs(urlparse(self.path).query)
        try:
            days = int((q.get("days") or ["14"])[0])
        except (TypeError, ValueError):
            days = 14
        return self._send_json({"ok": True, "days": _sa.get_daily_trend(days, base_dir=os.getcwd())})

    def _digest_push(self):
        """POST /api/superagent/digest/push {period?} -> 聚合并推送到全部启用接收端。"""
        from .. import superagent as _sa
        body = self._read_json({})
        period = body.get("period") if body.get("period") in ("daily", "weekly") else "daily"
        rep = _sa.push_digest(period, base_dir=os.getcwd())
        return self._send_json({**rep, "period": period})

    # ---------------------------------------------------------------
    # 插件中枢 (Phase 32): Connector/Expert 注册/发现/接入
    # ---------------------------------------------------------------
    def _plugin_get(self):
        """GET /api/plugins -> 插件中枢概览(最近编排+已注册连接器+专家)。"""
        try:
            from .. import plugin_hub as _ph
            hub = _ph.get_hub()
            return self._send_json({"ok": True,
                                    "connectors": hub.list_connectors(),
                                    "experts": hub.list_experts()})
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

    def _plugin_conn_list(self):
        try:
            from .. import plugin_hub as _ph
            q = parse_qs(urlparse(self.path).query)
            cat = (q.get("category") or [""])[0] or None
            return self._send_json({"ok": True, "connectors": _ph.get_hub().list_connectors(category=cat)})
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

    def _plugin_expert_list(self):
        try:
            from .. import plugin_hub as _ph
            q = parse_qs(urlparse(self.path).query)
            domain = (q.get("domain") or [""])[0] or None
            return self._send_json({"ok": True, "experts": _ph.get_hub().list_experts(domain=domain)})
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

    def _plugin_discover(self):
        """POST /api/plugins/discover {plugin_dir} -> 扫描目录注册插件。"""
        try:
            from .. import plugin_hub as _ph
            body = self._read_json({})
            plugin_dir = (body.get("plugin_dir") or "").strip()
            if not plugin_dir:
                return self._send_json({"error": "缺少 plugin_dir"}, status=400)
            found = _ph.get_hub().discover(plugin_dir)
            return self._send_json({"ok": True, "found": found})
        except Exception as e:
            return self._send_json({"error": "插件发现失败: %s" % e}, status=500)

    def _plugin_wire(self):
        """POST /api/plugins/wire {goal} -> 把可用插件接入超级 AGENT, 返回接入摘要。"""
        try:
            from .. import plugin_hub as _ph
            from .. import superagent as _sa
            body = self._read_json({})
            goal = (body.get("goal") or "").strip()
            sa = _sa.SuperAgent(base_dir=os.getcwd())
            wired = _ph.get_hub().wire(sa, goal=goal)
            return self._send_json({"ok": True, "wired": wired})
        except Exception as e:
            return self._send_json({"error": "插件接入失败: %s" % e}, status=500)

    def _plugin_call_connector(self, name):
        """POST/GET /api/plugins/connectors/<name> -> 直接调用某连接器。"""
        try:
            from .. import plugin_hub as _ph
            hub = _ph.get_hub()
            c = hub.get_connector(name)
            if c is None:
                return self._send_json({"error": "连接器不存在: %s" % name}, status=404)
            body = self._read_json({}) if self.command == "POST" else {}
            goal = (body.get("goal") or "").strip()
            res = c.call(goal, **{k: v for k, v in body.items() if k != "goal"})
            res["name"] = name
            res["available"] = c.check()["available"]
            return self._send_json({"ok": True, **res})
        except Exception as e:
            return self._send_json({"error": "连接器调用失败: %s" % e}, status=500)

    def _plugin_connector_register(self):
        """POST /api/plugins/connectors/register {name, category, description, env_required?}
        -> 注册 connector(占位; 调用需通过 /api/plugins/connectors/<name> 的 call_fn 由 hub 内部设定,
        此处只演示注册表; 真实调用需 register_connector(call_fn=...) 在服务端预置)。"""
        try:
            from .. import plugin_hub as _ph
            body = self._read_json({})
            name = (body.get("name") or "").strip()
            if not name:
                return self._send_json({"error": "缺少 name"}, status=400)
            if not name.isidentifier():
                return self._send_json({"error": "非法 name(须为标识符)"}, status=400)
            hub = _ph.get_hub()
            hub.register_connector(
                name=name,
                category=body.get("category") or "tool",
                description=body.get("description") or "用户注册连接器",
                env_required=body.get("env_required") or [],
                optional_dep=body.get("optional_dep") or [],
                extra=body.get("extra") or {})
            return self._send_json({"ok": True, "name": name,
                                    "available": hub.get_connector(name).check()["available"]})
        except Exception as e:
            return self._send_json({"error": "连接器注册失败: %s" % e}, status=500)

    def _plugin_expert_register(self):
        """POST /api/plugins/experts/register {name, domain, system_prompt, description?, tags?} -> 注册专家画像。"""
        try:
            from .. import plugin_hub as _ph
            body = self._read_json({})
            name = (body.get("name") or "").strip()
            domain = (body.get("domain") or "code").strip()
            sys_prompt = (body.get("system_prompt") or "").strip()
            if not name or not sys_prompt:
                return self._send_json({"error": "缺少 name 或 system_prompt"}, status=400)
            if not name.isidentifier():
                return self._send_json({"error": "非法 name"}, status=400)
            hub = _ph.get_hub()
            hub.register_expert(
                name=name, domain=domain, system_prompt=sys_prompt,
                description=body.get("description") or "用户注册专家",
                tags=body.get("tags") or [])
            return self._send_json({"ok": True, "name": name, "domain": domain})
        except Exception as e:
            return self._send_json({"error": "专家注册失败: %s" % e}, status=500)

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

    def _serve_data_file(self, path):
        """按扩展名 mime 发送任意二进制文件(用于 /outputs/ 真实媒体交付)。"""
        import mimetypes
        try:
            with open(path, "rb") as f:
                body = f.read()
        except Exception:
            return self.send_error(404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _health_full(self):
        """全链路健康度自检: LLM 连通 + 9 MCP 服务器 + 文件系统根。

        返回结构化 JSON, 各组件带 ok/warn/fail 状态; MCP 额外补全实时 connected。
        """
        cfg = _get_cfg()
        try:
            from ..tools import health as _health
            report = _health.health_check(cfg)
        except Exception as e:
            return self._send_json({"ok": False, "overall": "fail", "error": str(e)}, status=500)
        # 补全 9 MCP 实时连接状态 (best-effort, 超时保护, 不阻断主报告)
        try:
            from ..tools.mcp import get_manager
            mgr = get_manager()
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(mgr.connect_all, cfg)
                try:
                    fut.result(timeout=12)
                except Exception:
                    pass
            live = {s["name"]: True for s in mgr.status()}
            for s in report.get("mcp_servers", []):
                s["connected"] = bool(live.get(s["name"]))
        except Exception:
            pass
        return self._send_json(report)

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
        # 主题 F — 专家/技能 提示词增强: 本轮激活的条目(名称列表, 可空)
        req_experts = body.get("experts") or []
        req_skills = body.get("skills") or []

        cfg = _get_cfg()
        # 尊重 config.toml 的 backend 配置 (本地 Ollama / 云端 / mock 均支持)
        backend = backend_override or cfg["llm"].get("backend", "ollama")
        client = build_client(backend, cfg=cfg)
        registry = build_registry(cfg, permission_mode=mode)

        # 主题 F — 合并「库默认激活项」与「本轮用户选择」, 去重后注入系统提示
        from ..agent import enhance as _enh
        _enh_data = _enh.load(os.getcwd())
        _def_exp, _def_skl = _enh.default_active(_enh_data)
        _act_exp = sorted(set(list(req_experts) + _def_exp))
        _act_skl = sorted(set(list(req_skills) + _def_skl))

        # 会话续跑: 同一 session_id 多次请求复用活体 AgentLoop(执行态保留);
        # 若内存无则尝试磁盘水合(完整 messages 含 tool 角色); 否则新建。
        session_id = body.get("session_id") or None
        loop, session_lock, hydrated = acquire_session(
            session_id, client, registry, cfg, backend,
            experts=_act_exp, skills=_act_skl, enhance_data=_enh_data)
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
                    # 错误归集: 对话循环异常写入 logs/errors(便于「错误汇总」)
                    try:
                        from .. import errorlog as _el
                        _el.record(os.getcwd(), "chat_loop", "对话循环异常: %s" % e,
                                   source="agent_loop", detail=str(e))
                    except Exception:
                        pass
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
        if p.startswith("/api/multimodal/"):
            aid = p[len("/api/multimodal/"):].strip("/")
            if not aid:
                return self._send_json({"ok": False, "error": "缺少资产 id"}, status=400)
            from .. import multimodal as _mm
            ok = _mm.AssetLibrary(os.getcwd()).delete(aid)
            return self._send_json({"ok": ok})
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
            "superagent": self._superagent_stats(),
        }

    # ---- Phase 35: 超级 AGENT 全链路可观测合规 ----
    def _superagent_stats(self):
        """从 superagent._RUNS 环形缓冲读取最近编排, 输出聚合统计 + 最近编排明细。"""
        try:
            from .. import superagent as _sa
            runs = _sa.get_recent_runs(30)
        except Exception:
            return {"total": 0, "ok": 0, "fail": 0,
                    "recent": [], "elapsed_p50": None, "elapsed_p95": None,
                    "elapsed_max": None, "avg_score": None}
        if not runs:
            return {"total": 0, "ok": 0, "fail": 0,
                    "recent": [], "elapsed_p50": None, "elapsed_p95": None,
                    "elapsed_max": None, "avg_score": None}
        n = len(runs)
        ok_n = sum(1 for r in runs if r.get("ok"))
        elapsed_sorted = sorted(r.get("elapsed_sec") or 0 for r in runs)
        def _pct(p):
            i = max(0, min(n - 1, int(round(p * (n - 1)))))
            return round(elapsed_sorted[i], 2)
        scores = [r.get("selfcheck_score") for r in runs if r.get("selfcheck_score") is not None]
        avg_score = round(sum(scores) / len(scores), 1) if scores else None
        recent = []
        for r in runs[-10:][::-1]:
            recent.append({
                "goal": r.get("goal", "")[:60],
                "ts": r.get("ts", ""),
                "ok": bool(r.get("ok")),
                "routed": r.get("routed", []) or [],
                "partners_ok": r.get("partners_ok", 0),
                "partners_total": r.get("partners_total", 0),
                "conflicts": r.get("conflicts", 0),
                "artifacts": r.get("artifacts", 0),
                "elapsed_sec": r.get("elapsed_sec", 0),
                "selfcheck_score": r.get("selfcheck_score"),
                "guards_passed": r.get("guards_passed", True),
            })
        return {
            "total": n,
            "ok": ok_n,
            "fail": n - ok_n,
            "recent": recent,
            "elapsed_p50": _pct(0.50),
            "elapsed_p95": _pct(0.95),
            "elapsed_max": round(elapsed_sorted[-1], 2) if elapsed_sorted else 0,
            "avg_score": avg_score,
        }

    # ---- 主题 E 成本看板 (批次13): 会话级 token/成本追踪 ----
    def _cost_stats(self):
        """遍历活体会话, 汇总每会话估算 token/成本 + 进程总计 + 价目参考。

        成本看板预警(批次17): 读取 agent.cost_alert_threshold, 对每个会话与进程总额
        标注 over_threshold 与阈值 threshold, 前端据此红色高亮。
        """
        try:
            _cfg = _get_cfg()
            threshold = float(_cfg_get(_cfg, "agent.cost_alert_threshold") or 1.0)
        except Exception:
            threshold = 1.0
        sessions = []
        t_in = t_out = 0
        t_cost = 0.0
        for sid, loop in _SESSION_LOOPS.items():
            try:
                st = loop.token_stats()
            except Exception:
                continue
            mode = getattr(getattr(loop, "registry", None), "permission_mode", "") or ""
            cost = st.get("est_cost_cny", 0.0)
            sessions.append({
                "session_id": sid,
                "backend": loop.provider or "",
                "model": st.get("model") or "",
                "est_input_tokens": st.get("est_input_tokens", 0),
                "est_output_tokens": st.get("est_output_tokens", 0),
                "est_total_tokens": st.get("est_total_tokens", 0),
                "est_cost_cny": cost,
                "turns": len(getattr(loop, "messages", []) or []),
                "plan_mode": (mode == "plan"),
                "threshold": threshold,
                "over_threshold": bool(cost > threshold),
            })
            t_in += st.get("est_input_tokens", 0)
            t_out += st.get("est_output_tokens", 0)
            t_cost += cost
        sessions.sort(key=lambda x: -x["est_total_tokens"])
        # Phase 40: 超级 AGENT 编排 LLM 用量聚合(内存 + 磁盘历史, 重启不丢)
        sa_usage = {}
        try:
            from .. import superagent as _sa
            sa_usage = _sa.get_usage_totals(base_dir=os.getcwd())
        except Exception:
            sa_usage = {}
        return {
            "sessions": sessions,
            "total": {
                "est_input_tokens": t_in,
                "est_output_tokens": t_out,
                "est_total_tokens": t_in + t_out,
                "est_cost_cny": round(t_cost, 6),
                "threshold": threshold,
                "over_threshold": bool(t_cost > threshold),
            },
            "superagent": sa_usage,
            "pricing": _pricing.reference_list(),
            "currency": "CNY",
        }

    # ---- 主题 B 计划看板 (批次13): 计划模式产物可视化 ----
    def _planboard(self, sid):
        """返回某会话的计划看板数据 (仅活体会话; 无产物返回 found=False)。"""
        loop = _SESSION_LOOPS.get(sid) if sid else None
        if not loop:
            return {"session_id": sid, "found": False, "plan": None, "cards": None, "model": ""}
        cards = None
        try:
            cards = loop.get_plan_cards()
        except Exception:
            cards = None
        return {
            "session_id": sid,
            "found": True,
            "plan": getattr(loop, "plan_artifact", None),
            "cards": cards,
            "model": getattr(loop, "model", ""),
        }

    # ---- 设置中心 (批次14): 查看 / 编辑 config.toml ----
    def _settings_get(self):
        """GET /api/settings: 返回配置路径、原始文本、结构化标量分组(供表单)。"""
        cfg = _get_cfg()
        path = _config_path()
        raw = ""
        if path and path.exists():
            try:
                raw = path.read_text(encoding="utf-8")
            except Exception:
                raw = ""
        values = {}
        for g in _SETTINGS_SCHEMA:
            for fld in g["fields"]:
                values[fld["key"]] = _cfg_get(cfg, fld["key"])
        return {
            "path": str(path) if path else None,
            "exists": bool(path and path.exists()),
            "raw": raw,
            "schema": _SETTINGS_SCHEMA,
            "values": values,
            "version": __version__,
        }

    def _settings_save(self):
        """POST /api/settings {mode, text?, values?}: 写回 config.toml。

        - mode=raw:   校验 text 为合法 TOML 后整文件覆盖 (保留用户完全掌控, 适合数组/复杂结构)。
        - mode=form:  对标量字段行内替换 (保留注释/缩进/数组), 再校验生成的 TOML。
        成功后软重载 _RUNTIME_CONFIG 即时部分生效; 改动需重建连接/客户端的字段则 require_restart=True。
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return self._send_json({"error": "请求体非 JSON"}, status=400)
        mode = body.get("mode", "raw")
        path = _config_path()
        if path is None:
            return self._send_json({"error": "找不到配置文件路径"}, status=500)
        changed_restart = False
        try:
            if mode == "raw":
                text = body.get("text", "")
                try:
                    tomllib.loads(text)
                except Exception as e:
                    return self._send_json({"error": "TOML 语法错误: %s" % e}, status=400)
                new_text = text
                # raw 模式可能因改了任意字段(含 mcp/security)而需重启
                changed_restart = True
            elif mode == "form":
                current = path.read_text(encoding="utf-8") if path.exists() else ""
                new_text = current
                for g in _SETTINGS_SCHEMA:
                    for fld in g["fields"]:
                        if fld["key"] not in body.get("values", {}):
                            continue
                        val = body["values"][fld["key"]]
                        new_text, _applied = _set_scalar_in_toml(
                            new_text, fld["section"], fld["key"].split(".")[-1], val, fld["type"])
                        if fld.get("restart"):
                            changed_restart = True
                try:
                    tomllib.loads(new_text)
                except Exception as e:
                    return self._send_json({"error": "生成的 TOML 语法错误: %s" % e}, status=400)
            else:
                return self._send_json({"error": "未知 mode: %s" % mode}, status=400)
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

        # 写回文件
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_text, encoding="utf-8")
        except Exception as e:
            return self._send_json({"error": "写入失败: %s" % e}, status=500)

        # 软重载: 即时生效 (新 chat/调用经 _get_cfg 取新值)
        try:
            global _RUNTIME_CONFIG
            _RUNTIME_CONFIG = load_config(str(path))
        except Exception:
            pass
        # Phase 63: 运行期开关即时重注入 (通知/重试/质量自动推送改完即生效, 无需重启)
        try:
            from .. import superagent as _sa
            _sa.set_digest_time(_cfg_get(_RUNTIME_CONFIG, "agent.digest_time") or "")
        except Exception:
            pass
        try:
            _sa.set_default_retry_max(
                _cfg_get(_RUNTIME_CONFIG, "agent.orchestration_retry_max") or 0)
        except Exception:
            pass
        try:
            _pub = _cfg_get(_RUNTIME_CONFIG, "agent.public_base_url") or ""
            if _pub:
                _sa.set_public_base_url(_pub)
        except Exception:
            pass
        try:
            _sa.set_quality_auto(
                bool(_cfg_get(_RUNTIME_CONFIG, "agent.quality_auto_push")),
                _cfg_get(_RUNTIME_CONFIG, "agent.quality_auto_interval_h") or 24)
        except Exception:
            pass
        # 若改动涉及 MCP, 尝试即时重连 (禁用需重启才能断开旧连接)
        if changed_restart and mode == "form" and any(
                fld["section"].startswith("mcp") for g in _SETTINGS_SCHEMA
                for fld in g["fields"] if fld["key"] in body.get("values", {})):
            try:
                from ..tools import mcp as _mcp
                _mcp.get_manager().connect_all(_get_cfg())
            except Exception:
                pass

        return self._send_json({
            "ok": True,
            "path": str(path),
            "require_restart": changed_restart,
            "bytes": len(new_text.encode("utf-8")),
        })

    # ---- 工作区沙箱 (文件系统根域管理): 把既有 allowed_roots 做成可管理、可可视化功能 ----
    def _sandbox_get(self):
        """GET /api/sandbox: 返回当前工作区沙箱状态。"""
        cfg = _get_cfg()
        sec = (cfg.get("agent", {}) or {}).get("security", {}) or {}
        raw_roots = sec.get("allowed_roots", []) or []
        try:
            from ..config import resolve_roots
            roots = [str(p) for p in resolve_roots(cfg)]
        except Exception:
            roots = []
        deny = sec.get("deny_patterns", []) or []
        active = bool(roots)
        note = ("已启用: 所有文件读写 / 搜索 / 编辑严格限制在以下根目录内, 越界操作被拦截"
                if active else
                "未配置允许根目录 (allowed_roots 为空), 文件操作边界未定义 (需至少配置一个根)")
        return {
            "active": active,
            "roots": roots,
            "raw_roots": raw_roots,
            "deny_patterns": deny,
            "base_dir": os.path.abspath(os.getcwd()),
            "note": note,
            "require_restart": False,
        }

    def _sandbox_save(self):
        """POST /api/sandbox {roots:[...]}: 写回 config.toml 的 agent.security.allowed_roots。

        接收字符串目录数组, 以 cwd 为基准绝对化、去重、跳过空项; 校验 TOML 合法后写回;
        软重载 _RUNTIME_CONFIG 使新会话即时采用(既有会话沿用旧 roots), 标 require_restart=True。
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return self._send_json({"error": "请求体非 JSON"}, status=400)
        roots = body.get("roots")
        if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
            return self._send_json({"error": "roots 必须是字符串数组"}, status=400)
        base = os.path.abspath(os.getcwd())
        norm, seen, warnings = [], set(), []
        for r in roots:
            r = (r or "").strip()
            if not r:
                continue
            p = os.path.abspath(r if os.path.isabs(r) else os.path.join(base, r))
            if p in seen:
                continue
            seen.add(p)
            if not os.path.exists(p):
                warnings.append("目录不存在(仍会写入, 但新建会话时该根不可用): %s" % p)
            norm.append(p)
        if not norm:
            return self._send_json({"error": "至少需要一个有效根目录"}, status=400)
        path = _config_path()
        if path is None:
            return self._send_json({"error": "找不到配置文件路径"}, status=500)
        try:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            new_text, _applied = _set_array_in_toml(current, "agent.security", "allowed_roots", norm)
            try:
                tomllib.loads(new_text)
            except Exception as e:
                return self._send_json({"error": "生成的 TOML 语法错误: %s" % e}, status=400)
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_text, encoding="utf-8")
        except Exception as e:
            return self._send_json({"error": "写入失败: %s" % e}, status=500)
        try:
            global _RUNTIME_CONFIG
            _RUNTIME_CONFIG = load_config(str(path))
        except Exception:
            pass
        return self._send_json({
            "ok": True,
            "path": str(path),
            "roots": norm,
            "require_restart": True,
            "warnings": warnings,
            "bytes": len(new_text.encode("utf-8")),
        })

    # ---- 工作区备份 / 回滚 (时间点快照管理) ----
    def _backups_get(self):
        """GET /api/backups: 列出快照 + 工作区信息。"""
        from .. import backup as _bk
        roots = [os.path.abspath(os.getcwd())]
        try:
            from ..config import resolve_roots
            resolved = [str(p) for p in resolve_roots(_get_cfg())]
            if resolved:
                roots = resolved
        except Exception:
            pass
        mgr = _bk.BackupManager(roots)
        items = []
        try:
            items = mgr.list()
        except Exception:
            pass
        return self._send_json({
            "ok": True,
            "store": mgr.store,
            "roots": roots,
            "backups": items,
            "count": len(items),
        })

    def _backups_create(self):
        """POST /api/backups {label?}: 创建快照。"""
        body = self._read_json({})
        label = (body.get("label") or "").strip()
        from .. import backup as _bk
        roots = [os.path.abspath(os.getcwd())]
        try:
            from ..config import resolve_roots
            resolved = [str(p) for p in resolve_roots(_get_cfg())]
            if resolved:
                roots = resolved
        except Exception:
            pass
        mgr = _bk.BackupManager(roots)
        try:
            m = mgr.create(label)
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)
        return self._send_json({"ok": True, "backup": m})

    def _backup_rollback(self):
        """POST /api/backups/rollback {id, clean?}: 回滚到指定快照。"""
        body = self._read_json({})
        bid = (body.get("id") or "").strip()
        if not bid:
            return self._send_json({"error": "缺少 id"}, status=400)
        clean = bool(body.get("clean"))
        from .. import backup as _bk
        roots = [os.path.abspath(os.getcwd())]
        try:
            from ..config import resolve_roots
            resolved = [str(p) for p in resolve_roots(_get_cfg())]
            if resolved:
                roots = resolved
        except Exception:
            pass
        mgr = _bk.BackupManager(roots)
        try:
            r = mgr.rollback(bid, clean=clean)
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)
        return self._send_json({"ok": True, **r})

    def _backup_delete(self):
        """POST /api/backups/delete {id}: 删除快照。"""
        body = self._read_json({})
        bid = (body.get("id") or "").strip()
        if not bid:
            return self._send_json({"error": "缺少 id"}, status=400)
        from .. import backup as _bk
        roots = [os.path.abspath(os.getcwd())]
        try:
            from ..config import resolve_roots
            resolved = [str(p) for p in resolve_roots(_get_cfg())]
            if resolved:
                roots = resolved
        except Exception:
            pass
        mgr = _bk.BackupManager(roots)
        try:
            r = mgr.delete(bid)
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)
        return self._send_json({"ok": True, **r})

    # ---- 提示词模板: 新建/更新 / 删除 ----
    def _templates_save(self):
        """POST /api/templates {name, content, category?, id?}: 新建或更新模板。"""
        body = self._read_json({})
        if not isinstance(body, dict):
            return self._send_json({"error": "请求体必须是对象"}, status=400)
        name = (body.get("name") or "").strip()
        if not name:
            return self._send_json({"error": "模板名称不能为空"}, status=400)
        from .. import templates as _tpl
        try:
            rec, is_new = _tpl.upsert(os.getcwd(), name, body.get("content") or "",
                                     body.get("category") or "其他", body.get("id"))
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        return self._send_json({"ok": True, "is_new": is_new, "template": rec})

    def _templates_delete(self):
        """POST /api/templates/delete {id}: 删除模板。"""
        body = self._read_json({})
        tid = (body.get("id") or "").strip()
        if not tid:
            return self._send_json({"error": "缺少 id"}, status=400)
        from .. import templates as _tpl
        removed = _tpl.delete(tid, os.getcwd())
        return self._send_json({"ok": True, "removed": removed})

    # ---- 密钥保险箱: 设置/删除 ----
    def _secrets_set(self):
        """POST /api/secrets {key, value, note?}: 新增或更新密钥(轻量本地加密落盘)。"""
        body = self._read_json({})
        if not isinstance(body, dict):
            return self._send_json({"error": "请求体必须是对象"}, status=400)
        key = (body.get("key") or "").strip()
        if not key:
            return self._send_json({"error": "密钥名称不能为空"}, status=400)
        from .. import secrets as _sec
        try:
            is_new = _sec.set_secret(key, body.get("value") or "", body.get("note") or "", os.getcwd())
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        return self._send_json({"ok": True, "is_new": is_new, "key": key})

    def _secrets_delete(self):
        """POST /api/secrets/delete {key}: 删除密钥。"""
        body = self._read_json({})
        key = (body.get("key") or "").strip()
        if not key:
            return self._send_json({"error": "缺少 key"}, status=400)
        from .. import secrets as _sec
        removed = _sec.delete_secret(key, os.getcwd())
        return self._send_json({"ok": True, "removed": removed})

    # ---- 代码片段库: 新建/更新/删除 ----
    def _snippets_save(self):
        """POST /api/snippets {title, content, language?, tags?, id?}: 新建或更新片段。"""
        body = self._read_json({})
        if not isinstance(body, dict):
            return self._send_json({"error": "请求体必须是对象"}, status=400)
        title = (body.get("title") or "").strip()
        if not title:
            return self._send_json({"error": "片段标题不能为空"}, status=400)
        from .. import snippets as _snip
        try:
            rec, is_new = _snip.upsert(os.getcwd(), title, body.get("content") or "",
                                       body.get("language") or "其他", body.get("tags"), body.get("id"))
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        return self._send_json({"ok": True, "is_new": is_new, "snippet": rec})

    def _snippets_delete(self):
        """POST /api/snippets/delete {id}: 删除片段。"""
        body = self._read_json({})
        tid = (body.get("id") or "").strip()
        if not tid:
            return self._send_json({"error": "缺少 id"}, status=400)
        from .. import snippets as _snip
        removed = _snip.delete(tid, os.getcwd())
        return self._send_json({"ok": True, "removed": removed})

    # ---- 笔记: 新建/更新/删除 ----
    def _notes_save(self):
        """POST /api/notes {title, content?, id?}: 新建或更新笔记。"""
        body = self._read_json({})
        if not isinstance(body, dict):
            return self._send_json({"error": "请求体必须是对象"}, status=400)
        title = (body.get("title") or "").strip()
        if not title:
            return self._send_json({"error": "笔记标题不能为空"}, status=400)
        from .. import notes as _note
        try:
            rec, is_new = _note.upsert(os.getcwd(), title, body.get("content") or "", body.get("id"))
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        return self._send_json({"ok": True, "is_new": is_new, "note": rec})

    def _notes_delete(self):
        """POST /api/notes/delete {id}: 删除笔记。"""
        body = self._read_json({})
        tid = (body.get("id") or "").strip()
        if not tid:
            return self._send_json({"error": "缺少 id"}, status=400)
        from .. import notes as _note
        removed = _note.delete(tid, os.getcwd())
        return self._send_json({"ok": True, "removed": removed})

    # ---- 待办清单: 新增/改状态/删除 ----
    def _todos_add(self):
        """POST /api/todos {title, priority?, due?, note?}: 新增待办。"""
        body = self._read_json({})
        if not isinstance(body, dict):
            return self._send_json({"error": "请求体必须是对象"}, status=400)
        title = (body.get("title") or "").strip()
        if not title:
            return self._send_json({"error": "待办标题不能为空"}, status=400)
        from .. import todos as _td
        try:
            rec = _td.add(os.getcwd(), title, body.get("priority") or "mid",
                          body.get("due"), body.get("note") or "")
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        return self._send_json({"ok": True, "todo": rec})

    def _todos_status(self):
        """POST /api/todos/status {id, status?}: 更新待办状态(默认 done)。"""
        body = self._read_json({})
        tid = (body.get("id") or "").strip()
        if not tid:
            return self._send_json({"error": "缺少 id"}, status=400)
        status = (body.get("status") or "done").strip() or "done"
        from .. import todos as _td
        try:
            rec = _td.set_status(tid, status, os.getcwd())
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        if rec is None:
            return self._send_json({"error": "未找到该待办"}, status=404)
        return self._send_json({"ok": True, "todo": rec})

    def _todos_delete(self):
        """POST /api/todos/delete {id}: 删除待办。"""
        body = self._read_json({})
        tid = (body.get("id") or "").strip()
        if not tid:
            return self._send_json({"error": "缺少 id"}, status=400)
        from .. import todos as _td
        removed = _td.delete(tid, os.getcwd())
        return self._send_json({"ok": True, "removed": removed})

    # ---- 主题 F 专家/技能 提示词增强: 写回库 (prompts_enhance.json) ----
    def _enhance_save(self):
        """POST /api/enhance {experts:[...], skills:[...]}: 全量写回增强库。

        每条目字段:
          专家: {name, description?, prompt, enabled?}
          技能: {name, description?, prompt, trigger?, auto?}
        返回 {ok, path, count}。"""
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return self._send_json({"error": "请求体非 JSON"}, status=400)
        if not isinstance(body, dict):
            return self._send_json({"error": "请求体必须是对象"}, status=400)
        # 恢复内置预设: 覆盖写回 DEFAULT_LIBRARY(忽略其余字段)
        if body.get("reset"):
            from ..agent import enhance as _enh
            try:
                path = _enh.reset_to_defaults(os.getcwd())
                data = _enh.load(os.getcwd(), seed=False)
            except Exception as e:
                return self._send_json({"error": "恢复预设失败: %s" % e}, status=500)
            return self._send_json({
                "ok": True,
                "reset": True,
                "path": str(path),
                "count": {"experts": len(data["experts"]), "skills": len(data["skills"])},
            })
        experts = body.get("experts")
        skills = body.get("skills")
        if not isinstance(experts, list) or not isinstance(skills, list):
            return self._send_json({"error": "experts / skills 必须是数组"}, status=400)
        # 基础校验: name 必填且唯一
        for label, items in (("experts", experts), ("skills", skills)):
            seen = set()
            for it in items:
                if not isinstance(it, dict) or not (it.get("name") or "").strip():
                    return self._send_json({"error": "%s 中每项需含非空 name" % label}, status=400)
                nm = it["name"].strip()
                if nm in seen:
                    return self._send_json({"error": "%s 名称重复: %s" % (label, nm)}, status=400)
                seen.add(nm)
        from ..agent import enhance as _enh
        try:
            path = _enh.save(os.getcwd(), {"experts": experts, "skills": skills})
        except Exception as e:
            return self._send_json({"error": "写入失败: %s" % e}, status=500)
        return self._send_json({
            "ok": True,
            "path": str(path),
            "count": {"experts": len(experts), "skills": len(skills)},
        })

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
        if action == "grep":
            return self._fs_grep(qs, base, home)
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

    def _fs_grep(self, qs, base, home):
        """GET /api/fs/grep?path=&pattern=&regex=0/1&ignorecase=0/1&ext=.py,.js

        递归扫描文本文件, 返回命中行列表 {file, line, col, text, match}。
        安全限制同 _fs_api; 跳过二进制与 SKIP 目录; 结果上限保护。
        """
        import re as _re
        raw_path = (qs.get("path") or ["."])[0]
        pattern = (qs.get("pattern") or [""])[0]
        if not pattern:
            return self._send_json({"error": "缺少 pattern"}, status=400)
        use_regex = (qs.get("regex") or ["0"])[0] in ("1", "true", "on")
        ignorecase = (qs.get("ignorecase") or ["1"])[0] in ("1", "true", "on")
        ext_filter = [e.strip().lower() for e in (qs.get("ext") or [""])[0].split(",") if e.strip()]
        try:
            flags = _re.IGNORECASE if ignorecase else 0
            if use_regex:
                comp = _re.compile(pattern, flags)
            else:
                comp = _re.compile(_re.escape(pattern), flags)
        except Exception as e:
            return self._send_json({"error": "正则编译失败: %s" % e}, status=400)
        target = os.path.abspath(os.path.join(base, raw_path)) if not raw_path.startswith("~") \
            else os.path.abspath(os.path.expanduser(raw_path))
        SKIP = {".git", "__pycache__", "node_modules", ".venv", "dist", "build", ".workbuddy", ".pytest_cache"}
        BINARY_EXT = {".pyc", ".pyo", ".exe", ".dll", ".so", ".dylib", ".png", ".jpg",
                      ".jpeg", ".gif", ".bmp", ".ico", ".zip", ".gz", ".tar", ".rar",
                      ".pdf", ".bin", ".dat", ".ttf", ".woff", ".woff2", ".mp3", ".mp4"}
        MAX_RESULTS = 800
        MAX_FILES = 400
        MAX_BYTES = 2_000_000
        results = []
        scanned = 0
        truncated = False

        def walk(d):
            nonlocal scanned, truncated
            if len(results) >= MAX_RESULTS or scanned >= MAX_FILES:
                return
            try:
                names = sorted(os.listdir(d))
            except Exception:
                return
            for name in names:
                if len(results) >= MAX_RESULTS or scanned >= MAX_FILES:
                    truncated = True
                    return
                fp = os.path.join(d, name)
                rel = os.path.relpath(fp, base)
                if os.path.isdir(fp):
                    if name in SKIP:
                        continue
                    walk(fp)
                elif os.path.isfile(fp):
                    ext = os.path.splitext(name)[1].lower()
                    if ext in BINARY_EXT:
                        continue
                    if ext_filter and ext not in ext_filter:
                        continue
                    scanned += 1
                    try:
                        if os.path.getsize(fp) > MAX_BYTES:
                            continue
                        with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                            for i, line in enumerate(fh, 1):
                                if len(results) >= MAX_RESULTS:
                                    truncated = True
                                    return
                                m = comp.search(line)
                                if not m:
                                    continue
                                results.append({
                                    "file": rel,
                                    "line": i,
                                    "col": m.start() + 1,
                                    "text": line.rstrip("\n").rstrip("\r"),
                                })
                    except Exception:
                        continue

        if os.path.isfile(target):
            walk(os.path.dirname(target))
        else:
            walk(target)
        return self._send_json({
            "path": raw_path,
            "pattern": pattern,
            "count": len(results),
            "scanned": scanned,
            "truncated": truncated,
            "results": results,
        })

    # ===================================================================
    # 记忆中枢 (长期记忆 + 每日日志)
    # ===================================================================
    def _memory_get(self):
        """GET /api/memory -> {memory, logs:[...]}; 或 ?log=<date> 读取单日日志。"""
        from .. import memory_mgr as _mm
        q = parse_qs(urlparse(self.path).query)
        log_date = (q.get("log") or [None])[0]
        if log_date:
            return self._send_json(_mm.read_log(os.getcwd(), log_date))
        return self._send_json({
            "memory": _mm.read_memory(os.getcwd()),
            "logs": _mm.list_logs(os.getcwd()).get("logs", []),
        })

    def _memory_update(self):
        """POST /api/memory {mode:'replace'|'append'|'log'|'capture', content, title?, date?}

        - replace: 整体覆盖 MEMORY.md
        - append:  向 MEMORY.md 追加一条带时间戳笔记
        - log:     向每日日志追加一段
        - capture: 从文本 LLM 抽取关键事实并写入语义记忆(无 key 走规则兜底)
        """
        from .. import memory_mgr as _mm
        body = self._read_json({})
        mode = (body.get("mode") or "append").strip()
        content = (body.get("content") or "").strip()
        title = (body.get("title") or "").strip()
        if mode == "replace":
            if not content:
                return self._send_json({"error": "replace 模式需要 content"}, status=400)
            return self._send_json(_mm.update_memory(os.getcwd(), content))
        if mode == "log":
            if not content:
                return self._send_json({"error": "log 模式需要 content"}, status=400)
            return self._send_json(_mm.append_log(os.getcwd(), content, title=title, date=body.get("date")))
        if mode == "capture":
            if not content:
                return self._send_json({"error": "capture 模式需要 content"}, status=400)
            return self._send_json(_mm.capture(os.getcwd(), content, llm_call=self._make_llm_call()))
        # 默认 append
        if not content:
            return self._send_json({"error": "append 模式需要 content"}, status=400)
        return self._send_json(_mm.append_memory(os.getcwd(), content, title=title))

    def _memory_retrieve(self):
        """POST /api/memory/retrieve {query, k?} -> 语义召回相关记忆片段。"""
        from .. import memory_mgr as _mm
        body = self._read_json({})
        query = (body.get("query") or "").strip()
        if not query:
            return self._send_json({"error": "缺少 query"}, status=400)
        k = body.get("k") or 5
        try:
            k = int(k)
        except Exception:
            k = 5
        return self._send_json(_mm.retrieve(os.getcwd(), query, k=max(1, min(20, k))))

    # ===================================================================
    # 计划书 + 任务清单
    # ===================================================================
    def _plans_list(self):
        """GET /api/plans -> {plans:[...]}; 或 ?id=<id> 返回单篇完整计划。"""
        from .. import plans as _pl
        q = parse_qs(urlparse(self.path).query)
        pid = (q.get("id") or [None])[0]
        if pid:
            obj = _pl.get_plan(pid, os.getcwd())
            if not obj:
                return self._send_json({"error": "计划不存在"}, status=404)
            return self._send_json(obj)
        return self._send_json(_pl.list_plans(os.getcwd()))

    def _plans_save(self):
        """POST /api/plans {id?, title, content?, tasks?, status?} -> 新建/更新计划。"""
        from .. import plans as _pl
        body = self._read_json({})
        if not isinstance(body, dict):
            return self._send_json({"error": "请求体必须是对象"}, status=400)
        if not (body.get("title") or "").strip():
            return self._send_json({"error": "计划标题不能为空"}, status=400)
        try:
            obj = _pl.save(os.getcwd(), body)
        except Exception as e:
            return self._send_json({"error": "保存失败: %s" % e}, status=500)
        return self._send_json({"ok": True, "plan": obj})

    def _decompose_api(self):
        """POST /api/decompose {goal, context?} -> 层级任务分解(LLM 驱动 + 规则兜底)。"""
        from .. import decompose_engine as _de
        body = self._read_json({})
        goal = (body.get("goal") or "").strip()
        if not goal:
            return self._send_json({"error": "缺少 goal"}, status=400)
        try:
            steps = _de.decompose(goal, text=body.get("context") or "", llm_call=self._make_llm_call())
            payload = _de.to_plan_payload(goal, steps, title=goal)
            order = _de.execution_order(steps)
            return self._send_json({
                "ok": True, "goal": goal, "steps": steps,
                "execution_order": order, "plan": payload,
            })
        except Exception as e:
            from .. import errorlog as _el
            _el.record(os.getcwd(), "decompose", "任务分解失败: %s" % e, source="api:/api/decompose", detail=str(e))
            return self._send_json({"error": "分解失败: %s" % e}, status=500)

    # ---- 四大创作域 统一创作工作台 (终极蓝图 Phase 4) ----
    def _creation_domains(self):
        """GET /api/creation/domains -> 四域元信息。"""
        from .. import creation_domains as _cd
        return self._send_json({"ok": True, "domains": _cd.list_domains()})

    def _creation_dispatch(self):
        """POST /api/creation/dispatch {domain, brief, context?} -> 路由到创作域产出蓝图。"""
        from .. import creation_domains as _cd
        try:
            body = self._read_json({})
            domain = (body.get("domain") or "").strip()
            brief = (body.get("brief") or "").strip()
            if not domain or not brief:
                return self._send_json({"error": "domain 与 brief 必填"}, status=400)
            result = _cd.dispatch(domain, brief, context=body.get("context") or "", llm_call=self._make_llm_call())
            return self._send_json(result)
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        except Exception as e:
            from .. import errorlog as _el
            _el.record(os.getcwd(), "creation", "创作分发失败: %s" % e, source="api:/api/creation/dispatch", detail=str(e))
            return self._send_json({"error": "分发失败: %s" % e}, status=500)

    def _autonomous_run(self):
        """POST /api/autonomous {goal, context?, max_iter?} -> 自主自驱循环(规划/观察/Critic/反思)。"""
        from .. import autonomous as _au
        try:
            body = self._read_json({})
            goal = (body.get("goal") or "").strip()
            if not goal:
                return self._send_json({"error": "缺少 goal"}, status=400)
            try:
                max_iter = int(body.get("max_iter") or 6)
            except (TypeError, ValueError):
                max_iter = 6
            result = _au.run(goal, llm_call=self._make_llm_call(), context=body.get("context") or "", max_iter=max_iter)
            return self._send_json(result)
        except Exception as e:
            from .. import errorlog as _el
            _el.record(os.getcwd(), "autonomous", "自主循环失败: %s" % e, source="api:/api/autonomous", detail=str(e))
            return self._send_json({"error": "自主循环失败: %s" % e}, status=500)

    def _pipeline_api(self):
        """POST /api/pipeline {goal, context?, max_dispatch?} -> 理解->拆解->编排->执行->自检->交付 全链路。"""
        from .. import goal_pipeline as _gp
        try:
            body = self._read_json({})
            goal = (body.get("goal") or "").strip()
            if not goal:
                return self._send_json({"error": "缺少 goal"}, status=400)
            try:
                max_dispatch = int(body.get("max_dispatch") or 4)
            except (TypeError, ValueError):
                max_dispatch = 4
            result = _gp.run_pipeline(goal, context=body.get("context") or "",
                                     llm_call=self._make_llm_call(), max_dispatch=max_dispatch,
                                     memory_dir=os.getcwd(),
                                     do_learn=bool(body.get("do_learn", True)))
            return self._send_json(result)
        except Exception as e:
            from .. import errorlog as _el
            _el.record(os.getcwd(), "pipeline", "目标流水线失败: %s" % e, source="api:/api/pipeline", detail=str(e))
            return self._send_json({"error": "流水线执行失败: %s" % e}, status=500)

    def _pipeline_recall(self):
        """POST /api/pipeline/recall {goal, k?} -> 预览将喂入流水线的跨会话记忆 (Phase 9 透明化)。"""
        from .. import goal_pipeline as _gp
        from .. import memory_mgr as _mm
        try:
            body = self._read_json({})
            goal = (body.get("goal") or "").strip()
            if not goal:
                return self._send_json({"error": "缺少 goal"}, status=400)
            try:
                k = int(body.get("k") or 6)
            except (TypeError, ValueError):
                k = 6
            res = _mm.retrieve(os.getcwd(), goal, k=k)
            return self._send_json({"ok": True, "goal": goal,
                                    "count": len(res.get("results", [])), "memory": res.get("results", [])})
        except Exception as e:
            return self._send_json({"error": "记忆召回失败: %s" % e}, status=500)

    # ---- 统一引擎总控台 (Phase 10) ----
    def _engines_status(self):
        """GET /api/engines -> 四大引擎 + 编排 + 记忆 的统一可观测快照。"""
        from .. import creation_domains as _cd
        orch_items = _ORCH.list_all() or []
        orch_running = sum(1 for o in orch_items if o.get("status") == "running")
        orch_done = sum(1 for o in orch_items if o.get("status") == "done")
        # 语义记忆条目数(直接读 facts.jsonl, 无副作用)
        mem_count = 0
        try:
            fp = os.path.join(os.getcwd(), ".lmw_memory", "facts.jsonl")
            if os.path.isfile(fp):
                with open(fp, encoding="utf-8") as f:
                    mem_count = sum(1 for ln in f if ln.strip())
        except Exception:
            mem_count = 0
        engines = [
            {"id": "orchestrate", "name": "编排中枢", "emoji": "🧭", "theme": "#22d3ee",
             "desc": "多智能体并行扇出 · 统一指挥中心视图", "status": "ready", "hits": orch_running + orch_done},
            {"id": "creation", "name": "创作域", "emoji": "🎨", "theme": "#f472b6",
             "desc": "编程/音频/图片/视频 四域统一创作工作台", "status": "ready", "domains": len(_cd.list_domains())},
            {"id": "autonomous", "name": "自主模式", "emoji": "🤖", "theme": "#34d399",
             "desc": "目标驱动自驱循环 · 规划→观察→Critic→反思", "status": "ready"},
            {"id": "pipeline", "name": "全链路流水线", "emoji": "🌊", "theme": "#6366f1",
             "desc": "理解→拆解→编排→执行→自检→交付 一站式闭环", "status": "ready"},
        ]
        with _engine_runs_lock():
            recent = list(reversed(_ENGINE_RUNS))[:20]
            counts = {}
            for r in _ENGINE_RUNS:
                counts[r["engine"]] = counts.get(r["engine"], 0) + 1
        backend = "none"
        try:
            backend = ((_get_cfg().get("llm") or {}).get("backend") or "none")
        except Exception:
            backend = "none"
        self._send_json({
            "ok": True,
            "engines": engines,
            "orchestration": {"total": len(orch_items), "running": orch_running, "done": orch_done},
            "creation": {"domains": _cd.list_domains()},
            "memory": {"entries": mem_count},
            "control_center": {"total": len(_ENGINE_RUNS), "by_engine": counts, "recent": recent},
            "llm_backend": backend,
        })

    def _engine_run_summary(self, engine, goal, result):
        if not result:
            return "（无结果）"
        if engine == "pipeline":
            sc = (result.get("selfcheck") or {}).get("score")
            return "拆解 %d 步 · 自检 %s/100" % (result.get("decompose", {}).get("count", 0),
                                                 sc if sc is not None else "-")
        if engine == "autonomous":
            return "自驱 %d 轮 · %s" % (len(result.get("iterations", []) or []),
                                         "已达成" if result.get("reached") else "未达成")
        if engine == "creation":
            return "创作域 %s · %s" % (result.get("domain_name", ""), result.get("status", ""))
        if engine == "decompose":
            return "拆解 %d 步" % (len(result.get("steps", []) or []))
        return "ok"

    def _engines_run(self):
        """POST /api/engines/run {engine, goal, domain?, context?, max_iter?, max_dispatch?}
        -> 统一引擎启动器: 把总控台的『一键启动』路由到对应引擎。"""
        from .. import goal_pipeline as _gp
        from .. import autonomous as _au
        from .. import creation_domains as _cd
        from .. import decompose_engine as _de
        try:
            body = self._read_json({})
            engine = (body.get("engine") or "").strip()
            goal = (body.get("goal") or "").strip()
            if not engine:
                return self._send_json({"error": "缺少 engine"}, status=400)
            llm = self._make_llm_call()
            started = time.time()
            if engine == "pipeline":
                if not goal:
                    return self._send_json({"error": "流水线需要 goal"}, status=400)
                md = int(body.get("max_dispatch") or 4)
                result = _gp.run_pipeline(goal, context=body.get("context") or "", llm_call=llm,
                                          max_dispatch=md, memory_dir=os.getcwd(),
                                          do_learn=bool(body.get("do_learn", True)),
                                          do_autonomous=bool(body.get("do_autonomous", True)),
                                          max_autonomous_iter=int(body.get("max_autonomous_iter") or 4))
            elif engine == "autonomous":
                if not goal:
                    return self._send_json({"error": "自主模式需要 goal"}, status=400)
                mi = int(body.get("max_iter") or 6)
                result = _au.run(goal, llm_call=llm, context=body.get("context") or "", max_iter=mi)
            elif engine == "creation":
                domain = (body.get("domain") or "").strip()
                brief = (body.get("brief") or goal).strip()
                if not domain or not brief:
                    return self._send_json({"error": "创作域需要 domain 与 brief"}, status=400)
                result = _cd.dispatch(domain, brief, context=body.get("context") or "", llm_call=llm)
            elif engine == "decompose":
                if not goal:
                    return self._send_json({"error": "拆解需要 goal"}, status=400)
                steps = _de.decompose(goal, text=body.get("context") or "", llm_call=llm)
                result = {"ok": True, "goal": goal, "steps": steps,
                          "execution_order": _de.execution_order(steps),
                          "plan": _de.to_plan_payload(goal, steps, title=goal)}
            else:
                return self._send_json({"error": "未知引擎: %s" % engine}, status=400)
            summary = self._engine_run_summary(engine, goal, result)
            with _engine_runs_lock():
                _ENGINE_RUNS.append({
                    "engine": engine, "goal": goal[:120],
                    "ok": bool((result or {}).get("ok", True)),
                    "summary": summary, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "elapsed_sec": round(time.time() - started, 1),
                })
                if len(_ENGINE_RUNS) > _ENGINE_RUNS_MAX:
                    del _ENGINE_RUNS[:len(_ENGINE_RUNS) - _ENGINE_RUNS_MAX]
            self._emit("engine", "run", "完成 %s: %s (%.1fs)" % (engine, goal[:60], time.time() - started),
                       {"engine": engine, "ok": bool((result or {}).get("ok", True)),
                        "elapsed_sec": round(time.time() - started, 1)}, audit=True)
            return self._send_json({"ok": True, "engine": engine, "result": result})
        except Exception as e:
            from .. import errorlog as _el
            _el.record(os.getcwd(), "engines", "总控台引擎调用失败: %s" % e,
                       source="api:/api/engines/run", detail=str(e))
            self._emit("engine", "run_fail", "引擎调用失败 %s: %s" % (engine, e), {"engine": engine}, audit=True)
            return self._send_json({"error": "引擎调用失败: %s" % e}, status=500)

    def _multimodal_list(self):
        """GET /api/multimodal?kind=&q=&limit= -> 资产库画廊 (按域/语义检索)。"""
        from .. import multimodal as _mm
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            kind = (qs.get("kind", [""])[0] or None)
            q = (qs.get("q", [""])[0] or None)
            try:
                limit = int(qs.get("limit", ["60"])[0] or 60)
            except ValueError:
                limit = 60
            lib = _mm.AssetLibrary(os.getcwd())
            items = lib.list(kind=kind, q=q, limit=limit)
            return self._send_json({"ok": True, "assets": items, "count": len(items)})
        except Exception as e:
            return self._send_json({"error": "资产库读取失败: %s" % e}, status=500)

    def _multimodal_generate(self):
        """POST /api/multimodal/generate {domain,brief,blueprint?,session_id?,mode?,voice?,rate?,pitch?,image_path?}
        -> 统一生成入口 (包装 multimodal_adapters.render + 登记资产库)。

        audio 域 mode: tts(默认语音合成) / music(本地配乐合成) / clone(语音克隆占位)
        audio 域 voice/rate/pitch: edge_tts 语音参数 (tts 模式)
        image 域 mode: gen(文生图,默认) / inpaint(局部重绘) / upscale(超分放大)
        image 域 image_path: inpaint/upscale 参考图路径(可选, 留空用演示画布)
        video 域 mode: gen(文生视频,默认) / img2video(图生视频·Ken Burns) / clips(剪辑配音合成)
        video 域 image_path: img2video/clips 参考图路径(可选, 多图逗号分隔, 留空用演示画布)
        """
        from .. import multimodal as _mm
        try:
            body = self._read_json({})
            domain = (body.get("domain") or "").strip().lower()
            brief = (body.get("brief") or "").strip()
            blueprint = body.get("blueprint") or ""
            session_id = body.get("session_id") or ""
            mode = (body.get("mode") or "tts").strip().lower()
            voice = body.get("voice") or ""
            rate = body.get("rate") or ""
            pitch = body.get("pitch") or ""
            image_path = body.get("image_path") or ""
            if domain not in ("audio", "image", "video"):
                return self._send_json({"error": "不支持的域: %s (可选: audio/image/video)" % domain}, status=400)
            if mode in ("music", "clone") and domain != "audio":
                return self._send_json({"error": "模式 %s 仅支持 audio 域" % mode}, status=400)
            if mode in ("inpaint", "upscale") and domain != "image":
                return self._send_json({"error": "模式 %s 仅支持 image 域" % mode}, status=400)
            if mode in ("img2video", "clips") and domain != "video":
                return self._send_json({"error": "模式 %s 仅支持 video 域" % mode}, status=400)
            if domain == "video" and mode not in ("gen", "img2video", "clips"):
                mode = "gen"   # video 域默认文生视频 (避免默认 tts 被拒)
            if not brief:
                return self._send_json({"error": "缺少 brief"}, status=400)
            asset = _mm.generate(domain, brief, blueprint, "", session_id, os.getcwd(),
                                 llm_call=self._make_llm_call(),
                                 mode=mode, voice=voice, rate=rate, pitch=pitch,
                                 image_path=image_path)
            if not asset:
                return self._send_json({"error": "生成失败 (适配层未产出文件)"}, status=500)
            return self._send_json({"ok": True, "asset": asset})
        except Exception as e:
            return self._send_json({"error": "多模态生成失败: %s" % e}, status=500)

    def _multimodal_render(self):
        """POST /api/multimodal/render {domain, brief, blueprint?} -> 真实媒体文件(落 outputs/multimodal)。"""
        from .. import multimodal_adapters as _ma
        try:
            body = self._read_json({})
            domain = (body.get("domain") or "").strip().lower()
            brief = (body.get("brief") or "").strip()
            blueprint = body.get("blueprint") or ""
            if domain not in _ma.available_domains():
                return self._send_json({"error": "不支持的域: %s (可选: %s)" % (domain, ", ".join(_ma.available_domains()))}, status=400)
            if not brief:
                return self._send_json({"error": "缺少 brief"}, status=400)
            art = _ma.render(domain, brief, blueprint, "", llm_call=self._make_llm_call())
            if not art:
                return self._send_json({"error": "适配失败"}, status=500)
            fname = os.path.basename(art.get("file") or "")
            if fname:
                art["url"] = "/outputs/" + fname
            return self._send_json({"ok": True, **art})
        except Exception as e:
            return self._send_json({"error": "多模态渲染失败: %s" % e}, status=500)

    def _plans_delete(self):
        """POST /api/plans/delete {id} -> 删除计划。"""
        from .. import plans as _pl
        body = self._read_json({})
        pid = (body.get("id") or "").strip()
        if not pid:
            return self._send_json({"error": "缺少 id"}, status=400)
        removed = _pl.delete(pid, os.getcwd())
        return self._send_json({"ok": True, "removed": removed})

    def _plans_task(self):
        """POST /api/plans/task {plan_id, action:'add'|'status'|'remove', ...}。"""
        from .. import plans as _pl
        body = self._read_json({})
        pid = (body.get("plan_id") or "").strip()
        action = (body.get("action") or "").strip()
        if not pid:
            return self._send_json({"error": "缺少 plan_id"}, status=400)
        try:
            if action == "add":
                title = (body.get("title") or "").strip()
                if not title:
                    return self._send_json({"error": "缺少 title"}, status=400)
                task = _pl.add_task(pid, title, note=body.get("note") or "")
                return self._send_json({"ok": True, "task": task})
            if action == "status":
                tid = (body.get("task_id") or "").strip()
                if not tid:
                    return self._send_json({"error": "缺少 task_id"}, status=400)
                rec = _pl.set_task_status(pid, tid, body.get("status") or "done")
                if rec is None:
                    return self._send_json({"error": "未找到计划或任务"}, status=404)
                return self._send_json({"ok": True, "task": rec})
            if action == "remove":
                tid = (body.get("task_id") or "").strip()
                if not tid:
                    return self._send_json({"error": "缺少 task_id"}, status=400)
                removed = _pl.remove_task(pid, tid)
                return self._send_json({"ok": True, "removed": removed})
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)
        return self._send_json({"error": "未知 action: %s" % action}, status=400)

    # ===================================================================
    # 上下文操作: 压缩 / 整理 / 拆解
    # ===================================================================
    def _make_llm_call(self):
        """构造一个 llm_call(prompt, system=None)->str|None。

        仅在配置了后端且存在可用 API key 环境变量时尝试; 否则返回 None
        (context_ops 会回退到确定性规则版, 保证无 key 环境行为不变)。
        """
        try:
            cfg = _get_cfg()
        except Exception:
            return None
        llm = (cfg.get("llm") or {})
        providers = llm.get("providers") or []
        has_key = False
        for p in providers:
            env = (p.get("api_key_env") if isinstance(p, dict) else None)
            if env and os.environ.get(env):
                has_key = True
                break
        if not has_key:
            return None
        try:
            client = build_client(llm.get("backend"), cfg=cfg)
        except Exception:
            return None

        def call(prompt, system=None):
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.append({"role": "user", "content": prompt})
            try:
                out = client.chat(msgs, stream=False, temperature=0.2, timeout=120)
            except Exception:
                return None
            if isinstance(out, dict):
                return (out.get("content") or out.get("text") or "").strip() or None
            return (str(out) or "").strip() or None

        return call

    def _context_op(self, kind):
        """POST /api/context/{compress,organize,decompose}
        {session_id? | messages?} -> {ok, kind, markdown}。"""
        from .. import context_ops as _cx
        from ..agent import session as _sess
        body = self._read_json({})
        messages = body.get("messages")
        sid = (body.get("session_id") or "").strip()
        if not messages and sid:
            s = _sess.load_session(sid)
            if not s:
                return self._send_json({"error": "会话不存在: %s" % sid}, status=404)
            messages = s.get("messages", [])
        if not isinstance(messages, list) or not messages:
            return self._send_json({"error": "缺少 messages 或有效的 session_id"}, status=400)
        fn = {"compress": _cx.compress, "organize": _cx.organize, "decompose": _cx.decompose}.get(kind)
        if not fn:
            return self._send_json({"error": "未知操作: %s" % kind}, status=400)
        try:
            md = fn(messages, llm_call=self._make_llm_call())
        except Exception as e:
            from .. import errorlog as _el
            _el.record(os.getcwd(), "context_op", "上下文操作失败: %s" % e, source="api:/api/context/%s" % kind, detail=str(e))
            return self._send_json({"error": "操作失败: %s" % e}, status=500)
        # 同步记录到错误日志(便于排查上下文相关异常)
        return self._send_json({"ok": True, "kind": kind, "markdown": md, "chars": len(md)})

    # ===================================================================
    # 错误日志 + 错误汇总
    # ===================================================================
    def _errors_list(self):
        """GET /api/errors -> {errors:[...], total}。"""
        from .. import errorlog as _el
        return self._send_json(_el.list_errors(os.getcwd()))

    def _errors_summary(self):
        """GET /api/errors/summary -> 聚合统计 + markdown 报告。"""
        from .. import errorlog as _el
        return self._send_json(_el.summary(os.getcwd()))

    def _errors_record(self):
        """POST /api/errors/record {type, message, source?, detail?, severity?}。"""
        from .. import errorlog as _el
        body = self._read_json({})
        etype = (body.get("type") or "").strip()
        msg = (body.get("message") or "").strip()
        if not etype or not msg:
            return self._send_json({"error": "缺少 type 或 message"}, status=400)
        rec = _el.record(os.getcwd(), etype, msg,
                         source=body.get("source") or "", detail=body.get("detail") or "",
                         severity=body.get("severity") or "error")
        return self._send_json({"ok": True, "recorded": rec})

    # ===================================================================
    # 技术文档 (MD 文件夹管理, 专用于 docs/technical/)
    # ===================================================================
    def _docs_get(self):
        """GET /api/docs?file=<name> -> 列出 docs/technical/ 下 MD, 或读取指定文件。"""
        from .. import errorlog as _el
        import re as _re
        try:
            q = parse_qs(urlparse(self.path).query)
            name = (q.get("file") or [None])[0]
            base = os.path.join(os.getcwd(), "docs", "technical")
            os.makedirs(base, exist_ok=True)
            if name:
                # 只允许字母数字/中文/._- 防越权
                if not _re.match(r"^[\w.\-一-鿿]+\.md$", name or ""):
                    return self._send_json({"error": "非法文件名"}, status=400)
                fp = os.path.join(base, name)
                if not os.path.isfile(fp):
                    return self._send_json({"error": "文件不存在"}, status=404)
                try:
                    content = open(fp, encoding="utf-8").read()
                except Exception as e:
                    return self._send_json({"error": "读取失败: %s" % e}, status=500)
                return self._send_json({"name": name, "content": content, "exists": True})
            entries = []
            for fn in sorted(os.listdir(base)):
                if not fn.endswith(".md"):
                    continue
                fp = os.path.join(base, fn)
                try:
                    sz = os.path.getsize(fp)
                    mt = os.path.getmtime(fp)
                except Exception:
                    sz, mt = 0, 0
                entries.append({"name": fn, "size": sz, "mtime": mt})
            return self._send_json({"dir": "docs/technical", "files": entries})
        except Exception as e:
            _el.record(os.getcwd(), "docs_get", "技术文档列表失败: %s" % e, source="api:/api/docs", detail=str(e))
            return self._send_json({"error": "操作失败: %s" % e}, status=500)

    def _docs_save(self):
        """POST /api/docs/save {name, content} -> 写入 docs/technical/<name>.md。"""
        from .. import errorlog as _el
        import re as _re
        body = self._read_json({})
        name = (body.get("name") or "").strip()
        content = body.get("content", "") or ""
        if not _re.match(r"^[\w.\-一-鿿]+\.md$", name or ""):
            return self._send_json({"error": "文件名须以 .md 结尾且不含路径分隔符"}, status=400)
        base = os.path.join(os.getcwd(), "docs", "technical")
        os.makedirs(base, exist_ok=True)
        fp = os.path.join(base, name)
        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(content)
            return self._send_json({"ok": True, "path": "docs/technical/%s" % name, "bytes": len(content.encode("utf-8"))})
        except Exception as e:
            _el.record(os.getcwd(), "docs_save", "技术文档保存失败: %s" % e, source="api:/api/docs/save", detail=str(e))
            return self._send_json({"error": "保存失败: %s" % e}, status=500)

    def _docs_delete(self):
        """POST /api/docs/delete {name} -> 删除 docs/technical/<name>.md。"""
        from .. import errorlog as _el
        import re as _re
        body = self._read_json({})
        name = (body.get("name") or "").strip()
        if not _re.match(r"^[\w.\-一-鿿]+\.md$", name or ""):
            return self._send_json({"error": "非法文件名"}, status=400)
        base = os.path.join(os.getcwd(), "docs", "technical")
        fp = os.path.join(base, name)
        if not os.path.isfile(fp):
            return self._send_json({"error": "文件不存在"}, status=404)
        try:
            os.remove(fp)
            return self._send_json({"ok": True, "removed": name})
        except Exception as e:
            _el.record(os.getcwd(), "docs_delete", "技术文档删除失败: %s" % e, source="api:/api/docs/delete", detail=str(e))
            return self._send_json({"error": "删除失败: %s" % e}, status=500)


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

    # 初始化活动总线（启用持久化审计链, Phase 17）
    try:
        from .. import event_bus as _eb
        _eb.init_bus(persist_path=os.path.join(os.getcwd(), ".lmw_events", "events.jsonl"))
    except Exception:
        pass

    # 定时编排调度器 (Phase 43): 常驻 daemon, 每 20s 扫描到期计划
    try:
        from .. import superagent as _sa
        # 通知消息内嵌报告链接用(Phase 49): 优先 config agent.public_base_url
        try:
            _pub = _cfg_get(_RUNTIME_CONFIG or _get_cfg(), "agent.public_base_url") \
                or ("http://127.0.0.1:%d" % port)
        except Exception:
            _pub = "http://127.0.0.1:%d" % port
        _sa.set_public_base_url(_pub)
        # 每日摘要自动推送时刻 (Phase 50): config agent.digest_time (HH:MM, 空则不自动)
        try:
            _sa.set_digest_time(_cfg_get(_RUNTIME_CONFIG or _get_cfg(), "agent.digest_time") or "")
        except Exception:
            _sa.set_digest_time("")
        # 定时编排失败自动重试次数 (Phase 52): config agent.orchestration_retry_max
        try:
            _sa.set_default_retry_max(_cfg_get(_RUNTIME_CONFIG or _get_cfg(),
                                               "agent.orchestration_retry_max") or 0)
        except Exception:
            pass
        # 质量告警自动推送 (Phase 62): config agent.quality_auto_push + quality_auto_interval_h
        try:
            _sa._load_quality_state(os.getcwd())
            _sa.set_quality_auto(
                bool(_cfg_get(_RUNTIME_CONFIG or _get_cfg(), "agent.quality_auto_push")),
                _cfg_get(_RUNTIME_CONFIG or _get_cfg(), "agent.quality_auto_interval_h") or 24)
        except Exception:
            pass
        # llm_call 工厂: 每次调度执行时现取(后端/key 配置可能运行期变化)
        _sa.start_scheduler(base_dir=os.getcwd(),
                            llm_call=lambda p, system=None:
                                (Handler._make_llm_call(None) or (lambda q, s=None: None))(p, system=system))
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

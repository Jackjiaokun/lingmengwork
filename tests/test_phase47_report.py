"""Phase 47 · 编排报告页面化测试.

覆盖:
- _render_orch_report 渲染: 关键区块(目标/7阶段/伙伴/护栏/用量) + XSS 转义
- GET /api/superagent/report?ts= e2e: 200 text/html + 内容; 缺 ts 400; 未知 ts 404
- 页面历史行含「报告」按钮
"""

import http.client
import json
import os
import tempfile
import threading
import time

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


@pytest.fixture
def fast_executors(monkeypatch):
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [], "note": ""})
                         for d in ("code", "creation", "research", "ops")})


def test_render_orch_report_sections():
    result = {
        "ok": True, "goal": "研究<分析>竞品 & 部署监控",
        "intent": {"intent": "E2E 巡检", "domains": ["research", "ops"],
                   "constraints": ["中文"], "memory_recap": "历史经验内容"},
        "routed": ["research", "ops"],
        "dispatch": {"partners": [
            {"name": "研究伙伴", "domain": "research", "status": "ok", "summary": "简报内容"},
            {"name": "运维伙伴", "domain": "ops", "status": "error", "summary": ""}],
            "matched_connectors": [
                {"name": "http_probe", "ok": True, "result": {"status_code": 200}}]},
        "executions": {"artifacts": ["/tmp/a.md", "/tmp/b.md"]},
        "converge": {"partners_ok": 1, "partners_total": 2, "selfcheck_score": 96,
                     "guards": [{"level": 1, "kind": "partner_error", "severity": "warning",
                                 "msg": "有 1 个伙伴执行异常"}]},
        "memory": {"entities_added": 3, "relations_added": 1, "facts_count": 4},
        "usage": {"llm_calls": 5, "est_total_tokens": 1200, "est_cost_cny": 0.0002},
        "trace": [{"stage": s, "ts": "2026-08-28 10:00:00", "ok": True, "detail": "d"}
                  for s in sa_mod._STAGE_NAMES],
        "elapsed_sec": 3.2,
    }
    html = _srv._render_orch_report(result)
    for kw in ("超级 AGENT 编排报告", "研究&lt;分析&gt;竞品 &amp; 部署监控",  # XSS 转义
               "📜 阶段 Trace", "🤝 并行编排", "🛡️ 收敛护栏", "📦 执行产物",
               "🧠 记忆沉淀", "💰 LLM 用量", "编排成功", "http_probe",
               "研究伙伴", "运维伙伴", "partner_error"):
        assert kw in html, "报告应含区块: %s" % kw
    assert "研究<分析>" not in html
    # 失败路径渲染
    html2 = _srv._render_orch_report({"ok": False, "goal": "g", "error": "RuntimeError: boom",
                                      "trace": [], "elapsed_sec": 0.1})
    assert "编排失败" in html2 and "RuntimeError: boom" in html2


def test_report_api_e2e(fast_executors):
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9106), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", 9106, timeout=20)
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace"), dict(r.getheaders())

        # 造一次编排
        c = http.client.HTTPConnection("127.0.0.1", 9106, timeout=30)
        c.request("POST", "/api/superagent/run",
                  body=json.dumps({"goal": "研究分析竞品趋势并部署监控"}).encode(),
                  headers={"Content-Type": "application/json"})
        json.loads(c.getresponse().read().decode())

        _, js, _ = get("/api/superagent")
        runs = json.loads(js)["runs"]
        ts = runs[0]["ts"]

        st, body, hdrs = get("/api/superagent/report?ts=" + ts.replace(" ", "%20"))
        assert st == 200
        assert (hdrs.get("Content-Type") or "").startswith("text/html")
        assert "超级 AGENT 编排报告" in body
        assert "研究分析竞品趋势并部署监控" in body

        st, _, _ = get("/api/superagent/report")
        assert st == 400
        st, _, _ = get("/api/superagent/report?ts=1970-01-01%2000:00:00")
        assert st == 404
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_report_button():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "/api/superagent/report?ts=" in html
    assert "📄 报告" in html

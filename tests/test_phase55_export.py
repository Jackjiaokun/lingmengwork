"""Phase 55 · 编排结果结构化导出测试.

覆盖:
- _render_orch_md 渲染结构(标题/目标/trace 表/伙伴表/护栏/产物/用量)
- 表格单元格转义(管道符 | 与换行不破坏 Markdown 表格)
- API GET /api/superagent/export?ts=&fmt=md|json (Content-Type / 附件头 / 404)
- 非法 fmt / 缺 ts 的 400
- API GET /api/superagent/export/bundle -> zip 含 md+json+README
- 页面含导出工具条(exportBar / copyOrchMd)与历史行三类导出按钮
"""

import http.client
import io
import json
import os
import threading
import time
import zipfile
from urllib.parse import quote

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    monkeypatch.setattr(sa_mod, "_RUNS", __import__("collections").deque(maxlen=200))
    yield


def _sample_result():
    return {
        "ts": "2026-08-28 10:00:00",
        "goal": "做一个带 | 竖线 的目标",
        "ok": True,
        "elapsed_sec": 3.5,
        "routed": ["code", "research"],
        "intent": {"intent": "实现功能", "domains": ["code"], "constraints": ["零依赖"]},
        "trace": [
            {"stage": "理解", "ts": "T1", "detail": "含|竖线 与\n换行", "ok": True},
            {"stage": "执行", "ts": "T2", "detail": "ok", "ok": False},
        ],
        "dispatch": {"partners": [
            {"name": "码农 | 甲", "domain": "code", "status": "ok", "summary": "写完了\n真的"},
            {"name": "研究员", "domain": "research", "status": "error", "summary": "失败"},
        ]},
        "converge": {"selfcheck_score": 92, "guards": [
            {"level": 2, "kind": "conflict", "msg": "两处方案冲突"}]},
        "executions": {"artifacts": ["outputs/superagent/a.py"]},
        "memory": {"entities_added": 3, "relations_added": 2},
        "usage": {"llm_calls": 7, "est_total_tokens": 1234, "est_cost_cny": 0.0123},
    }


def _write_run(base_dir, result):
    """按 _persist_result 的行结构写: {"ts","summary","result"}。"""
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    summary = {"goal": result.get("goal", ""), "ts": result.get("ts", ""),
               "ok": bool(result.get("ok")), "elapsed_sec": result.get("elapsed_sec", 0)}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": result.get("ts", ""), "summary": summary,
                            "result": result}, ensure_ascii=False) + "\n")


def test_render_md_structure():
    md = _srv._render_orch_md(_sample_result())
    assert md.startswith("# 灵梦work · 超级 AGENT 编排报告")
    for sec in ("## 🧭 目标理解", "## 📜 阶段 Trace", "## 🤝 并行编排",
                "## 🛡️ 收敛护栏", "## 📦 执行产物"):
        assert sec in md, "缺章节: " + sec
    assert "✅ 成功" in md and "自检分" in md
    assert "outputs/superagent/a.py" in md
    assert "1234" in md and "0.0123" in md
    # 表格存在表头分隔符
    assert "|---|------|------|------|" in md


def test_render_md_escapes_pipes_and_newlines():
    md = _srv._render_orch_md(_sample_result())
    # 原始内容里的管道符必须转义, 换行必须折叠 —— 表格结构不能被撑破
    assert "含\\|竖线" in md, "trace detail 的管道符未转义"
    assert "码农 \\| 甲" in md, "伙伴名的管道符未转义"
    # 每个表格数据行的列数必须等于表头列数(管道符转义后不会多出列)
    for header in ("| # | 阶段 | 时间 | 明细 |", "| 伙伴 | 域 | 状态 | 产出 |"):
        assert header in md
    # 4 列的数据行应为恰好 5 个「真实」分隔符(转义 \| 不算)
    def real_pipes(s):
        return s.count("|") - s.count("\\|")
    lines = md.splitlines()
    idx = lines.index("|---|------|------|------|")
    row = lines[idx + 1]
    assert real_pipes(row) == 5, "trace 行被管道符撑破: " + row
    assert "\n" not in row
    prow = [l for l in lines if l.startswith("| 码农")][0]
    assert real_pipes(prow) == 5, "伙伴行被撑破: " + prow
    # 换行已被折叠为空格
    assert "写完了 真的" in md
    assert "含|竖线" not in md.replace("含\\|竖线", "")


def test_render_md_empty_result_is_safe():
    md = _srv._render_orch_md({})
    assert "灵梦work" in md
    assert "| — | — | — | — |" in md, "无伙伴时应有占位行"
    assert "三级护栏全通过" in md


def test_export_md_and_json_e2e(tmp_path):
    res = _sample_result()
    _write_run(tmp_path, res)
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9116), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    ts = res["ts"]
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9116, timeout=15)

        # Markdown
        c.request("GET", "/api/superagent/export?ts=" + quote(ts) + "&fmt=md")
        r = c.getresponse()
        body = r.read().decode("utf-8")
        ctype = r.getheader("Content-Type") or ""
        disp = r.getheader("Content-Disposition") or ""
        assert r.status == 200, body
        assert "text/markdown" in ctype
        assert "attachment" in disp and disp.endswith('.md"')
        assert "超级 AGENT 编排报告" in body

        # JSON
        c.request("GET", "/api/superagent/export?ts=" + quote(ts) + "&fmt=json")
        r = c.getresponse()
        body = r.read().decode("utf-8")
        ctype = r.getheader("Content-Type") or ""
        disp = r.getheader("Content-Disposition") or ""
        assert "application/json" in ctype
        assert "attachment" in disp and disp.endswith('.json"')
        back = json.loads(body)
        assert back["goal"] == res["goal"]
        assert back["dispatch"]["partners"][0]["name"] == "码农 | 甲"
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_export_bad_params(tmp_path):
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9117), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9117, timeout=15)
        c.request("GET", "/api/superagent/export?ts=" + quote("2026-08-28 10:00:00") + "&fmt=pdf")
        r = c.getresponse()
        data = json.loads(r.read().decode())
        assert r.status == 400 and "fmt" in (data.get("error") or "")

        c.request("GET", "/api/superagent/export?fmt=md")
        r = c.getresponse()
        data = json.loads(r.read().decode())
        assert r.status == 400 and "ts" in (data.get("error") or "")

        c.request("GET", "/api/superagent/export?ts=" + quote("2099-01-01 00:00:00") + "&fmt=md")
        r = c.getresponse()
        data = json.loads(r.read().decode())
        assert r.status == 404
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_export_bundle_zip(tmp_path):
    res = _sample_result()
    _write_run(tmp_path, res)
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9118), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9118, timeout=15)
        c.request("GET", "/api/superagent/export/bundle?ts=" + quote(res["ts"]))
        r = c.getresponse()
        raw = r.read()
        assert r.status == 200 and (r.getheader("Content-Type") or "") == "application/zip"
        assert "attachment" in (r.getheader("Content-Disposition") or "")
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = z.namelist()
        assert any(n.endswith(".md") for n in names), names
        assert any(n.endswith(".json") for n in names), names
        assert "README.txt" in names
        md = z.read([n for n in names if n.endswith(".md")][0]).decode("utf-8")
        assert "超级 AGENT 编排报告" in md
        js = json.loads(z.read([n for n in names if n.endswith(".json")][0]).decode("utf-8"))
        assert js["ts"] == res["ts"]

        # 缺 ts -> 400
        c.request("GET", "/api/superagent/export/bundle")
        r = c.getresponse()
        assert r.status == 400
        r.read()
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_export_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "exportBar" in html and "renderExportBar" in html and "copyOrchMd" in html
    assert "/api/superagent/export?ts=" in html
    assert "/api/superagent/export/bundle?ts=" in html
    assert "结构化导出" in html

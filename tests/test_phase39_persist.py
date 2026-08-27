"""Phase 39 · 编排历史持久化测试.

覆盖:
- run 后 JSONL 落盘(行结构 ts/summary/result, result 含全 7 阶段 trace)
- 模拟重启(清空 _RUNS)后 get_recent_runs 从磁盘回看历史
- get_run_detail 按 ts 取完整结果; 未找到返回 None
- 超长结果体积保护(截断后仍 <64KB 且可解析)
- GET /api/superagent/detail?ts= 端到端(含缺 ts 400 / 不存在 404)
- 页面含回看 UI(loadRunDetail)
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


def _fast_exec(domain):
    def fn(partner, goal="", llm_call=None, base_dir=None):
        return {"domain": domain, "status": "ok", "artifacts": [], "note": "fast"}
    return fn


@pytest.fixture
def fast_executors(monkeypatch):
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: _fast_exec(d) for d in ("code", "creation", "research", "ops")})


def test_persist_jsonl_written(tmp_path, fast_executors):
    """run 后 JSONL 落盘: 行可解析, summary 与 result 完整。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("研究分析竞品趋势并部署监控", session_id="p39", quality_gate=False)
    assert rep["ok"] is True
    path = os.path.join(str(tmp_path), "outputs", "superagent_runs.jsonl")
    assert os.path.isfile(path), "JSONL 应落盘"
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    assert rows, "至少一行记录"
    row = rows[-1]
    assert row["summary"]["goal"] == "研究分析竞品趋势并部署监控"
    assert row["summary"]["ok"] is True
    assert row["summary"]["ts"] == [r for r in sa_mod._RUNS if r["goal"] == row["summary"]["goal"]][-1]["ts"]
    stages = [t["stage"] for t in row["result"]["trace"]]
    assert stages == sa_mod._STAGE_NAMES, "result 应含完整 7 阶段 trace"


def test_recent_runs_survive_restart(tmp_path, fast_executors):
    """模拟重启(清空内存缓冲): get_recent_runs 从磁盘回看历史。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("研究分析竞品趋势并部署监控", session_id="p39b", quality_gate=False)
    assert rep["ok"] is True
    saved = sa_mod._RUNS
    try:
        sa_mod._RUNS = __import__("collections").deque(maxlen=60)  # 模拟重启清空
        runs = sa_mod.get_recent_runs(20, base_dir=str(tmp_path))
        assert runs, "重启后应能从磁盘回看历史"
        assert any(r["goal"] == "研究分析竞品趋势并部署监控" for r in runs)
        # ts 倒序
        tss = [r["ts"] for r in runs]
        assert tss == sorted(tss, reverse=True)
        # 详情可回看
        detail = sa_mod.get_run_detail(runs[0]["ts"], base_dir=str(tmp_path))
        assert detail is not None
        assert [t["stage"] for t in detail["trace"]] == sa_mod._STAGE_NAMES
    finally:
        sa_mod._RUNS = saved


def test_run_detail_not_found(tmp_path):
    assert sa_mod.get_run_detail("1970-01-01 00:00:00", base_dir=str(tmp_path)) is None


def test_persist_size_guard(tmp_path):
    """超长结果: 截断保护, 落盘行 <64KB 且可解析。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    big = {"goal": "超大目标", "ts": "2026-08-28 00:00:00", "ok": True,
           "dispatch": {"partners": [{"domain": "code", "plan": "x" * 300000}]},
           "trace": [{"stage": "目标理解", "ts": "t", "ok": True, "detail": "d"}]}
    sa._persist_result(big["ts"], {"goal": big["goal"], "ts": big["ts"], "ok": True}, big)
    path = os.path.join(str(tmp_path), "outputs", "superagent_runs.jsonl")
    lines = [l for l in open(path, encoding="utf-8") if l.strip()]
    assert len(lines) == 1
    assert len(lines[0].encode("utf-8")) < sa_mod._PERSIST_MAX_BYTES, "落盘行应 <64KB"
    row = json.loads(lines[0])
    assert row["result"]["trace"][0]["stage"] == "目标理解"  # 重载荷截断但结构保留


def test_detail_api_e2e(monkeypatch):
    """GET /api/superagent/detail?ts= 端到端: 正常回看 / 缺 ts 400 / 不存在 404。"""
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: _fast_exec(d) for d in ("code", "creation", "research", "ops")})
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8991
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", PORT), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        # 造一次编排
        st, _ = get("/api/superagent")
        assert st == 200
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        c.request("POST", "/api/superagent/run",
                  body=json.dumps({"goal": "研究分析竞品趋势并部署监控"}).encode(),
                  headers={"Content-Type": "application/json"})
        rep = json.loads(c.getresponse().read().decode())
        assert rep["ok"], rep.get("error")

        # 概览里有 ts
        st, js = get("/api/superagent")
        runs = json.loads(js)["runs"]
        assert runs and runs[0].get("ts"), "概览应含 ts"
        ts = runs[0]["ts"]

        # 详情回看
        st, js = get("/api/superagent/detail?ts=" + ts.replace(" ", "%20"))
        assert st == 200, js
        det = json.loads(js)
        assert det["ok"] is True
        assert [t["stage"] for t in det["result"]["trace"]] == sa_mod._STAGE_NAMES

        # 缺 ts -> 400; 不存在 -> 404
        st, _ = get("/api/superagent/detail")
        assert st == 400
        st, _ = get("/api/superagent/detail?ts=1970-01-01%2000:00:00")
        assert st == 404
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_history_review_ui():
    """页面应含回看 UI: loadRunDetail / detail API 调用 / 记录可点击。"""
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "loadRunDetail" in html
    assert "/api/superagent/detail" in html
    assert "onclick=" in html

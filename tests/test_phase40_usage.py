"""Phase 40 · 编排成本入账测试.

覆盖:
- mock LLM 编排: result["usage"] 记录 llm_calls/token/成本(同 loop 1.6 字符/token 口径)
- 无 LLM: usage 全零(llm_calls=0)
- summary(_RUNS) 与 JSONL 持久化均携带用量字段
- get_usage_totals 聚合(内存 + 重启后磁盘)
- GET /api/cost 含 superagent 聚合段; cost.html 含渲染区块
"""

import collections
import json
import os

import pytest

from lingmengwork import superagent as sa_mod


def make_llm():
    def llm(prompt, system=None):
        s = system or ""
        if "任务理解器" in s:
            return json.dumps({"intent": "x", "domains": ["research", "ops"],
                               "constraints": []}, ensure_ascii=False)
        if "知识图谱抽取器" in s:
            return json.dumps({"entities": [], "relations": []})
        return "这是一段用于计量的输出文本, 长度足够产生非零 token 估算。" * 3
    return llm


@pytest.fixture
def fast_executors(monkeypatch):
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [], "note": ""})
                         for d in ("code", "creation", "research", "ops")})


def test_run_usage_counted_with_mock_llm(tmp_path, fast_executors, monkeypatch):
    monkeypatch.setattr(sa_mod._sc, "run",
                        lambda: {"ok": True, "score": 95, "passed": 13, "total": 13,
                                 "all_ok": True, "checks": [], "ts": "t"})
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("研究分析竞品趋势并部署监控", session_id="p40",
                 llm_call=make_llm(), quality_gate=True, model="sensenova-6.8-flash-lite")
    u = rep["usage"]
    assert u["llm_calls"] >= 3, "理解+伙伴+记忆至少 3 次 LLM 调用"
    assert u["est_output_tokens"] > 0
    assert u["est_total_tokens"] > 0
    assert u["est_cost_cny"] > 0, "按价格档应产生非零估算成本"
    assert u["model"] == "sensenova-6.8-flash-lite"


def test_run_usage_zero_without_llm(tmp_path, fast_executors):
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("研究分析竞品趋势并部署监控", session_id="p40b", quality_gate=False)
    u = rep["usage"]
    assert u["llm_calls"] == 0
    assert u["est_total_tokens"] == 0
    assert u["est_cost_cny"] == 0.0


def test_summary_and_persist_carry_usage(tmp_path, fast_executors):
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("研究分析竞品趋势并部署监控", session_id="p40c",
                 llm_call=make_llm(), quality_gate=False)
    last = sa_mod._RUNS[-1]
    assert last["goal"] == rep["goal"]
    assert last["llm_calls"] == rep["usage"]["llm_calls"]
    assert last["est_input_tokens"] == rep["usage"]["est_input_tokens"]
    assert last["est_cost_cny"] == rep["usage"]["est_cost_cny"]
    path = os.path.join(str(tmp_path), "outputs", "superagent_runs.jsonl")
    row = json.loads([l for l in open(path, encoding="utf-8") if l.strip()][-1])
    assert row["summary"]["est_total_tokens"] == rep["usage"]["est_total_tokens"]


def test_usage_totals_survive_restart(tmp_path, fast_executors):
    """隔离内存后: 聚合 = 本次 run; 模拟重启(清空内存)后磁盘聚合结果不变。"""
    saved = sa_mod._RUNS
    sa_mod._RUNS = collections.deque(maxlen=60)  # 隔离: 清空全文件共享的内存缓冲
    try:
        sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
        rep = sa.run("研究分析竞品趋势并部署监控", session_id="p40d",
                     llm_call=make_llm(), quality_gate=False)
        before = sa_mod.get_usage_totals(base_dir=str(tmp_path))
        assert before["runs"] == 1
        assert before["llm_calls"] == rep["usage"]["llm_calls"]
        assert before["est_total_tokens"] == rep["usage"]["est_total_tokens"]
        sa_mod._RUNS = collections.deque(maxlen=60)  # 模拟重启
        after = sa_mod.get_usage_totals(base_dir=str(tmp_path))
        assert after == before, "重启后应从磁盘聚合出相同用量"
    finally:
        sa_mod._RUNS = saved


def test_cost_api_has_superagent_section(monkeypatch, tmp_path, fast_executors):
    """GET /api/cost 应含 superagent 聚合段(runs/llm_calls/token/成本)。"""
    import tempfile
    import threading
    import time
    import http.client
    from lingmengwork.web import server as _srv
    monkeypatch.setattr(sa_mod._sc, "run",
                        lambda: {"ok": True, "score": 95, "passed": 13, "total": 13,
                                 "all_ok": True, "checks": [], "ts": "t"})
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 8992), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        # 造一次带 LLM 的编排
        c = http.client.HTTPConnection("127.0.0.1", 8992, timeout=30)
        c.request("POST", "/api/superagent/run",
                  body=json.dumps({"goal": "研究分析竞品趋势并部署监控"}).encode(),
                  headers={"Content-Type": "application/json"})
        json.loads(c.getresponse().read().decode())
        c2 = http.client.HTTPConnection("127.0.0.1", 8992, timeout=15)
        c2.request("GET", "/api/cost")
        data = json.loads(c2.getresponse().read().decode())
        sa_sec = data.get("superagent") or {}
        assert sa_sec.get("runs", 0) >= 1, "成本 API 应含编排用量聚合段"
        assert "llm_calls" in sa_sec and "est_total_tokens" in sa_sec
        assert "est_cost_cny" in sa_sec
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_cost_page_has_superagent_section():
    path = os.path.join(os.path.dirname(sa_mod.__file__), "web", "static", "cost.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "超级 AGENT 编排用量" in html
    assert "sa-runs" in html and "sa-cost" in html
    assert "d.superagent" in html

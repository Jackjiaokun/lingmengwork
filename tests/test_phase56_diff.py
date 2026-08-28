"""Phase 56 · 编排结果 A/B 对比测试.

覆盖:
- diff_runs 指标 delta 与 improved 方向(耗时/成本越低越好, 自检分越高越好)
- 路由域 / 产物 的 added/removed/same
- 伙伴逐项配对(状态变化 / 仅 A / 仅 B / 一致)
- verdict 改善/退化/持平/有得有失
- 缺记录返回 None
- API GET /api/superagent/diff (200/400/404)
- API GET /api/superagent/diff/report -> HTML 报告含结论与 A/B
- 页面含对比 UI (diffA/diffB/runDiff/renderDiff)
"""

import http.client
import json
import os
import threading
import time
from urllib.parse import quote

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    monkeypatch.setattr(sa_mod, "_RUNS", __import__("collections").deque(maxlen=200))
    yield


def _run(ts, goal="同一目标", ok=True, elapsed=5.0, score=80, routed=("code",),
         partners=(("码农", "code", "ok"),), guards=0, conflicts=0,
         artifacts=("out/a.py",), calls=4, tokens=800, cost=0.008):
    return {
        "ts": ts, "goal": goal, "ok": ok, "elapsed_sec": elapsed,
        "routed": list(routed),
        "dispatch": {"partners": [
            {"name": n, "domain": d, "status": s, "summary": "sum:" + n}
            for n, d, s in partners]},
        "converge": {"selfcheck_score": score,
                     "guards": [{"level": 2, "kind": "k", "msg": "m"}] * guards,
                     "conflicts": [{"a": "x"}] * conflicts},
        "executions": {"artifacts": list(artifacts)},
        "usage": {"llm_calls": calls, "est_total_tokens": tokens,
                  "est_cost_cny": cost},
    }


def _write(base_dir, *results):
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({"ts": r["ts"],
                                "summary": {"goal": r["goal"], "ts": r["ts"],
                                            "ok": r["ok"]},
                                "result": r}, ensure_ascii=False) + "\n")


TS_A, TS_B = "2026-08-28 10:00:00", "2026-08-28 11:00:00"


def test_diff_metrics_direction(tmp_path):
    _write(tmp_path,
           _run(TS_A, elapsed=10.0, score=70, calls=8, tokens=1600, cost=0.016),
           _run(TS_B, elapsed=5.0, score=90, calls=4, tokens=800, cost=0.008))
    d = sa_mod.diff_runs(TS_A, TS_B, base_dir=str(tmp_path))
    assert d is not None
    m = {x["key"]: x for x in d["metrics"]}
    # 耗时/成本/tokens/calls: 越低越好 -> B 更小 = 改善
    for k in ("elapsed_sec", "cost", "tokens", "llm_calls"):
        assert m[k]["delta"] < 0, k
        assert m[k]["improved"] is True, k
        assert m[k]["higher_better"] is False, k
    # 自检分: 越高越好 -> B 更大 = 改善
    assert m["score"]["delta"] == 20 and m["score"]["improved"] is True
    assert m["score"]["higher_better"] is True
    assert d["verdict"] == "改善"


def test_diff_regression_verdict(tmp_path):
    _write(tmp_path,
           _run(TS_A, elapsed=2.0, score=95, cost=0.001),
           _run(TS_B, elapsed=9.0, score=60, cost=0.05))
    d = sa_mod.diff_runs(TS_A, TS_B, base_dir=str(tmp_path))
    assert d["verdict"] == "退化"


def test_diff_mixed_and_flat(tmp_path):
    # 有得有失: 耗时变好(lower better) 但自检分变差(higher better)
    _write(tmp_path,
           _run(TS_A, elapsed=10.0, score=90),
           _run(TS_B, elapsed=5.0, score=70))
    assert sa_mod.diff_runs(TS_A, TS_B, base_dir=str(tmp_path))["verdict"] == "有得有失"

    # 完全一致 -> 持平
    p2 = tmp_path / "flat"
    p2.mkdir()
    _write(p2, _run(TS_A), _run(TS_B))
    d = sa_mod.diff_runs(TS_A, TS_B, base_dir=str(p2))
    assert d["verdict"] == "持平"
    assert all(x["delta"] == 0 for x in d["metrics"])
    assert d["goal_changed"] is False


def test_diff_routed_and_artifacts(tmp_path):
    _write(tmp_path,
           _run(TS_A, routed=("code", "ops"), artifacts=("out/a.py", "out/old.txt")),
           _run(TS_B, routed=("code", "research"), artifacts=("out/a.py", "out/new.md")))
    d = sa_mod.diff_runs(TS_A, TS_B, base_dir=str(tmp_path))
    assert d["routed"]["added"] == ["research"]
    assert d["routed"]["removed"] == ["ops"]
    assert d["routed"]["same"] == ["code"]
    assert d["artifacts"]["added"] == ["out/new.md"]
    assert d["artifacts"]["removed"] == ["out/old.txt"]
    assert d["artifacts"]["same"] == ["out/a.py"]


def test_diff_partners_pairing(tmp_path):
    _write(tmp_path,
           _run(TS_A, partners=(("码农", "code", "ok"), ("老伙伴", "ops", "ok"))),
           _run(TS_B, partners=(("码农", "code", "error"), ("新伙伴", "research", "ok"))))
    d = sa_mod.diff_runs(TS_A, TS_B, base_dir=str(tmp_path))
    ps = {(p["name"], p["domain"]): p for p in d["partners"]}
    assert ps[("码农", "code")]["changed"] is True
    assert ps[("码农", "code")]["only_in"] == ""
    assert ps[("老伙伴", "ops")]["only_in"] == "a"
    assert ps[("新伙伴", "research")]["only_in"] == "b"
    # 顺序稳定: 先列 A 的全部, 再列仅 B 的
    assert [p["name"] for p in d["partners"]][:2] == ["码农", "老伙伴"]


def test_diff_missing_returns_none(tmp_path):
    _write(tmp_path, _run(TS_A))
    assert sa_mod.diff_runs(TS_A, "2099-01-01 00:00:00", base_dir=str(tmp_path)) is None
    assert sa_mod.diff_runs("2099-01-01 00:00:00", TS_A, base_dir=str(tmp_path)) is None


def test_diff_api_and_report_e2e(tmp_path):
    _write(tmp_path,
           _run(TS_A, elapsed=10.0, score=70, routed=("code", "ops")),
           _run(TS_B, elapsed=5.0, score=90, routed=("code", "research"),
                goal="另一个目标"))
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9119), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9119, timeout=15)

        # JSON diff
        c.request("GET", "/api/superagent/diff?a=%s&b=%s" % (quote(TS_A), quote(TS_B)))
        r = c.getresponse()
        data = json.loads(r.read().decode())
        assert r.status == 200 and data["ok"] is True
        assert data["diff"]["verdict"] == "改善"
        assert data["diff"]["goal_changed"] is True
        assert data["diff"]["routed"]["added"] == ["research"]

        # 缺参数 -> 400
        c.request("GET", "/api/superagent/diff?a=%s" % quote(TS_A))
        r = c.getresponse()
        assert r.status == 400 and "a/b" in (json.loads(r.read().decode()).get("error") or "")

        # 记录不存在 -> 404
        c.request("GET", "/api/superagent/diff?a=%s&b=%s"
                  % (quote(TS_A), quote("2099-01-01 00:00:00")))
        r = c.getresponse()
        assert r.status == 404
        r.read()

        # HTML 报告
        c.request("GET", "/api/superagent/diff/report?a=%s&b=%s"
                  % (quote(TS_A), quote(TS_B)))
        r = c.getresponse()
        html = r.read().decode("utf-8")
        assert r.status == 200 and "text/html" in (r.getheader("Content-Type") or "")
        assert html.lstrip().startswith("<!doctype html")
        assert "编排对比报告" in html and "改善" in html
        assert TS_A in html and TS_B in html
        assert "另一个目标" in html
        assert "目标不一致" in html, "目标不同时应给出警示"
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_diff_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    for token in ("diffA", "diffB", "diffBox", "runDiff", "renderDiff",
                  "fillDiffSelects", "diffReportLink", "编排对比"):
        assert token in html, "页面缺: " + token
    assert "/api/superagent/diff" in html
    # 0 值不能被 esc 吞掉
    assert "escv" in html

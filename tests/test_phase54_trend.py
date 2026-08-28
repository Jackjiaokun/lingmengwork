"""Phase 54 · 编排指标趋势图测试.

覆盖:
- get_daily_trend 按日聚合(跨两日数据/成功率/均值/升序)
- days 钳位与空态
- API GET /api/superagent/trend?days=
- 页面含趋势区块(trendBox/loadTrend)
"""

import http.client
import json
import os
import threading
import time

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(sa_mod, "_SCHEDS", {})
    monkeypatch.setattr(sa_mod, "_SCHEDS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    yield


def _write_runs(base_dir, entries):
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for ts, ok in entries:
            f.write(json.dumps({
                "ts": ts,
                "summary": {"goal": "g", "ts": ts, "ok": ok, "selfcheck_score": 90,
                            "elapsed_sec": 2.0, "llm_calls": 3,
                            "est_total_tokens": 50, "est_cost_cny": 0.0002}}) + "\n")


def test_daily_trend_aggregation(tmp_path):
    _write_runs(tmp_path, [
        ("2026-08-27 10:00:00", True),
        ("2026-08-27 11:00:00", False),
        ("2026-08-28 09:00:00", True),
        ("2026-08-28 09:30:00", True),
    ])
    trend = sa_mod.get_daily_trend(14, base_dir=str(tmp_path))
    assert [d["date"] for d in trend] == ["2026-08-27", "2026-08-28"], "应按日期升序"
    d27, d28 = trend
    assert (d27["total"], d27["ok"], d27["fail"]) == (2, 1, 1)
    assert d27["success_rate"] == 50.0
    assert (d28["total"], d28["ok"]) == (2, 2) and d28["success_rate"] == 100.0
    assert d28["avg_elapsed"] == 2.0
    assert d28["cost"] > 0


def test_daily_trend_empty_and_clamp(tmp_path):
    assert sa_mod.get_daily_trend(14, base_dir=str(tmp_path)) == []
    assert sa_mod.get_daily_trend(99999, base_dir=str(tmp_path)) == []
    assert sa_mod.get_daily_trend("abc", base_dir=str(tmp_path)) == []


def test_trend_api_e2e(tmp_path):
    d = str(tmp_path)
    _write_runs(d, [("2026-08-28 10:00:00", True)])
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9115), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9115, timeout=15)
        c.request("GET", "/api/superagent/trend?days=14")
        r = c.getresponse()
        data = json.loads(r.read().decode())
        assert r.status == 200 and data["ok"] is True
        assert len(data["days"]) == 1 and data["days"][0]["total"] == 1
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_trend_section():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "编排趋势" in html and "trendBox" in html and "loadTrend" in html

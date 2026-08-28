# -*- coding: utf-8 -*-
"""Phase 60 · 编排质量基线测试.

覆盖:
- _baseline_rows: 时间窗过滤 / 非法 ts 剔除 / summary 字段映射
- _bl_stats: 均值/标准差/空态
- get_quality_baseline: 全局 + 按 goal 分组 + min_runs 过滤 + 排序
- _bl_deviations: 坏方向判定(score 低=坏, elapsed 高=坏) / std==0 伪 std 兜底 / severity
- check_quality: 正常 / 偏离 / insufficient / 记录不存在 None
- list_quality_alerts: 只在组内探查 / high 优先 / ts 倒序
- API baseline / check (200·400·404) / alerts
- 页面含质量基线 UI
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
    yield


def _write(base_dir, rows):
    """rows: [(ts, goal, score, elapsed, partners_ok)]"""
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for ts, goal, score, elapsed, pok in rows:
            rec = {"ts": ts,
                   "summary": {"goal": goal, "ts": ts, "ok": score >= 60,
                               "selfcheck_score": score, "elapsed_sec": elapsed,
                               "partners_ok": pok}}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


G = "同一目标"
import time as _t
TODAY = _t.strftime("%Y-%m-%d")
def _d(h):
    return "%s %02d:00:00" % (TODAY, h)
T1, T2, T3 = _d(10), _d(11), _d(12)
T_BAD = _d(13)


def _seed(tmp_path, score=90, elapsed=3.0, pok=2):
    _write(tmp_path, [
        (T1, G, score, elapsed, pok),
        (T2, G, score, elapsed, pok),
        (T3, G, score, elapsed, pok),
    ])


def test_baseline_rows_window_and_mapping(tmp_path):
    _seed(tmp_path)
    _write(tmp_path, [("2020-01-01 00:00:00", "老目标", 50, 1, 1)])  # 窗外
    _write(tmp_path, [("垃圾时间戳", "坏ts", 50, 1, 1)])             # 非法 ts
    rows = sa_mod._baseline_rows(str(tmp_path), days=30)
    assert len(rows) == 3
    assert all(r["goal"] == G for r in rows)
    assert rows[0]["score"] == 90.0 and rows[0]["elapsed"] == 3.0
    assert rows[0]["partners_ok"] == 2.0


def test_bl_stats(tmp_path):
    s = sa_mod._bl_stats([80, 90, 100])
    assert s["count"] == 3 and s["mean"] == 90.0
    assert s["std"] == round(((100 + 0 + 100) / 3) ** 0.5, 2)
    assert s["min"] == 80 and s["max"] == 100
    assert sa_mod._bl_stats([]) == {"count": 0, "mean": None, "std": None,
                                    "min": None, "max": None}


def test_get_quality_baseline(tmp_path):
    _seed(tmp_path, score=88, elapsed=4.0)
    _write(tmp_path, [(T1, "另一目标", 70, 2, 1),
                      (T2, "另一目标", 72, 2, 1)])  # 只有 2 次, 低于 min_runs=3
    b = sa_mod.get_quality_baseline(str(tmp_path), days=30)
    assert b["total_runs"] == 5 and b["min_runs"] == 3
    assert b["global"]["score"]["count"] == 5 and b["global"]["score"]["mean"] == round((88*3+70+72)/5, 2)
    # 只有"同一目标"够 3 次
    assert len(b["goals"]) == 1 and b["goals"][0]["goal"] == G
    assert b["goals"][0]["runs"] == 3
    assert b["goals"][0]["score"]["mean"] == 88.0
    assert b["goals"][0]["score"]["std"] == 0.0


def test_deviations_direction_and_pseudo_std(tmp_path):
    _seed(tmp_path, score=90, elapsed=3.0, pok=2)
    me = {"ts": T_BAD, "goal": G, "score": 40.0, "elapsed": 30.0, "partners_ok": 0.0}
    peers = sa_mod._baseline_rows(str(tmp_path), days=30)
    devs = sa_mod._bl_deviations(me, peers, zt=2.0)
    keys = {d["metric"]: d for d in devs}
    # 三个指标全部坏方向偏离, 且 std==0 时伪 std 兜底应能检出
    assert set(keys) == {"score", "elapsed", "partners_ok"}
    assert keys["score"]["z"] < 0 and keys["elapsed"]["z"] > 0 and keys["partners_ok"]["z"] < 0
    assert keys["score"]["severity"] == "high" and keys["elapsed"]["severity"] == "high"
    # partners_ok z 恰为 -2.0(=阈值) -> medium; high 需 |z| >= 1.5*阈值
    assert keys["partners_ok"]["severity"] == "medium"
    # 正常值不应误报
    ok_me = {"ts": T_BAD, "goal": G, "score": 89.0, "elapsed": 3.5, "partners_ok": 2.0}
    assert sa_mod._bl_deviations(ok_me, peers, zt=2.0) == []


def test_check_quality_paths(tmp_path):
    _seed(tmp_path, score=90)
    # 偏离
    _write(tmp_path, [(T_BAD, G, 40, 3.0, 2)])
    rep = sa_mod.check_quality(T_BAD, base_dir=str(tmp_path), days=30)
    assert rep["verdict"] == "偏离" and rep["peers"] == 3
    assert rep["deviations"][0]["metric"] == "score"
    assert rep["baseline"]["score"]["mean"] == 90.0

    # 正常
    _write(tmp_path, [(_d(14), G, 91, 3.0, 2)])
    rep2 = sa_mod.check_quality(_d(14), base_dir=str(tmp_path))
    assert rep2["verdict"] == "正常" and rep2["deviations"] == []

    # insufficient: 独目标只有 1 次
    _write(tmp_path, [(_d(15), "独目标", 60, 1, 1)])
    rep3 = sa_mod.check_quality(_d(15), base_dir=str(tmp_path))
    assert rep3["verdict"] == "insufficient" and rep3["have"] == 0 and rep3["need"] == 3

    # 记录不存在
    assert sa_mod.check_quality("2099-01-01 00:00:00", base_dir=str(tmp_path)) is None


def test_list_quality_alerts(tmp_path):
    _seed(tmp_path, score=90)
    _write(tmp_path, [(T_BAD, G, 40, 3.0, 2)])
    _write(tmp_path, [(_d(14), G, 91, 3.0, 2)])
    alerts = sa_mod.list_quality_alerts(str(tmp_path), days=30)
    assert len(alerts) == 1, "只有 T_BAD 偏离"
    assert alerts[0]["ts"] == T_BAD
    assert alerts[0]["deviations"][0]["metric"] == "score"

    # 多条告警: high 优先, 组内 ts 倒序
    _write(tmp_path, [(_d(15), G, 20, 3.0, 2)])  # 更烂 -> high
    _write(tmp_path, [(_d(16), G, 84, 3.0, 2)])  # 接近伪 std 边界
    alerts2 = sa_mod.list_quality_alerts(str(tmp_path), days=30)
    assert alerts2[0]["ts"] == _d(15), "high 应最前"


def test_quality_api_e2e(tmp_path):
    _seed(tmp_path, score=90)
    _write(tmp_path, [(T_BAD, G, 40, 3.0, 2)])
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9123), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9123, timeout=15)

        c.request("GET", "/api/superagent/quality/baseline?days=30")
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True
        assert j["total_runs"] == 4 and len(j["goals"]) == 1

        c.request("GET", "/api/superagent/quality/check?ts=" + quote(T_BAD))
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True and j["verdict"] == "偏离"

        c.request("GET", "/api/superagent/quality/check?ts=" +
                  quote("2099-01-01 00:00:00"))
        r = c.getresponse()
        assert r.status == 404
        r.read()
        c.request("GET", "/api/superagent/quality/check")
        r = c.getresponse()
        assert r.status == 400
        r.read()

        c.request("GET", "/api/superagent/quality/alerts?days=30")
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True and j["count"] == 1
        assert j["alerts"][0]["ts"] == T_BAD
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_quality_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    for token in ("qualityBox", "loadQuality", "质量基线",
                  "/api/superagent/quality/baseline",
                  "/api/superagent/quality/alerts"):
        assert token in html, "页面缺: " + token

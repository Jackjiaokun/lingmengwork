# -*- coding: utf-8 -*-
"""Phase 73 · 摘要并入质量告警/基线/预算测试."""

import json
import os

from lingmengwork import superagent as sa_mod

TODAY = __import__("time").strftime("%Y-%m-%d")


def _write(base_dir, rows):
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for ts, goal, score, elapsed, pok in rows:
            rec = {"ts": ts,
                   "summary": {"goal": goal, "ts": ts, "ok": score >= 60,
                               "selfcheck_score": score, "elapsed_sec": elapsed,
                               "partners_ok": pok}}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


G = "摘要目标"


def _seed(base_dir):
    _write(base_dir, [(TODAY + " 10:00:00", G, 90, 3.0, 2),
                      (TODAY + " 11:00:00", G, 92, 4.0, 2),
                      (TODAY + " 12:00:00", G, 91, 3.5, 2)])


def test_digest_stats_has_new_keys(tmp_path):
    _seed(tmp_path)
    st = sa_mod._digest_stats("daily", base_dir=str(tmp_path))
    assert "quality_alerts" in st and st["quality_alerts"] == 0, "无偏离应为 0"
    assert st["baseline_score"] == (91.0, 0.82) or (
        isinstance(st["baseline_score"], tuple) and st["baseline_score"][0] == 91.0), \
        "基线自检分 mean 应为 91: %s" % st["baseline_score"]
    assert st["baseline_runs"] == 3
    b = st["budget"]
    assert isinstance(b, dict) and b["daily_limit"] == 0.0 and b["paused"] is False


def test_digest_md_renders_new_lines(tmp_path):
    _seed(tmp_path)
    st = sa_mod._digest_stats("daily", base_dir=str(tmp_path))
    md = sa_mod._digest_md(st)
    assert "**质量告警**: 0 条 ✓" in md
    assert "**质量基线**: 自检分 91.0±" in md and "近90天 3 次编排" in md
    assert "**预算**: 今日 ¥0.0 / 不限" in md
    # 暂停态: 需真实超限(今日成本>=预算), 否则 get_budget_state 会自动解除
    path = sa_mod._persist_path(str(tmp_path))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": TODAY + " 23:00:00",
                            "summary": {"goal": G, "ts": TODAY + " 23:00:00",
                                        "ok": True, "est_cost_cny": 5.0}}) + "\n")
    sa_mod.set_daily_budget(1.0)
    sa_mod._BUDGET["paused"] = True
    st2 = sa_mod._digest_stats("daily", base_dir=str(tmp_path))
    md2 = sa_mod._digest_md(st2)
    assert "⏸ 定时编排已暂停" in md2
    assert "今日 ¥5.0 / ¥1.0" in md2
    sa_mod.set_daily_budget(0)


def test_digest_md_alert_branch(tmp_path):
    """有偏离告警时 md 显示条数并加 ⚠。"""
    _seed(tmp_path)
    _write(tmp_path, [(TODAY + " 20:00:00", G, 40, 3.0, 2)])  # 偏离
    st = sa_mod._digest_stats("daily", base_dir=str(tmp_path))
    # 偏离按 3 指标全扫: 40 分自检分命中; 92 分那条耗时 4.0s 相对同伴也命中 -> 共 2 条
    assert st["quality_alerts"] == 2
    md = sa_mod._digest_md(st)
    assert "**质量告警**: **2 条** ⚠" in md


def test_digest_md_without_optional_data():
    """无任何可选数据(空环境)时不渲染三行、不抛异常。"""
    st = {"period": "daily", "since": "a", "until": "b", "total": 0,
          "ok_count": 0, "fail": 0, "success_rate": None,
          "avg_elapsed": None, "avg_score": None,
          "llm_calls": 0, "tokens": 0, "cost": 0, "recent": [],
          "quality_alerts": None, "baseline_score": None, "budget": None}
    md = sa_mod._digest_md(st)
    assert "质量告警" not in md and "质量基线" not in md and "**预算**" not in md

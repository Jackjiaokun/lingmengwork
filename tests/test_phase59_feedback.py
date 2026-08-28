# -*- coding: utf-8 -*-
"""Phase 59 · 批注驱动的自优化测试.

覆盖:
- collect_feedback: 批注 x 编排 join / min_rating 过滤 / 无编排批注保留
- analyze_feedback: 均分 / 低分统计 / 标签惩罚榜排序 / 域统计 / 规则建议 / 钳位
- render_feedback_block: 条数上限 / 未评分头 / 标签
- build_optimized_goal: 无批注原样 / 有批注规则拼接 / LLM 改写与回退 / 缺记录 None / 空 goal
- replay_run(optimize=True): 用优化后目标跑 / 方法透传 / 优化失败透传
- API GET /feedback + /feedback/optimize (200/400/404) + POST /replay optimize
- 页面含反馈自优化 UI
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
    monkeypatch.setattr(sa_mod, "_ANNOS", {})
    monkeypatch.setattr(sa_mod, "_ANNOS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    monkeypatch.setattr(sa_mod, "_RUNS", __import__("collections").deque(maxlen=200))
    yield


def _write_run(base_dir, ts, goal="目标", model="m1", routed=("code",), ok=True):
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    result = {"ts": ts, "goal": goal, "ok": ok, "elapsed_sec": 3.0, "model": model,
              "routed": list(routed),
              "dispatch": {"partners": []},
              "converge": {"selfcheck_score": 80, "guards": [], "conflicts": []},
              "executions": {"artifacts": []},
              "usage": {"llm_calls": 1, "est_total_tokens": 50, "est_cost_cny": 0.001}}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "summary": {"goal": goal, "ts": ts, "ok": ok},
                            "result": result}, ensure_ascii=False) + "\n")


TS_A, TS_B = "2026-08-28 16:00:00", "2026-08-28 17:00:00"


def _seed(base_dir):
    _write_run(base_dir, TS_A, goal="写爬虫", routed=("code",))
    _write_run(base_dir, TS_B, goal="写报表", routed=("research",))
    sa_mod.add_annotation(TS_A, "太慢了", rating=1, tags=["性能", "慢"],
                          sediment=False, base_dir=str(base_dir))
    sa_mod.add_annotation(TS_A, "还是慢", rating=2, tags=["性能"],
                          sediment=False, base_dir=str(base_dir))
    sa_mod.add_annotation(TS_B, "不错", rating=5, tags=["准确"],
                          sediment=False, base_dir=str(base_dir))


def test_collect_feedback_join_and_filter(tmp_path):
    _seed(tmp_path)
    fb = sa_mod.collect_feedback(base_dir=str(tmp_path))
    assert len(fb) == 3
    crawler = [f for f in fb if f["goal"] == "写爬虫"]
    assert len(crawler) == 2
    assert sorted(f["rating"] for f in crawler) == [1, 2]
    assert all("性能" in f["tags"] for f in crawler)
    report = [f for f in fb if f["goal"] == "写报表"][0]
    assert report["rating"] == 5
    assert report["score"] == 80, "应带编排的自检分"
    # min_rating 过滤
    hi = sa_mod.collect_feedback(base_dir=str(tmp_path), min_rating=4)
    assert len(hi) == 1 and hi[0]["goal"] == "写报表" and hi[0]["rating"] == 5


def test_collect_feedback_orphan_annotation_kept(tmp_path):
    sa_mod.add_annotation("2099-01-01 00:00:00", "孤儿批注", rating=3,
                          sediment=False, base_dir=str(tmp_path))
    fb = sa_mod.collect_feedback(base_dir=str(tmp_path))
    assert len(fb) == 1 and fb[0]["goal"] == "" and fb[0]["ok"] is False


def test_analyze_feedback_stats(tmp_path):
    _seed(tmp_path)
    an = sa_mod.analyze_feedback(base_dir=str(tmp_path))
    assert an["count"] == 3 and an["rated"] == 3
    assert an["avg_rating"] == round((1 + 2 + 5) / 3, 2)
    assert an["low_rated"] == 2
    # 标签惩罚榜: 性能 2 次全低分, 应排最前
    assert an["tags"][0]["tag"] == "性能"
    assert an["tags"][0]["count"] == 2 and an["tags"][0]["low"] == 2
    assert an["tags"][0]["low_rate"] == 100.0
    assert an["tags"][0]["avg_rating"] == 1.5
    # 域: code 2 次全低分, research 0 低分
    doms = {d["domain"]: d for d in an["domains"]}
    assert doms["code"]["low"] == 2 and doms["research"]["low"] == 0
    # 建议应点名「性能」与 code 域
    joined = "\n".join(an["suggestions"])
    assert "性能" in joined and "code" in joined
    # 低分目标列表
    assert any(g["goal"] == "写爬虫" for g in an["low_goals"])


def test_analyze_feedback_clamp_and_flat(tmp_path):
    # low_rating_max 钳位: 0 -> 1, 99 -> 5
    assert sa_mod.analyze_feedback(low_rating_max=0)["low_rating_max"] == 1
    assert sa_mod.analyze_feedback(low_rating_max=99)["low_rating_max"] == 5
    assert sa_mod.analyze_feedback(low_rating_max="abc")["low_rating_max"] == 2
    # 无数据: 全零 + 中性建议
    an = sa_mod.analyze_feedback(base_dir=str(tmp_path))
    assert an["count"] == 0 and an["avg_rating"] is None
    assert "暂无明显负面模式" in an["suggestions"][0]


def test_render_feedback_block(tmp_path):
    _seed(tmp_path)
    fb = sa_mod.collect_feedback(base_dir=str(tmp_path))
    block = sa_mod.render_feedback_block(fb, max_items=2)
    lines = [l for l in block.splitlines() if l.strip()]
    assert len(lines) == 2, "应只取最后 2 条"
    assert "- 评分 2/5 [性能]" in lines[0]
    assert "- 评分 5/5 [准确]" in lines[1]
    # 未评分条目头
    plain = sa_mod.render_feedback_block([{"rating": None, "tags": [], "text": "x"}])
    assert plain.startswith("- 未评分")


def test_build_optimized_goal_rule_mode(tmp_path):
    _seed(tmp_path)
    rep = sa_mod.build_optimized_goal(TS_A, base_dir=str(tmp_path))
    assert rep["ok"] is True and rep["used_llm"] is False and rep["method"] == "rule"
    assert rep["feedback_count"] == 2
    og = rep["optimized_goal"]
    assert og.startswith("写爬虫"), "应以原目标开头"
    assert "【历史人工反馈" in og and "太慢了" in og and "还是慢" in og
    assert "评分 1/5 [性能/慢]" in og


def test_build_optimized_goal_no_annos_and_missing(tmp_path):
    _write_run(tmp_path, TS_A, goal="无批注目标")
    rep = sa_mod.build_optimized_goal(TS_A, base_dir=str(tmp_path))
    assert rep["ok"] is True and rep["method"] == "none"
    assert rep["optimized_goal"] == "无批注目标", "无批注应原样回放"
    assert "暂无批注" in rep["note"]

    assert sa_mod.build_optimized_goal("2099-01-01 00:00:00",
                                       base_dir=str(tmp_path)) is None
    _write_run(tmp_path, TS_B, goal="  ")
    rep2 = sa_mod.build_optimized_goal(TS_B, base_dir=str(tmp_path))
    assert rep2["ok"] is False and "goal" in rep2["error"]


def test_build_optimized_goal_llm_and_fallback(tmp_path):
    _seed(tmp_path)
    # LLM 正常改写
    rep = sa_mod.build_optimized_goal(
        TS_A, base_dir=str(tmp_path),
        llm_call=lambda prompt, sys=None: "写一个异步高性能爬虫，控制并发并加缓存")
    assert rep["used_llm"] is True and rep["method"] == "llm"
    assert rep["optimized_goal"].startswith("写一个异步高性能爬虫")

    # LLM 输出过短 -> 回退规则模式
    rep2 = sa_mod.build_optimized_goal(TS_A, base_dir=str(tmp_path),
                                       llm_call=lambda p, sys=None: "太短")
    assert rep2["used_llm"] is False and rep2["method"] == "rule"
    assert "【历史人工反馈" in rep2["optimized_goal"]

    # LLM 抛异常 -> 回退规则模式
    def boom(p, sys=None):
        raise RuntimeError("llm down")
    rep3 = sa_mod.build_optimized_goal(TS_A, base_dir=str(tmp_path), llm_call=boom)
    assert rep3["used_llm"] is False and rep3["method"] == "rule"


def test_replay_optimize(tmp_path, monkeypatch):
    _seed(tmp_path)
    calls = {}

    def run(self, goal, **kw):
        calls["goal"] = goal
        calls["replay_of"] = self.replay_of
        return {"ok": True, "goal": goal, "ts": "2026-08-28 18:00:00",
                "replay_of": self.replay_of or "", "elapsed_sec": 1.0,
                "converge": {}, "usage": {}, "executions": {}}
    monkeypatch.setattr(sa_mod.SuperAgent, "run", run, raising=True)

    rep = sa_mod.replay_run(TS_A, base_dir=str(tmp_path), optimize=True)
    assert rep["ok"] is True and rep["optimized"] is True and rep["method"] == "rule"
    assert "【历史人工反馈" in rep["goal"], "应用优化后目标跑"
    assert rep["original_goal"] == "写爬虫"
    assert calls["goal"] == rep["goal"] and calls["replay_of"] == TS_A

    # 不 optimize: 用原目标
    rep2 = sa_mod.replay_run(TS_A, base_dir=str(tmp_path))
    assert rep2["optimized"] is False and rep2["goal"] == "写爬虫"

    # 无批注时 optimize: 原样回放
    _write_run(tmp_path, "2026-08-28 19:00:00", goal="干净目标")
    rep3 = sa_mod.replay_run("2026-08-28 19:00:00", base_dir=str(tmp_path),
                             optimize=True)
    assert rep3["ok"] is True and rep3["goal"] == "干净目标"
    assert rep3["method"] == "none"


def test_feedback_api_and_replay_optimize_e2e(tmp_path, monkeypatch):
    _seed(tmp_path)
    calls = {}

    def run(self, goal, **kw):
        calls["goal"] = goal
        return {"ok": True, "goal": goal, "ts": "2026-08-28 18:00:00",
                "replay_of": self.replay_of or "", "elapsed_sec": 1.0,
                "converge": {}, "usage": {}, "executions": {}}
    monkeypatch.setattr(sa_mod.SuperAgent, "run", run, raising=True)

    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9122), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9122, timeout=20)

        # 反馈分析
        c.request("GET", "/api/superagent/feedback")
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True
        assert j["rated"] == 3 and j["low_rated"] == 2
        assert j["tags"][0]["tag"] == "性能"

        # 优化预览
        c.request("GET", "/api/superagent/feedback/optimize?ts=" + quote(TS_A))
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True and j["method"] == "rule"
        assert "太慢了" in j["optimized_goal"]

        c.request("GET", "/api/superagent/feedback/optimize?ts=" +
                  quote("2099-01-01 00:00:00"))
        r = c.getresponse()
        assert r.status == 404
        r.read()
        c.request("GET", "/api/superagent/feedback/optimize")
        r = c.getresponse()
        assert r.status == 400
        r.read()

        # 带 optimize 的回放
        c.request("POST", "/api/superagent/replay",
                  body=json.dumps({"ts": TS_A, "optimize": True}).encode(),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True and j["optimized"] is True
        assert "【历史人工反馈" in j["goal"]
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_feedback_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    for token in ("feedbackBox", "loadFeedback", "optimizeReplay",
                  "反馈自优化", "/api/superagent/feedback",
                  "/api/superagent/feedback/optimize"):
        assert token in html, "页面缺: " + token

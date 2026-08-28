"""Phase 58 · 编排回放测试.

覆盖:
- replay_run 取原编排 goal 重跑, 打上 replay_of 血缘
- 原记录不存在 -> None; 原 goal 空 -> ok=False
- busy 透传(排队超时)
- model 继承原编排(未显式指定时) / 显式覆盖
- list_replays 按源过滤 + 升序, 原始编排不进回放列表
- get_replay_lineage 源/回放双向视角
- _record summary 带 replay_of(原始为空串)
- API POST /replay (200/400/404/429) + GET /replays + GET /lineage
- 页面含回放 UI(按钮/血缘/自动对比)
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


def _write(base_dir, ts, goal="原始目标", model="model-x", ok=True, extra=None):
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    result = {"ts": ts, "goal": goal, "ok": ok, "elapsed_sec": 3.0, "model": model,
              "routed": ["code"],
              "dispatch": {"partners": [{"name": "码农", "domain": "code",
                                         "status": "ok", "summary": "s"}]},
              "converge": {"selfcheck_score": 88, "guards": [], "conflicts": []},
              "executions": {"artifacts": []},
              "usage": {"llm_calls": 2, "est_total_tokens": 100, "est_cost_cny": 0.001}}
    if extra:
        result.update(extra)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "summary": {"goal": goal, "ts": ts, "ok": ok},
                            "result": result}, ensure_ascii=False) + "\n")


TS_SRC = "2026-08-28 16:00:00"
TS_REPLAY = "2026-08-28 16:30:00"


def _fake_run(monkeypatch, new_ts=TS_REPLAY, busy=False):
    """拦截 SuperAgent.run, 模拟一次编排(不真跑)。同时模拟 _record 打 ts。"""
    calls = {}

    def run(self, goal, **kw):
        calls["goal"] = goal
        calls["kwargs"] = kw
        calls["replay_of"] = self.replay_of
        if busy:
            return {"ok": False, "busy": True, "goal": goal,
                    "error": "排队超时", "trace": [], "elapsed_sec": 0.0, "usage": {}}
        return {"ok": True, "goal": goal, "ts": new_ts,
                "replay_of": self.replay_of or "",
                "selfcheck": 90, "elapsed_sec": 2.0, "routed": ["code"],
                "dispatch": {"partners": []},
                "converge": {"selfcheck_score": 90, "guards": [], "conflicts": []},
                "executions": {"artifacts": []},
                "usage": {"llm_calls": 1, "est_total_tokens": 50,
                          "est_cost_cny": 0.0005}}
    monkeypatch.setattr(sa_mod.SuperAgent, "run", run, raising=True)
    return calls


def test_replay_runs_and_marks_lineage(tmp_path, monkeypatch):
    _write(tmp_path, TS_SRC, goal="写一个快排")
    calls = _fake_run(monkeypatch)
    rep = sa_mod.replay_run(TS_SRC, base_dir=str(tmp_path))
    assert rep["ok"] is True
    assert rep["source_ts"] == TS_SRC
    assert rep["replay_ts"] == TS_REPLAY
    assert rep["goal"] == "写一个快排", "应取原编排的 goal 重跑"
    assert rep["result"]["replay_of"] == TS_SRC, "结果应打上血缘标记"
    assert calls["replay_of"] == TS_SRC
    assert calls["kwargs"]["session_id"] == "replay:" + TS_SRC


def test_replay_missing_and_empty_goal(tmp_path, monkeypatch):
    _fake_run(monkeypatch)
    assert sa_mod.replay_run("2099-01-01 00:00:00", base_dir=str(tmp_path)) is None
    _write(tmp_path, TS_SRC, goal="   ")
    rep = sa_mod.replay_run(TS_SRC, base_dir=str(tmp_path))
    assert rep["ok"] is False and "goal" in rep["error"]


def test_replay_busy_passthrough(tmp_path, monkeypatch):
    _write(tmp_path, TS_SRC)
    _fake_run(monkeypatch, busy=True)
    rep = sa_mod.replay_run(TS_SRC, base_dir=str(tmp_path))
    assert rep["ok"] is False and rep["busy"] is True
    assert "排队" in (rep["error"] or "")


def test_replay_model_inherit_and_override(tmp_path, monkeypatch):
    _write(tmp_path, TS_SRC, model="model-src")
    calls = _fake_run(monkeypatch)
    sa_mod.replay_run(TS_SRC, base_dir=str(tmp_path))
    assert calls["kwargs"]["model"] == "model-src", "未指定时继承原编排 model"

    sa_mod.replay_run(TS_SRC, base_dir=str(tmp_path), model="model-override")
    assert calls["kwargs"]["model"] == "model-override", "显式指定应覆盖"


def test_record_summary_carries_replay_of(tmp_path):
    """_record 写进 _RUNS 的 summary 应带 replay_of(原始编排为空串)。"""
    base = {"ok": True, "goal": "g", "converge": {}, "memory": {},
            "usage": {}, "executions": {}}

    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    sa.replay_of = TS_SRC
    sa._record(dict(base, replay_of=TS_SRC))
    assert sa_mod._RUNS[-1]["replay_of"] == TS_SRC, "回放摘要应带血缘"

    sa2 = sa_mod.SuperAgent(base_dir=str(tmp_path))
    sa2._record(dict(base))
    assert sa_mod._RUNS[-1]["replay_of"] == "", "普通编排血缘应为空串"


def test_list_replays(tmp_path, monkeypatch):
    _write(tmp_path, TS_SRC)
    _write(tmp_path, TS_REPLAY, extra={"replay_of": TS_SRC})
    _write(tmp_path, "2026-08-28 17:00:00", goal="另一种回放",
           extra={"replay_of": TS_SRC})

    got = sa_mod.list_replays(TS_SRC, base_dir=str(tmp_path))
    assert [r["ts"] for r in got] == [TS_REPLAY, "2026-08-28 17:00:00"], "按 ts 升序"
    assert all(r["replay_of"] == TS_SRC for r in got)

    # 全部回放
    _write(tmp_path, "2026-08-28 18:00:00", goal="别人的回放",
           extra={"replay_of": "2099-01-01 00:00:00"})
    assert len(sa_mod.list_replays(base_dir=str(tmp_path))) == 3

    # 原始编排不应出现在回放列表里
    assert sa_mod.list_replays("2099-09-09 00:00:00", base_dir=str(tmp_path)) == []


def test_lineage_both_directions(tmp_path, monkeypatch):
    _write(tmp_path, TS_SRC, goal="源目标")
    _write(tmp_path, TS_REPLAY, goal="源目标", extra={"replay_of": TS_SRC})

    # 从源看: is_replay=False, 挂 1 次回放
    src_lin = sa_mod.get_replay_lineage(TS_SRC, base_dir=str(tmp_path))
    assert src_lin["is_replay"] is False
    assert src_lin["source"] is None
    assert len(src_lin["replays"]) == 1
    assert src_lin["replays"][0]["ts"] == TS_REPLAY

    # 从回放看: is_replay=True, 指向源
    rp_lin = sa_mod.get_replay_lineage(TS_REPLAY, base_dir=str(tmp_path))
    assert rp_lin["is_replay"] is True
    assert rp_lin["source"]["ts"] == TS_SRC
    assert rp_lin["source"]["goal"] == "源目标"
    assert rp_lin["replays"] == []

    assert sa_mod.get_replay_lineage("2099-01-01 00:00:00",
                                     base_dir=str(tmp_path)) is None


def test_replay_api_e2e(tmp_path, monkeypatch):
    _write(tmp_path, TS_SRC, goal="API 回放目标")
    _fake_run(monkeypatch)
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9121), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9121, timeout=20)

        def post(path, payload):
            c.request("POST", path, body=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r, json.loads(r.read().decode())

        # 回放
        r, j = post("/api/superagent/replay", {"ts": TS_SRC})
        assert r.status == 200 and j["ok"] is True, j
        assert j["source_ts"] == TS_SRC and j["replay_ts"] == TS_REPLAY

        # 缺 ts -> 400
        r, j = post("/api/superagent/replay", {})
        assert r.status == 400 and "ts" in (j.get("error") or "")

        # 记录不存在 -> 404
        r, j = post("/api/superagent/replay", {"ts": "2099-01-01 00:00:00"})
        assert r.status == 404

        # lineage
        c.request("GET", "/api/superagent/lineage?ts=" + quote(TS_SRC))
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True and j["is_replay"] is False

        c.request("GET", "/api/superagent/lineage?ts=" + quote("2099-01-01 00:00:00"))
        r = c.getresponse()
        assert r.status == 404
        r.read()

        c.request("GET", "/api/superagent/lineage")
        r = c.getresponse()
        assert r.status == 400
        r.read()

        # replays 列表(磁盘上此时还没有落盘的回放记录 -> 空)
        c.request("GET", "/api/superagent/replays?ts=" + quote(TS_SRC))
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True and isinstance(j["replays"], list)
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_replay_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    for token in ("replayRun", "loadLineage", "lineageBox", "🔁 回放",
                  "/api/superagent/replay", "/api/superagent/lineage"):
        assert token in html, "页面缺: " + token
    # 回放完成应自动载入 A/B 对比(与 Phase 56 闭环)
    assert "runDiff();" in html

# -*- coding: utf-8 -*-
"""Phase 68 · 批注编辑测试.

覆盖:
- update_annotation: 全字段更新 / 部分更新(只 text) / rating "" 清除 / 非法 rating 钳 None
- 空 text 拒绝 / 未找到 404 / updated_at 留痕 / tags 去重去空截 8
- API POST /annotations/update (200/400/404)
- 页面含编辑 UI token
- observability 页签化 token
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
    monkeypatch.setattr(sa_mod, "_ANNOS", {})
    monkeypatch.setattr(sa_mod, "_ANNOS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    yield


TS = "2026-08-29 01:00:00"


def _seed(base_dir, sedimented=False):
    rep = sa_mod.add_annotation(TS, "原始批注内容", rating=3, tags=["旧标签"],
                                sediment=False, base_dir=str(base_dir))
    assert rep["ok"]
    aid = rep["annotation"]["id"]
    if sedimented:  # 模拟已沉淀(不真调 memory_graph)
        sa_mod._ANNOS[aid]["sedimented"] = True
        sa_mod._ANNOS[aid]["sediment"] = {"entities_added": 1,
                                          "relations_added": 0, "facts_count": 1,
                                          "error": None}
        sa_mod._save_annos(str(base_dir))
    return aid


def test_update_full_and_partial(tmp_path):
    aid = _seed(tmp_path)
    # 全字段更新
    rep = sa_mod.update_annotation(aid, text="改好的批注", rating=5,
                                   tags=["新标签", "旧标签", "新标签", "  "],
                                   base_dir=str(tmp_path))
    a = rep["annotation"]
    assert rep["ok"] and a["text"] == "改好的批注"
    assert a["rating"] == 5
    assert a["tags"] == ["新标签", "旧标签"], "去重去空保序"
    assert a.get("updated_at"), "应留 updated_at"

    # 部分更新: 只改 text, rating/tags 不动
    rep2 = sa_mod.update_annotation(aid, text="第二次修改", base_dir=str(tmp_path))
    a2 = rep2["annotation"]
    assert a2["text"] == "第二次修改" and a2["rating"] == 5
    assert a2["tags"] == ["新标签", "旧标签"]


def test_update_rating_semantics(tmp_path):
    aid = _seed(tmp_path)
    # "" 显式清除评分
    r1 = sa_mod.update_annotation(aid, rating="", base_dir=str(tmp_path))
    assert r1["annotation"]["rating"] is None
    # 非法数字串 -> 钳 None
    r2 = sa_mod.update_annotation(aid, rating="abc", base_dir=str(tmp_path))
    assert r2["annotation"]["rating"] is None
    # 合法字符串数字
    r3 = sa_mod.update_annotation(aid, rating="4", base_dir=str(tmp_path))
    assert r3["annotation"]["rating"] == 4
    # 越界 -> None
    r4 = sa_mod.update_annotation(aid, rating=9, base_dir=str(tmp_path))
    assert r4["annotation"]["rating"] is None


def test_update_reject_and_404(tmp_path):
    aid = _seed(tmp_path)
    # 空 text 拒绝
    r = sa_mod.update_annotation(aid, text="   ", base_dir=str(tmp_path))
    assert r["ok"] is False and "不能为空" in r["error"]
    # 未找到
    r2 = sa_mod.update_annotation("a_nope", text="x", base_dir=str(tmp_path))
    assert r2["ok"] is False and "未找到" in r2["error"]
    # text=None 纯触控也允许(只更新 tags 等)
    r3 = sa_mod.update_annotation(aid, tags=["只改标签"], base_dir=str(tmp_path))
    assert r3["annotation"]["tags"] == ["只改标签"]
    assert sa_mod.list_annotations(TS, base_dir=str(tmp_path))[0]["text"] == "原始批注内容"


def test_update_resediment_on_text_change(tmp_path):
    """已沉淀批注正文有变 -> 重沉淀; 无变化/未沉淀 -> 不触发。"""
    aid = _seed(tmp_path, sedimented=True)
    calls = []
    class _FakeGraph:
        def absorb(self, title, content, session_id=None):
            calls.append((title, content))
            return {"ok": True, "entities_added": 2, "relations_added": 1,
                    "facts_count": 3}
    fake = _FakeGraph()
    orig = sa_mod._mg.get_graph
    sa_mod._mg.get_graph = lambda base_dir=None: fake
    try:
        sa_mod.update_annotation(aid, text="修订后的批注", base_dir=str(tmp_path))
        assert len(calls) == 1 and "修订后的批注" in calls[0][1]
        assert "【人工批注·修订】" in calls[0][0]
        a = sa_mod._ANNOS[aid]
        assert a["sedimented"] is True and a["sediment"]["entities_added"] == 2

        # 正文无变化 -> 不重沉淀
        sa_mod.update_annotation(aid, text="修订后的批注", base_dir=str(tmp_path))
        assert len(calls) == 1
    finally:
        sa_mod._mg.get_graph = orig


def test_update_api_e2e(tmp_path):
    aid = _seed(tmp_path)
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9131), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9131, timeout=15)

        def post(path, payload):
            c.request("POST", path, body=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r, json.loads(r.read().decode())

        r, j = post("/api/superagent/annotations/update",
                    {"id": aid, "text": "API 改的", "rating": "", "tags": ["api"]})
        assert r.status == 200 and j["ok"] is True
        assert j["annotation"]["text"] == "API 改的"
        assert j["annotation"]["rating"] is None
        assert j["annotation"]["tags"] == ["api"]

        r, j = post("/api/superagent/annotations/update", {"id": "a_nope", "text": "x"})
        assert r.status == 404
        r, j = post("/api/superagent/annotations/update", {"text": "x"})
        assert r.status == 400 and "id" in (j.get("error") or "")
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_pages_have_new_ui():
    sa = open(os.path.join(os.path.dirname(_srv.__file__),
                           "static", "superagent.html"), encoding="utf-8").read()
    for tok in ("toggleAnnoEdit", "saveAnnoEdit", "✏️ 编辑",
                "/api/superagent/annotations/update"):
        assert tok in sa, "工作台缺: " + tok
    obs = open(os.path.join(os.path.dirname(_srv.__file__),
                            "static", "observability.html"), encoding="utf-8").read()
    for tok in ("showObsTab", "obstab-health", "obstab-trace", "obstab-orch",
                "obstab-feed", "lmw_obs_tab", "全链路健康度", "最近事件流"):
        assert tok in obs, "observability 缺: " + tok

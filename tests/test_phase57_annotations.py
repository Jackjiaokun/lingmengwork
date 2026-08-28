"""Phase 57 · 编排结果人工批注测试.

覆盖:
- add_annotation 基本落库(id 递增 / 字段归一 / 空内容 400 语义)
- rating 越界与非数字归 None; tags 去空去重去上限
- 沉淀进记忆图谱(sedimented=True + 实体/关系计数); sediment=False 时不沉淀
- 沉淀失败不阻断批注落库
- list_annotations 按 ts 过滤 + 升序
- remove_annotation 存在/不存在
- get_annotation_stats 条数/均分/标签分布/沉淀数
- 落盘 JSON 持久化与重载
- API create/get/delete e2e
- 页面含批注 UI
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
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(sa_mod, "_ANNOS", {})
    monkeypatch.setattr(sa_mod, "_ANNOS_LOADED", set())
    monkeypatch.setattr(sa_mod, "_INFLIGHT", set())
    yield


def _write_run(base_dir, ts, goal="测试目标"):
    path = sa_mod._persist_path(str(base_dir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "summary": {"goal": goal, "ts": ts, "ok": True},
                            "result": {"ts": ts, "goal": goal, "ok": True}},
                           ensure_ascii=False) + "\n")


TS = "2026-08-28 16:00:00"


def test_add_annotation_basic(tmp_path):
    rep = sa_mod.add_annotation(TS, "这次结果不错", author="张三", rating=4,
                                tags=["可用", "可用", "  ", "提示词待改"],
                                sediment=False, base_dir=str(tmp_path))
    assert rep["ok"] is True
    a = rep["annotation"]
    assert a["id"] == "1" and a["ts"] == TS
    assert a["text"] == "这次结果不错" and a["author"] == "张三"
    assert a["rating"] == 4
    assert a["tags"] == ["可用", "提示词待改"], "应去空去重保序"
    assert a["created_at"]
    assert a["sedimented"] is False, "sediment=False 时不该沉淀"

    rep2 = sa_mod.add_annotation(TS, "第二条", sediment=False, base_dir=str(tmp_path))
    assert rep2["annotation"]["id"] == "2", "id 应递增"


def test_add_annotation_validation(tmp_path):
    r1 = sa_mod.add_annotation(TS, "   ", base_dir=str(tmp_path))
    assert r1["ok"] is False and "空" in r1["error"]
    r2 = sa_mod.add_annotation("", "有内容没 ts", base_dir=str(tmp_path))
    assert r2["ok"] is False and "ts" in r2["error"]


@pytest.mark.parametrize("raw,expect", [
    (0, None), (6, None), (-1, None), ("abc", None), (None, None),
    ("3", 3), (3, 3), (5, 5), (1, 1),
])
def test_rating_normalization(tmp_path, raw, expect):
    a = sa_mod.add_annotation(TS, "评分测试", rating=raw, sediment=False,
                              base_dir=str(tmp_path))["annotation"]
    assert a["rating"] == expect, "rating=%r 应归一为 %r" % (raw, expect)


def test_tags_cap_and_dedup(tmp_path):
    tags = ["t%d" % i for i in range(20)]
    a = sa_mod.add_annotation(TS, "标签测试", tags=tags, sediment=False,
                              base_dir=str(tmp_path))["annotation"]
    assert len(a["tags"]) == 8, "标签上限 8"
    assert a["tags"] == ["t%d" % i for i in range(8)]


def test_sediment_into_memory(tmp_path):
    _write_run(tmp_path, TS, goal="写一个排序函数")
    rep = sa_mod.add_annotation(TS, "改用快速排序会更好，当前冒泡太慢",
                                rating=3, tags=["性能"], base_dir=str(tmp_path))
    a = rep["annotation"]
    assert a["sedimented"] is True, "默认应沉淀进记忆图谱"
    assert isinstance(a["sediment"], dict)
    assert "entities_added" in a["sediment"] and "relations_added" in a["sediment"]
    assert a["sediment"]["error"] in (None, ""), a["sediment"]


def test_sediment_failure_does_not_block(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("记忆库炸了")
    monkeypatch.setattr(sa_mod._mg, "get_graph", boom)
    rep = sa_mod.add_annotation(TS, "沉淀会失败但批注要留住", base_dir=str(tmp_path))
    assert rep["ok"] is True, "沉淀异常不应阻断批注落库"
    a = rep["annotation"]
    assert a["sedimented"] is False
    assert "RuntimeError" in (a["sediment"] or {}).get("error", "")
    # 落库仍然成功
    assert len(sa_mod.list_annotations(TS, base_dir=str(tmp_path))) == 1


def test_list_and_remove(tmp_path):
    sa_mod.add_annotation(TS, "A", sediment=False, base_dir=str(tmp_path))
    sa_mod.add_annotation(TS, "B", sediment=False, base_dir=str(tmp_path))
    sa_mod.add_annotation("2026-08-28 17:00:00", "别的编排", sediment=False,
                          base_dir=str(tmp_path))
    assert len(sa_mod.list_annotations(base_dir=str(tmp_path))) == 3
    got = sa_mod.list_annotations(TS, base_dir=str(tmp_path))
    assert len(got) == 2, "应按 ts 过滤"
    assert [a["text"] for a in got] == ["A", "B"], "应按 created_at 升序"

    assert sa_mod.remove_annotation("1", base_dir=str(tmp_path))["ok"] is True
    assert len(sa_mod.list_annotations(base_dir=str(tmp_path))) == 2
    r = sa_mod.remove_annotation("999", base_dir=str(tmp_path))
    assert r["ok"] is False and "未找到" in r["error"]


def test_stats(tmp_path):
    sa_mod.add_annotation(TS, "a", rating=5, tags=["x"], sediment=False,
                          base_dir=str(tmp_path))
    sa_mod.add_annotation(TS, "b", rating=3, tags=["x", "y"], sediment=False,
                          base_dir=str(tmp_path))
    sa_mod.add_annotation(TS, "c", rating=None, tags=["y"], sediment=False,
                          base_dir=str(tmp_path))
    st = sa_mod.get_annotation_stats(TS, base_dir=str(tmp_path))
    assert st["count"] == 3 and st["rated"] == 2
    assert st["avg_rating"] == 4.0, "(5+3)/2"
    assert st["tags"] == {"x": 2, "y": 2}, "标签分布按次数降序"
    assert st["sedimented"] == 0

    assert sa_mod.get_annotation_stats(base_dir=str(tmp_path))["count"] == 3
    assert sa_mod.get_annotation_stats("2099-01-01 00:00:00",
                                       base_dir=str(tmp_path))["count"] == 0


def test_persistence_reload(tmp_path):
    sa_mod.add_annotation(TS, "要活过重启", rating=5, tags=["持久"],
                          sediment=False, base_dir=str(tmp_path))
    path = sa_mod._annos_path(str(tmp_path))
    assert os.path.isfile(path), "应落盘 outputs/superagent_annos.json"
    # 清空内存模拟重启
    sa_mod._ANNOS.clear()
    sa_mod._ANNOS_LOADED.clear()
    got = sa_mod.list_annotations(TS, base_dir=str(tmp_path))
    assert len(got) == 1 and got[0]["text"] == "要活过重启"
    assert got[0]["rating"] == 5 and got[0]["tags"] == ["持久"]


def test_annotations_api_e2e(tmp_path):
    _write_run(tmp_path, TS, goal="API 测试目标")
    old = os.getcwd()
    os.chdir(str(tmp_path))
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", 9120), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 9120, timeout=15)

        def post(path, payload):
            c.request("POST", path, body=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r, json.loads(r.read().decode())

        # 创建(带沉淀)
        r, j = post("/api/superagent/annotations/create",
                    {"ts": TS, "text": "结果可用", "author": "tester",
                     "rating": 4, "tags": ["ok"]})
        assert r.status == 200 and j["ok"] is True
        assert j["annotation"]["rating"] == 4
        assert j["annotation"]["sedimented"] is True

        # 空内容 -> 400
        r, j = post("/api/superagent/annotations/create", {"ts": TS, "text": ""})
        assert r.status == 400 and j["ok"] is False

        # 列表 + 统计
        c.request("GET", "/api/superagent/annotations?ts=" + quote(TS))
        r = c.getresponse()
        j = json.loads(r.read().decode())
        assert r.status == 200 and j["ok"] is True
        assert len(j["annotations"]) == 1
        assert j["stats"]["count"] == 1 and j["stats"]["avg_rating"] == 4.0

        # 删除
        aid = j["annotations"][0]["id"]
        r, j = post("/api/superagent/annotations/delete", {"id": aid})
        assert r.status == 200 and j["ok"] is True

        # 删不存在的 -> 404
        r, j = post("/api/superagent/annotations/delete", {"id": "999"})
        assert r.status == 404 and j["ok"] is False
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_page_has_annotation_ui():
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    for token in ("annoText", "annoRating", "annoTags", "annoAuthor",
                  "annoSediment", "annoList", "annoStats",
                  "addAnnotation", "loadAnnotations", "delAnnotation",
                  "人工批注"):
        assert token in html, "页面缺: " + token
    assert "/api/superagent/annotations/create" in html
    assert "/api/superagent/annotations/delete" in html

"""Phase 26 — 长期记忆图谱 (memory_graph) 测试。

覆盖：实体增改 / 关系抽取 / absorb 规则抽取多类实体(决策/约定/故障/接口/项目) /
隐私脱敏(密钥明文不入图) / recall 召回 + 1 跳关系遍历 / 置信度衰减(trusted 豁免) /
导出 Markdown / 服务端 API(GET/POST absorb/recall + /memory-graph 页面) / selfcheck 探针计数(12)。
"""
import json
import os
import tempfile
import threading
import time
import http.client

import pytest

from lingmengwork import memory_graph as mg
from lingmengwork.web import server as _srv


def _g(tmp):
    # 用内存库, 避免临时文件锁(沙箱 safe-delete 干扰清理导致 WinError 32)
    return mg.MemoryGraph(":memory:")


# ------------------------------------------------------------------ 实体 / 关系
def test_entity_upsert_and_trusted():
    with tempfile.TemporaryDirectory() as td:
        g = _g(td)
        e = g.add_entity("灵梦work", type="project", confidence=1.0, trusted=False)
        assert e.name == "灵梦work" and e.type == "project"
        # upsert: 提升为 trusted, 置信度更新
        e2 = g.add_entity("灵梦work", type="project", confidence=0.8, trusted=True)
        assert e2.trusted is True and e2.confidence == 0.8
        g.close()


def test_relation_add_and_auto_entity():
    with tempfile.TemporaryDirectory() as td:
        g = _g(td)
        r = g.add_relation("登录模块", "depends_on", "鉴权服务", weight=1.0, confidence=1.0)
        assert r and r.rel == "depends_on"
        # 端点自动建为 fact 实体
        assert g.get_entity("登录模块") is not None
        assert g.get_entity("鉴权服务") is not None
        assert len(g.list_relations()) == 1
        g.close()


# ------------------------------------------------------------------ 抽取
def test_absorb_extracts_types():
    with tempfile.TemporaryDirectory() as td:
        g = _g(td)
        rep = g.absorb(
            "决定采用 SenseNova 作为默认 LLM 后端；约定灵梦work 默认不重打包 exe；"
            "曾因并发死锁导致崩溃(bug)",
            "已落地决策与约定")
        assert rep["ok"]
        assert rep["entities_added"] >= 3
        types = {e["type"] for e in rep["entities"]}
        assert "decision" in types
        assert "convention" in types
        assert "bug" in types
        g.close()


def test_absorb_privacy_no_secret():
    """隐私: api_key 明文值不得入图(仅记『需 key』事实, 值脱敏)。"""
    with tempfile.TemporaryDirectory() as td:
        g = _g(td)
        g.absorb("某接口需要 api_key=sk-9f8e7d6c5b4a 才能调用", "结果")
        blob = json.dumps(g.list_entities(limit=500), ensure_ascii=False)
        assert "sk-9f8e7d6c5b4a" not in blob, "密钥明文不得入图"
        # 但应识别出 api 类实体(接口/需 key 域)
        assert any(e["type"] == "api" for e in g.list_entities())
        g.close()


def test_absorb_relation_extraction():
    with tempfile.TemporaryDirectory() as td:
        g = _g(td)
        rep = g.absorb("登录模块 依赖 鉴权服务。", "说明依赖关系")
        assert rep["relations_added"] >= 1
        rels = g.list_relations()
        assert any(r["rel"] == "depends_on" and r["src"] == "登录模块" and r["dst"] == "鉴权服务"
                   for r in rels)
        g.close()


# ------------------------------------------------------------------ 召回
def test_recall_returns_related():
    with tempfile.TemporaryDirectory() as td:
        g = _g(td)
        g.absorb("决定采用 SenseNova 作为默认 LLM 后端", "已落地")
        rc = g.recall("灵梦work 的默认 LLM 后端是什么", limit=10)
        assert rc["ok"]
        assert rc["count"] >= 1
        assert rc["recap"], "应产出可注入的 recap"
        names = {e["name"] for e in rc["entities"]}
        assert any("LLM" in n or "后端" in n for n in names)
        g.close()


def test_recall_one_hop_relation():
    """recall 命中实体应 1 跳扩展其关系。"""
    with tempfile.TemporaryDirectory() as td:
        g = _g(td)
        g.add_relation("登录模块", "depends_on", "鉴权服务")
        rc = g.recall("登录模块 如何工作", limit=10)
        assert rc["ok"]
        assert any(r["rel"] == "depends_on" and r["dst"] == "鉴权服务" for r in rc["relations"])
        g.close()


# ------------------------------------------------------------------ 衰减
def test_decay_reduces_non_trusted():
    with tempfile.TemporaryDirectory() as td:
        g = _g(td)
        g.add_entity("普通事实", type="fact", confidence=1.0, trusted=False)
        g.add_entity("铁律", type="convention", confidence=1.0, trusted=True)
        g.decay(factor=0.5)
        assert abs(g.get_entity("普通事实").confidence - 0.5) < 1e-6
        assert abs(g.get_entity("铁律").confidence - 1.0) < 1e-6, "trusted 豁免衰减"
        g.close()


# ------------------------------------------------------------------ 导出
def test_export_markdown():
    with tempfile.TemporaryDirectory() as td:
        g = _g(td)
        g.absorb("决定采用 X 方案；约定 Y 规范", "落地")
        md = g.export_markdown()
        assert "记忆图谱报告" in md
        assert "决策" in md and "约定" in md
        g.close()


# ------------------------------------------------------------------ 服务端
def test_server_api():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8982
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", PORT), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    try:
        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        def post(path, body):
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            c.request("POST", path, body=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            return r.status, r.read().decode("utf-8", "replace")

        # 初始: 空图
        st, js = get("/api/memory-graph")
        assert st == 200, (st, js)
        assert json.loads(js)["stats"]["entities"] == 0

        # 沉淀
        st2, js2 = post("/api/memory-graph/absorb",
                        {"goal": "决定采用 SenseNova 作为默认 LLM 后端", "result": "已落地"})
        assert st2 == 200, (st2, js2)
        d2 = json.loads(js2)
        assert d2["ok"] and d2["entities_added"] >= 1

        # 沉淀后 GET 有实体
        st3, js3 = get("/api/memory-graph")
        assert st3 == 200
        assert json.loads(js3)["stats"]["entities"] >= 1

        # 召回
        st4, js4 = post("/api/memory-graph/recall", {"goal": "默认 LLM 后端是什么"})
        assert st4 == 200, (st4, js4)
        d4 = json.loads(js4)
        assert d4["ok"] and d4["count"] >= 1 and d4["recap"]

        # 页面含图谱容器 + 「记忆图谱」字样
        st5, html = get("/memory-graph")
        assert st5 == 200 and "记忆图谱" in html and 'id="entityList"' in html

        # 缺 goal 与 result → 400
        st6, _ = post("/api/memory-graph/absorb", {})
        assert st6 == 400, (st6,)
    finally:
        srv.shutdown()
        try:
            mg.reset_graph(d)  # 关闭缓存连接, 释放文件锁
        except Exception:
            pass
        os.chdir(old)
        try:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


# ------------------------------------------------------------------ 自检集成
def test_selfcheck_probe_count():
    """selfcheck 探针数应为 13 (Phase26 记忆图谱 + Phase27 超级AGENT 探针)。"""
    from lingmengwork import selfcheck as sc
    rep = sc.run()
    assert rep["total"] == 13, "探针数应为 13, 实际 %d" % rep["total"]
    failed = {c["name"]: c["detail"] for c in rep["checks"] if not c["ok"]}
    assert not failed, failed

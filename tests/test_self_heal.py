"""Phase 18/19 — 自主进化闭环 (self_heal) 测试。

覆盖：无信号健康分、selfcheck 失败信号生成、run_fail 事件生成、
静态资产缺失精确诊断、严重度扣分、导出落盘、服务端 API、
Phase19 补丁预案(patch_plan)结构、可读报告导出。
"""
import json
import os
import tempfile
import threading
import time
import http.client

import pytest

from lingmengwork import self_heal as sh
from lingmengwork import event_bus as eb
from lingmengwork.web import server as _srv


def test_no_signal_healthy():
    rep = sh.propose(selfcheck_report={"checks": []}, bus=None)
    assert rep["health_score"] == 100
    assert rep["signal_count"] == 0
    assert rep["proposal_count"] == 0
    assert rep["by_severity"] == {"high": 0, "medium": 0, "low": 0}


def test_selfcheck_fail_generates_proposal():
    bad = {"checks": [
        {"name": "核心模块导入", "ok": False, "detail": "ImportError: no module x"},
        {"name": "关键静态资产", "ok": False, "detail": "缺失: web/static/missing.html"},
    ]}
    rep = sh.propose(selfcheck_report=bad, bus=None)
    assert rep["proposal_count"] == 2
    areas = {p["area"] for p in rep["proposals"]}
    assert "web/static" in areas
    assert "依赖/导入" in areas
    # 静态资产缺失应给出精确恢复动作
    st = next(p for p in rep["proposals"] if p["area"] == "web/static")
    assert any("missing.html" in a for a in st["actions"])


def test_static_missing_parse():
    miss = sh._parse_missing_static("缺失: a.html, b.html")
    assert miss == ["a.html", "b.html"]
    assert sh._parse_missing_static("") == []
    assert sh._parse_missing_static("ok") == []


def test_engine_run_fail_event():
    bus = eb.EventBus()
    bus.emit("engine", "run_fail", "引擎 autonomous 运行失败",
             {"engine": "autonomous"}, audit=True)
    rep = sh.propose(selfcheck_report=None, bus=bus)
    assert rep["proposal_count"] >= 1
    p = rep["proposals"][0]
    assert p["severity"] == "high"
    assert p["area"] == "引擎:autonomous"
    assert p["rule_id"] == "engine_run_fail"


def test_automation_run_fail_event():
    bus = eb.EventBus()
    bus.emit("automation", "run_fail", "运行任务 T1 失败", {"id": "T1"}, audit=True)
    rep = sh.propose(selfcheck_report=None, bus=bus)
    assert rep["proposal_count"] == 1
    p = rep["proposals"][0]
    assert p["severity"] == "medium"
    assert p["area"] == "自动化调度"
    assert p["rule_id"] == "automation_run_fail"


def test_severity_penalty():
    # 两条 high → 100 - 50 = 50
    bus = eb.EventBus()
    bus.emit("engine", "run_fail", "e1", {"engine": "a"}, audit=True)
    bus.emit("engine", "run_fail", "e2", {"x": 1}, audit=True)
    rep = sh.propose(selfcheck_report=None, bus=bus)
    assert rep["health_score"] == 50
    assert rep["by_severity"]["high"] == 2


def test_export_persist(tmp_path):
    rep = sh.propose(selfcheck_report={"checks": [
        {"name": "关键静态资产", "ok": False, "detail": "缺失: x.html"}]}, bus=None)
    res = sh.export_proposals(rep, str(tmp_path))
    assert res["ok"] is True
    assert os.path.exists(res["path"])
    # README 写入
    assert os.path.exists(os.path.join(str(tmp_path), ".lmw_heal", "README.md"))
    with open(res["path"], encoding="utf-8") as f:
        snap = json.loads(f.readline())
    assert snap["health_score"] == rep["health_score"]
    assert snap["proposals"][0]["area"] == "web/static"


def test_server_api():
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8971
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

        st, js = get("/api/heal")
        assert st == 200, (st, js)
        d1 = json.loads(js)
        assert d1["ok"] is True
        assert "health_score" in d1 and "proposals" in d1

        st2, html = get("/heal")
        assert st2 == 200 and "自主进化" in html and 'id="feed"' in html

        st3, js3 = post("/api/heal/export", {})
        assert st3 == 200, (st3, js3)
        d3 = json.loads(js3)
        assert d3["ok"] is True and d3["count"] == d1["proposal_count"]
        assert os.path.exists(os.path.join(d, ".lmw_heal", "proposals.jsonl"))
    finally:
        srv.shutdown()
        os.chdir(old)


def test_patch_plan_present_on_engine_fail():
    """Phase19: 引擎失败提议应携带结构化补丁预案 (title/steps/verify/risk)。"""
    bus = eb.EventBus()
    bus.emit("engine", "run_fail", "引擎 pipeline 运行失败: Timeout",
             {"engine": "pipeline"}, audit=True)
    rep = sh.propose(selfcheck_report=None, bus=bus)
    p = [x for x in rep["proposals"] if x["area"] == "引擎:pipeline"][0]
    plan = p["patch_plan"]
    assert plan, "应含补丁预案"
    assert plan["title"] and "pipeline" in plan["title"]
    assert isinstance(plan["steps"], list) and len(plan["steps"]) >= 2
    assert plan.get("verify") and plan.get("risk")
    # 应指向审计详情定位
    assert any("audit" in s for s in plan["steps"])


def test_patch_plan_present_on_static_missing():
    """Phase19: 静态资产缺失预案应给出精准恢复命令(含缺失文件名)。"""
    bad = {"checks": [
        {"name": "关键静态资产", "ok": False, "detail": "缺失: web/static/gone.html"}]}
    rep = sh.propose(selfcheck_report=bad, bus=None)
    p = rep["proposals"][0]
    assert "gone.html" in " ".join(p["patch_plan"]["steps"])


def test_export_markdown(tmp_path):
    """Phase19: 导出同时生成可读 .md 报告(含补丁预案)。"""
    rep = sh.propose(selfcheck_report={"checks": [
        {"name": "关键静态资产", "ok": False, "detail": "缺失: x.html"}]}, bus=None)
    res = sh.export_proposals(rep, str(tmp_path))
    assert res["ok"] is True
    assert os.path.exists(res["md_path"])
    with open(res["md_path"], encoding="utf-8") as f:
        md = f.read()
    assert "补丁预案" in md
    assert "x.html" in md
    assert "验证" in md and "风险" in md


def test_server_export_md():
    """Phase19: /api/heal/export-md 端点落盘可读报告。"""
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    PORT = 8972
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

        # 先注入一条失败事件, 让报告非空
        eb.get_bus().emit("engine", "run_fail", "引擎 creation 运行失败",
                          {"engine": "creation"}, audit=True)
        st, js = post("/api/heal/export-md", {})
        assert st == 200, (st, js)
        d4 = json.loads(js)
        assert d4["ok"] is True and "md_path" in d4
        assert os.path.exists(d4["md_path"])
        with open(d4["md_path"], encoding="utf-8") as f:
            md = f.read()
        assert "creation" in md and "补丁预案" in md
        # 页面也含导出按钮
        st2, html = get("/heal")
        assert "导出可读报告" in html and "/api/heal/export-md" in html
    finally:
        srv.shutdown()
        os.chdir(old)

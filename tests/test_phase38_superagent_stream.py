"""Phase 38 · 超级 AGENT SSE 流式编排测试.

覆盖:
- run(on_stage=...) 回调: 每阶段实时触发, 顺序与 trace 一致
- 回调异常隔离(不阻塞编排)
- POST /api/superagent/run/stream SSE: >=7 条 stage 事件 + done 完整结果 + 缺 goal 报错
- 页面含流式渲染 JS(appendStage / run/stream)
"""

import http.client
import json
import os
import tempfile
import threading
import time

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork.web import server as _srv


STAGES = sa_mod._STAGE_NAMES


def test_run_on_stage_callback(tmp_path, monkeypatch):
    """on_stage 每阶段触发一次, 顺序/内容与最终 trace 完全一致。"""
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [], "note": ""})
                         for d in ("code", "creation", "research", "ops")})
    seen = []
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("研究分析竞品趋势并部署监控", session_id="p38",
                 quality_gate=False, on_stage=seen.append)
    assert rep["ok"] is True
    assert [e["stage"] for e in seen] == STAGES
    assert seen == rep["trace"], "回调事件应与 trace 逐条一致"


def test_run_on_stage_exception_swallowed(tmp_path, monkeypatch):
    """回调抛异常: 编排不受影响(异常隔离)。"""
    def bad(_entry):
        raise ValueError("callback boom")

    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [], "note": ""})
                         for d in ("code", "creation", "research", "ops")})
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    rep = sa.run("研究分析竞品趋势并部署监控", session_id="p38b",
                 quality_gate=False, on_stage=bad)
    assert rep["ok"] is True
    assert [t["stage"] for t in rep["trace"]] == STAGES


def _start_server(port):
    d = tempfile.mkdtemp()
    old = os.getcwd()
    os.chdir(d)
    srv = _srv.ThreadingHTTPServer(("127.0.0.1", port), _srv.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    return srv, old


def test_stream_endpoint_sse(monkeypatch):
    """SSE 端点: 逐阶段 stage 事件 + done 完整结果; 缺 goal 返回 error 事件。"""
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: (lambda p, goal="", llm_call=None, base_dir=None:
                             {"domain": p.get("domain"), "status": "ok", "artifacts": [], "note": ""})
                         for d in ("code", "creation", "research", "ops")})
    srv, old = _start_server(8989)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8989, timeout=60)
        c.request("POST", "/api/superagent/run/stream",
                  body=json.dumps({"goal": "研究分析竞品趋势并部署监控"}).encode(),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        assert r.status == 200
        assert (r.getheader("Content-Type") or "").startswith("text/event-stream")
        body = r.read().decode("utf-8", "replace")
        events = []
        for frame in body.split("\n\n"):
            for line in frame.split("\n"):
                if line.startswith("data: "):
                    try:
                        events.append(json.loads(line[6:]))
                    except Exception:
                        pass
        stages = [e["stage"] for e in events if e.get("type") == "stage"]
        assert stages == STAGES, "SSE 应逐阶段推送 7 事件, 实际 %s" % stages
        assert all(e.get("ok") for e in events if e.get("type") == "stage")
        dones = [e for e in events if e.get("type") == "done"]
        assert len(dones) == 1
        rep = dones[0]["result"]
        assert rep["ok"] is True
        assert [t["stage"] for t in rep["trace"]] == STAGES
        assert len(rep["routed"]) >= 2

        # 缺 goal -> error 事件
        c2 = http.client.HTTPConnection("127.0.0.1", 8989, timeout=15)
        c2.request("POST", "/api/superagent/run/stream", body=b"{}",
                   headers={"Content-Type": "application/json"})
        r2 = c2.getresponse()
        body2 = r2.read().decode("utf-8", "replace")
        assert '"type": "error"' in body2 or '"type":"error"' in body2
        assert "缺少 goal" in body2
    finally:
        os.chdir(old)
        srv.shutdown()
        srv.server_close()


def test_superagent_page_has_stream_ui():
    """页面应含流式渲染 JS: run/stream 端点 + appendStage 实时上屏 + 同步回退。"""
    path = os.path.join(os.path.dirname(_srv.__file__), "static", "superagent.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "/api/superagent/run/stream" in html
    assert "appendStage" in html
    assert "text/event-stream" not in html  # 前端不解析 header, 只读 data 帧
    assert "/api/superagent/run" in html, "应保留同步端点回退"

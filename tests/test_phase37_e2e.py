"""Phase 37 · 真 LLM 端到端编排验证 (mock LLM + 真实连接器 + 全 7 阶段闭环).

覆盖:
- superagent.run() 全 7 阶段 trace 顺序完整:
  目标理解 → 插件接入 → 域路由 → 并行编排 → 执行落地 → 收敛护栏 → 记忆沉淀
- mock LLM 驱动 understand(结构化 JSON) / research / ops / 记忆抽取 全链路
- 真实连接器 http_probe: 标签匹配 → 联邦派发 → 真 HTTP 探测本地端点(200)
- 执行落地产出真实交付文件 + 记忆沉淀落图 + _RUNS 观测缓冲记录
- 无 LLM 规则兜底全链路 / 内核异常隔离与记录 / 连接器 dict 结果透传
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lingmengwork import superagent as sa_mod
from lingmengwork import federation as _fed
from lingmengwork import plugin_hub as _ph
from lingmengwork.plugins.sample_http_probe import register_connectors


# ------------------------------------------------------------------ mock LLM
def make_llm(domains=("research", "ops")):
    """按 system 语境分发的 mock LLM: 理解/研究/运维/记忆抽取 全覆盖。"""
    calls = []

    def llm(prompt, system=None):
        calls.append({"prompt": (prompt or "")[:48], "system": (system or "")[:32]})
        s = system or ""
        if "任务理解器" in s:
            return json.dumps({"intent": "E2E 诊断网络端点可用性",
                               "domains": list(domains),
                               "constraints": ["8s 超时"]}, ensure_ascii=False)
        if "研究分析师" in s:
            return "## 研究目标\n%s\n\n## 关键发现\n- 探测 200 OK" % (prompt or "")[:60]
        if "运维" in s:
            return "## 执行计划\n1. 探测端点\n2. 汇报结果"
        if "知识图谱抽取器" in s:
            return json.dumps({"entities": [{"name": "http_probe", "type": "project"}],
                               "relations": []}, ensure_ascii=False)
        return "OK"

    llm.calls = calls
    return llm


# ------------------------------------------------------------------ 快速执行器(隔离真实研究抓取/编码冒烟, 只验证 execute→artifact 链)
def _fast_exec(domain):
    def fn(partner, goal="", llm_call=None, base_dir=None):
        out_root = base_dir if (base_dir and base_dir != ":memory:"
                                and os.path.isdir(base_dir)) else os.path.join(os.getcwd(), ".e2e_tmp")
        out = os.path.join(out_root, "outputs", "superagent")
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "e2e37_%s.md" % domain)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# E2E·%s\n\n目标: %s\n" % (domain, goal))
        return {"domain": domain, "status": "artifact", "artifacts": [path],
                "note": "fast executor"}
    return fn


@pytest.fixture
def fast_executors(monkeypatch):
    monkeypatch.setattr(sa_mod, "EXECUTORS",
                        {d: _fast_exec(d) for d in ("code", "creation", "research", "ops")})


@pytest.fixture
def quick_selfcheck(monkeypatch):
    monkeypatch.setattr(sa_mod._sc, "run",
                        lambda: {"ok": True, "score": 96, "passed": 13, "total": 13,
                                 "all_ok": True, "checks": [], "ts": "test"})


@pytest.fixture
def probe_hub():
    hub = _ph.get_hub()
    register_connectors(hub)
    return hub


@pytest.fixture
def local_http_server():
    """本地真实 HTTP 端点(供 http_probe 探测, 200 + JSON 体)。"""
    class _H(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self._r()

        def do_GET(self):
            self._r()

        def _r(self):
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command == "GET":
                self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d/ping" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


# ------------------------------------------------------------------ 核心 E2E
def test_e2e_full_pipeline_with_llm_and_connector(tmp_path, monkeypatch, fast_executors,
                                                  quick_selfcheck, probe_hub,
                                                  local_http_server):
    """全 7 阶段闭环: mock LLM + 真实连接器探测本地端点 + 产物落盘 + 记忆沉淀 + 观测记录。"""
    goal = "诊断网络端点 %s" % local_http_server
    llm = make_llm()
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    res = sa.run(goal, session_id="e2e37", llm_call=llm, quality_gate=True)

    # 1) 整体成功 + trace 7 阶段顺序完整
    assert res["ok"] is True, "编排应成功: %s" % json.dumps(res.get("trace", []), ensure_ascii=False)
    stages = [t["stage"] for t in res["trace"]]
    assert stages == sa_mod._STAGE_NAMES, "trace 应为 7 阶段顺序, 实际 %s" % stages
    assert all(t["ok"] for t in res["trace"]), "每阶段子步骤应全部 ok"

    # 2) LLM 理解生效: intent 抽取 + 路由多域
    assert res["intent"]["intent"] == "E2E 诊断网络端点可用性"
    assert res["intent"]["constraints"] == ["8s 超时"]
    assert len(res["routed"]) >= 2, "专家域合并后应 >=2 域"
    assert res["converge"]["partners_ok"] >= 2
    assert res["converge"]["selfcheck_score"] == 96

    # 3) 真实连接器: 标签匹配命中 → 真 HTTP 探测 200 → dict 结果透传
    mcs = res["dispatch"].get("matched_connectors", [])
    probe = [m for m in mcs if m["name"] == "http_probe"]
    assert probe, "http_probe 应被标签匹配并调用: %s" % mcs
    p = probe[0]
    assert p["ok"] is True
    assert isinstance(p["result"], dict), "dict 结果应整包透传(Phase 37 修复)"
    assert p["result"].get("status_code") == 200
    assert isinstance(p["result"].get("elapsed_ms"), int)

    # 4) 执行落地: 真实交付文件存在
    arts = res["executions"].get("artifacts", [])
    assert len(arts) >= 2
    for a in arts:
        assert os.path.isfile(a), "产物应真实落盘: %s" % a

    # 5) 记忆沉淀: LLM 抽取实体入图
    mem = res["memory"]
    assert mem.get("ok") is True
    assert mem.get("entities_added", 0) >= 1

    # 6) mock LLM 确实被多处消费(理解/伙伴/记忆)
    assert len(llm.calls) >= 3

    # 7) 观测缓冲记录本次编排
    last = sa_mod.get_recent_runs(1)
    assert last and last[0]["goal"] == goal and last[0]["ok"] is True


def test_e2e_no_llm_rule_fallback(tmp_path, fast_executors):
    """无 LLM: 规则兜底跑通全链路(关键词路由 research+ops, 产物落盘)。"""
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    res = sa.run("研究分析竞品趋势并部署监控", session_id="e2e37b", quality_gate=False)
    assert res["ok"] is True
    stages = [t["stage"] for t in res["trace"]]
    assert stages == sa_mod._STAGE_NAMES
    assert set(res["routed"]) >= {"research", "ops"}, "关键词应命中研究+运维, 实际 %s" % res["routed"]
    assert res["converge"]["partners_ok"] >= 2
    for a in res["executions"].get("artifacts", []):
        assert os.path.isfile(a)
    assert res["memory"].get("ok") is True
    # 无 LLM: 连接器未被匹配(目标不含连接器标签) → 不阻塞主流程
    assert res["dispatch"].get("matched_connectors", []) == []


def test_e2e_kernel_exception_isolated(tmp_path, monkeypatch, fast_executors):
    """内核单阶段异常: 整体 ok=False + trace 记录「内核异常」+ _RUNS 留痕, 不崩。"""
    def _boom(self, understand, session_id="", llm_call=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(sa_mod.SuperAgent, "dispatch", _boom)
    sa = sa_mod.SuperAgent(base_dir=str(tmp_path))
    res = sa.run("随便什么目标", session_id="e2e37c", quality_gate=False)
    assert res["ok"] is False
    assert "boom" in res.get("error", "")
    assert res["trace"][-1]["stage"] == "内核异常"
    assert res["trace"][-1]["ok"] is False
    assert isinstance(res["elapsed_sec"], (int, float))
    last = sa_mod.get_recent_runs(1)
    assert last and last[0]["goal"] == "随便什么目标" and last[0]["ok"] is False


def test_federation_connector_dict_result_propagated():
    """回归: call_fn 返回 dict(无 result 键)时, dispatch.matched_connectors 应透传完整字段。"""
    hub = _ph.get_hub()
    hub.register_connector(
        name="probe37x", category="test",
        description="Phase37 回归连接器",
        call_fn=lambda goal, **kw: {"ok": True, "status_code": 200, "url": "http://x", "elapsed_ms": 3},
        tags=["probe37x"])
    try:
        rep = _fed.get_federation().dispatch("probe37x 探测", connector_names=["probe37x"])
        mc = rep.get("matched_connectors", [])
        assert mc and mc[0]["name"] == "probe37x" and mc[0]["ok"] is True
        assert isinstance(mc[0]["result"], dict)
        assert mc[0]["result"]["status_code"] == 200
        assert mc[0]["result"]["elapsed_ms"] == 3
    finally:
        hub.connectors.pop("probe37x", None)

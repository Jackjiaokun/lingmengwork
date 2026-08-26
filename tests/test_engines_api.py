"""Phase 10 统一引擎总控台 API 测试: /api/engines 聚合快照 + /api/engines/run 统一启动器。

无 LLM key 环境下走规则兜底, 验证路由/聚合/轨迹记录的正确性。
"""
import io
import json

import pytest

from lingmengwork.web import server


class _StubHandler(server.Handler):
    def __init__(self, body):
        raw = json.dumps(body).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self._captured = {}

    def _send_json(self, obj, status=200):
        self._captured = {"obj": obj, "status": status}


@pytest.fixture
def stub():
    return _StubHandler


def test_engines_status_shape(stub):
    h = stub({})
    h._engines_status()
    out = h._captured["obj"]
    assert out["ok"] is True
    assert len(out["engines"]) == 4
    assert set(e["id"] for e in out["engines"]) == {"orchestrate", "creation", "autonomous", "pipeline"}
    for key in ("orchestration", "creation", "memory", "control_center", "llm_backend"):
        assert key in out, key
    assert "domains" in out["creation"]


def test_engines_run_decompose(stub):
    h = stub({"engine": "decompose", "goal": "为登录模块增加记住我功能"})
    h._engines_run()
    out = h._captured["obj"]
    assert out["ok"] is True
    assert out["engine"] == "decompose"
    assert out["result"]["goal"]
    assert len(out["result"]["steps"]) >= 1
    # 写入 ring buffer
    assert server._ENGINE_RUNS and server._ENGINE_RUNS[-1]["engine"] == "decompose"


def test_engines_run_creation_rule_fallback(stub):
    h = stub({"engine": "creation", "domain": "code", "brief": "写一个快速排序函数"})
    h._engines_run()
    out = h._captured["obj"]
    assert out["ok"] is True
    assert out["result"]["domain"] == "code"
    assert out["result"]["plan"]


def test_engines_run_pipeline_no_learn(stub):
    h = stub({"engine": "pipeline", "goal": "做一个天气播报小工具", "do_learn": False})
    h._engines_run()
    out = h._captured["obj"]
    assert out["ok"] is True
    assert out["result"]["stages"]
    assert "decompose" in out["result"] and out["result"]["decompose"]["count"] >= 1


def test_engines_run_missing_engine(stub):
    h = stub({})
    h._engines_run()
    assert h._captured["status"] == 400


def test_engines_run_unknown_engine(stub):
    h = stub({"engine": "blah", "goal": "x"})
    h._engines_run()
    assert h._captured["status"] == 400


def test_engines_run_creation_requires_domain(stub):
    h = stub({"engine": "creation", "brief": "x"})
    h._engines_run()
    assert h._captured["status"] == 400

"""后端扇出合同测试: 用 stub Handler 直接调 _create_task, 验证 prompts 扇出行为。

不启真实 HTTP 服务, 但复用 server 模块真实逻辑 (_get_pool / _ORCH)。
配置切到 mock 通道, 任务真正快速跑完, 验证编排聚合。
"""
import copy
import io
import json

import pytest

from lingmengwork.web import server
from lingmengwork.config import DEFAULTS


@pytest.fixture
def mock_cfg():
    saved = server._RUNTIME_CONFIG
    saved_pool = server._TASK_POOL
    cfg = copy.deepcopy(DEFAULTS)
    cfg["llm"]["backend"] = "mock"
    server._RUNTIME_CONFIG = cfg
    server._TASK_POOL = None  # 强制用新 cfg 重建
    yield cfg
    server._RUNTIME_CONFIG = saved
    if saved_pool is not None:
        server._TASK_POOL = saved_pool


class _StubHandler(server.Handler):
    def __init__(self, body):
        raw = json.dumps(body).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self._captured = {}

    def _send_json(self, obj, status=200):
        self._captured = {"obj": obj, "status": status}


def test_create_task_fanout(mock_cfg):
    h = _StubHandler({"prompts": ["读 config.toml", "运行 echo hi", "写个 hello.py"]})
    h._create_task()
    out = h._captured["obj"]
    assert "orchestration_id" in out, out
    assert len(out["tasks"]) == 3, out
    assert h._captured["status"] == 201
    ids = [t["id"] for t in out["tasks"]]
    assert len(set(ids)) == 3, "扇出的任务 id 必须互异"
    oid = out["orchestration_id"]
    agg = server._ORCH.aggregate(oid, server._TASK_POOL)
    assert agg is not None
    assert agg["total"] == 3


def test_create_task_single_still_works(mock_cfg):
    h = _StubHandler({"prompt": "读 config.toml"})
    h._create_task()
    out = h._captured["obj"]
    # 单 prompt 路径保持返回单 snapshot (含 id), 不返回编排结构
    assert "id" in out and "orchestration_id" not in out, out
    assert h._captured["status"] == 201


def test_create_task_empty_prompts_falls_back_to_single(mock_cfg):
    # prompts 全是空白 -> 回退到单 prompt 校验 (空则报错)
    h = _StubHandler({"prompts": ["", "  ", "\n"]})
    h._create_task()
    assert h._captured["status"] == 400, h._captured

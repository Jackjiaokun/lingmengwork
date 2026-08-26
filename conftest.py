import os
import sys

import pytest

# 让 pytest 无论从哪个 cwd 运行都能 import lingmengwork 包
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# --- Phase 13: 测试时耗治理 ---
# 根因: pytest 运行环境继承了真实商汤/SenseNova key, 导致测试内启动的真实 server
# (ThreadingHTTPServer + Handler / 子类 _StubHandler) 的 _make_llm_call 真发 API 请求
# -> 全量回归 9m+ 卡顿甚至挂死。
# 治理: 全局清真实 LLM key 环境变量 + 强制 server.Handler._make_llm_call 返回 None,
# 让所有测试默认走规则兜底/None, 既验证完整链路又绝不触发真实 API 调用。
# (测试套件无任何依赖真实 LLM 的用例, 全部走 MockClient / 规则兜底 / 入参 llm_call=fake)

_LLM_KEY_VARS = (
    "SENSENOVA_API_KEY", "SENSENOVA_API_KEY_2", "SENSENOVA_API_KEY_3",
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY",
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
)


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """测试全程禁用真实 LLM: 清 key 环境变量, 并 stub web server 的真实调用入口。"""
    for k in _LLM_KEY_VARS:
        monkeypatch.delenv(k, raising=False)
    try:
        from lingmengwork.web import server as _srv
        # 进程内真实 server (ThreadingHTTPServer + Handler 及其子类) 走此入口 -> 强制返回 None
        monkeypatch.setattr(
            _srv.Handler, "_make_llm_call",
            lambda self, *a, **kw: None, raising=False)
    except Exception:
        pass
    yield

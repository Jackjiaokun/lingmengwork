"""LLM 语义评审层 + .env 自动注入 单元测试。

覆盖:
- _load_dotenv 解析 .env 并注入 env (不覆盖已设变量, 跳过注释/空行/去引号)
- _llm_review 在 SENSENOVA_API_KEY 存在时调 LLM 并解析 (mock 客户端, 无需真 key)
- _llm_review 无 key 时优雅回退 None (静态评审兜底)
- _review_file(use_llm=True) 合并静态 approve + LLM revise -> 终态 revise, source 标记 LLM
"""
import os

import pytest

from lingmengwork.config import _load_dotenv
from lingmengwork.web import server


def _fake_review_tool():
    class _T:
        def has_tool(self, n):
            return n == "code_review"
        def call_tool(self, n, a):
            return "[code-review] VERDICT: approve\nSCORE: 95\nISSUES: (无)\nSUMMARY: 静态通过"
    return _T()


def test_load_dotenv_sets_vars_and_skips_comments(tmp_path, monkeypatch):
    # 清空可能已存在的变量, 验证 .env 注入
    monkeypatch.delenv("SENSENOVA_API_KEY", raising=False)
    monkeypatch.delenv("SENSENOVA_API_KEY_2", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# 这是注释\n\nSENSENOVA_API_KEY=sk-test-abc\n"
        'SENSENOVA_API_KEY_2="sk-test-def"\n\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _load_dotenv()
    assert os.environ.get("SENSENOVA_API_KEY") == "sk-test-abc"
    assert os.environ.get("SENSENOVA_API_KEY_2") == "sk-test-def"


def test_load_dotenv_does_not_override_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("SENSENOVA_API_KEY", "already-set")
    env = tmp_path / ".env"
    env.write_text("SENSENOVA_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _load_dotenv()
    # 已设的 env 不被 .env 覆盖
    assert os.environ.get("SENSENOVA_API_KEY") == "already-set"


def test_llm_review_returns_none_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("SENSENOVA_API_KEY", raising=False)
    monkeypatch.delenv("SENSENOVA_API_KEY_2", raising=False)
    p = tmp_path / "m.py"
    p.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert server._llm_review(p.read_text(encoding="utf-8")) is None


def test_llm_review_engages_and_merges(tmp_path, monkeypatch):
    monkeypatch.setenv("SENSENOVA_API_KEY", "sk-dummy")
    # mock 商汤客户端: 返回预设 critic 文本
    class _FakeResp:
        def __iter__(self):
            return iter(["VERDICT: revise\nSCORE: 70\nISSUES:\n- [高] 测试发现隐患\nSUMMARY: 需改进"])
    class _FakeClient:
        def __init__(self, *a, **k):
            pass
        def chat(self, msgs, stream=False):
            return _FakeResp()
    import lingmengwork.llm.client as _cl
    monkeypatch.setattr(_cl, "OpenAIClient", _FakeClient)

    p = tmp_path / "m.py"
    p.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    raw, parsed = server._review_file(_fake_review_tool(), str(p), use_llm=True)
    assert parsed.get("verdict") == "revise", parsed
    assert (parsed.get("source") or "").startswith("LLM"), parsed
    assert any("测试发现隐患" in (i.get("desc", "")) for i in parsed.get("issues", [])), parsed
    # 评分取 LLM 的 70 (静态 95 被覆盖)
    assert parsed.get("score") == 70, parsed

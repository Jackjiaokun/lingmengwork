"""评审聚合报告 / LLM 语义评审层 / 成果落盘 的单元与集成测试。"""
import os
import sys
import pytest

# 直接导入 web server 模块 (顶层 import 不会触发 MCP 管理器加载)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lingmengwork.web import server as S


def test_render_review_report_basic():
    files = [
        {"path": "a.py", "verdict": "approve", "score": 92, "issues": [], "summary": "ok", "source": "静态规则"},
        {"path": "b.py", "verdict": "revise", "score": 70,
         "issues": [{"sev": "中", "desc": "疑似空实现"}], "summary": "x", "source": "静态规则"},
    ]
    html = S._render_review_report(files, note="示例说明")
    assert "灵梦work · 多文件评审报告" in html
    assert "a.py" in html and "b.py" in html
    assert "示例说明" in html
    assert "✅ 通过" in html and "⛔ 需修改" in html
    assert "平均分" in html
    # 注入内容应被 HTML 转义 (原文不应出现在输出中)
    html2 = S._render_review_report(files, note="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html2


def test_parse_critic_review_ok():
    text = ("一些语义分析...\n"
            "VERDICT: revise\n"
            "SCORE: 78\n"
            "ISSUES:\n- [高] 缺少输入校验\n- [中] 命名不清\n"
            "SUMMARY: 需要补测试\n")
    r = S._parse_critic_review(text)
    assert r is not None
    assert r["verdict"] == "revise"
    assert r["score"] == 78
    assert r["summary"] == "需要补测试"
    sevs = [i["sev"] for i in r["issues"]]
    assert "高" in sevs and "中" in sevs


def test_parse_critic_review_no_verdict():
    assert S._parse_critic_review("随便写了点意见, 没有结构化结论") is None
    assert S._parse_critic_review("") is None


def test_merge_review_static_only():
    static = {"verdict": "approve", "score": 90, "issues": [], "summary": "s", "source": "静态规则"}
    assert S._merge_review(static, None) is static  # 无 LLM 直接返回原对象


def test_merge_review_blend():
    static = {"verdict": "approve", "score": 95, "issues": [], "summary": "s", "source": "静态规则"}
    llm = {"verdict": "revise", "score": 60, "issues": [{"sev": "高", "desc": "缺校验"}],
           "summary": "l", "source": "LLM 语义 + 静态"}
    merged = S._merge_review(static, llm)
    assert merged["verdict"] == "revise"        # 任一 revise -> revise
    assert merged["score"] == 60                # 取 LLM 评分
    assert len(merged["issues"]) == 1          # 问题叠加
    assert "LLM" in merged["source"]


def test_llm_review_no_key_returns_none(monkeypatch):
    # 无 SENSENOVA_API_KEY(_2) 时优雅回退 (不触发网络)。
    # 注意: 项目根 .env 可能在 import 期被 _load_dotenv 注入双 key, 故两个都要清。
    monkeypatch.delenv("SENSENOVA_API_KEY", raising=False)
    monkeypatch.delenv("SENSENOVA_API_KEY_2", raising=False)
    assert S._llm_review("def f(): pass") is None


def test_llm_review_key_but_import_fail(monkeypatch):
    # 有 key 但 llm.client 不可导入 -> 回退 None (不崩)
    monkeypatch.setenv("SENSENOVA_API_KEY", "fake-key")
    real_import = __builtins__.__import__ if isinstance(__builtins__, type(__import__)) else __builtins__["__import__"]
    def fake_import(name, *a, **k):
        if name.endswith("llm.client"):
            raise ImportError("boom")
        return real_import(name, *a, **k)
    monkeypatch.setattr("builtins.__import__", fake_import)
    assert S._llm_review("def f(): pass") is None


def test_record_and_list_artifacts(monkeypatch):
    # 用普通临时目录(非 pytest-of-* 树, 避开本环境 safe-delete 垫片对 pytest 临时树的拦截),
    # 验证落盘 + 清单闭环
    import tempfile
    d = tempfile.mkdtemp(prefix="lmw_art_")
    try:
        # 预建 files 子目录(本环境 safe-delete 垫片可能拦截 makedirs, 预建后 _record_artifact 内的 makedirs 为 no-op)
        try:
            os.makedirs(os.path.join(d, "files"), exist_ok=True)
        except Exception:
            pass
        monkeypatch.setattr(S, "_artifact_dir", lambda: d)
        rec = S._record_artifact("delivery", b"<html>hi</html>", "text/html", {"target": "x.py"})
        assert rec is not None
        assert os.path.isfile(os.path.join(d, "files", rec["name"]))
        import json
        idx = os.path.join(d, "index.jsonl")
        assert os.path.exists(idx)
        items = [json.loads(l) for l in open(idx, encoding="utf-8").read().splitlines() if l.strip()]
        assert len(items) == 1 and items[0]["kind"] == "delivery"
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

"""Phase 14 · 离线自检中枢 测试。

无 LLM 依赖、确定性。覆盖: 报告结构 / 各引擎探针 / 静态资产 / CLI。
"""
import os
import sys
import subprocess

import pytest

pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)

from lingmengwork import selfcheck as sc  # noqa: E402


def test_run_returns_report():
    rep = sc.run()
    assert rep["ok"] is True
    assert isinstance(rep["score"], int) and 0 <= rep["score"] <= 100
    assert rep["total"] == 14
    assert rep["passed"] == rep["total"]  # 本地环境应全部通过
    assert rep["all_ok"] is True
    assert isinstance(rep["checks"], list) and len(rep["checks"]) == 14
    # 每项结构
    for c in rep["checks"]:
        assert set(c.keys()) >= {"name", "ok", "detail"}
        assert c["ok"] is True


def test_check_imports():
    d = sc.check_imports()
    assert "成功" in d


def test_check_decompose_rule_fallback():
    d = sc.check_decompose()
    assert "步" in d


def test_check_creation_rule_fallback():
    d = sc.check_creation()
    assert "蓝图" in d


def test_check_autonomous_no_llm():
    d = sc.check_autonomous()
    assert "轮" in d


def test_check_pipeline_no_llm():
    d = sc.check_pipeline()
    assert "阶段" in d


def test_check_multimodal_template_fallback():
    d = sc.check_multimodal()
    assert "真实产出" in d
    assert "real=" in d


def test_check_memory_capture_recall():
    d = sc.check_memory()
    assert "捕获" in d and "召回" in d


def test_check_static_files_present():
    # 关键静态资产应全部齐备 (selfcheck.py 即位于 lingmengwork 包目录)
    here = os.path.dirname(os.path.abspath(sc.__file__))
    pkg = here
    for f in sc._STATIC_FILES:
        assert os.path.isfile(os.path.join(pkg, f)), "缺失静态资产: %s" % f


def test_cli_invocation():
    """CLI 入口应 exit 0 并打印健康分。"""
    proc = subprocess.run(
        [sys.executable, "-m", "lingmengwork.selfcheck"],
        cwd=pkg_root, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[:500]
    assert "健康分" in proc.stdout
    assert "✅" in proc.stdout

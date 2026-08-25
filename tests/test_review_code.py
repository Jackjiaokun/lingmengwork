"""波次D+: 代码评审自评估 (Critic Loop) 测试。"""
import os
import tempfile

from lingmengwork.config import DEFAULTS
from lingmengwork.tools.registry import build_registry
from lingmengwork.tools import review


def _write_tmp(ext, content):
    fd, path = tempfile.mkstemp(suffix=ext, text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def test_static_review_clean_approve():
    code = "def add(a, b):\n    return a + b\n\n\nclass Calc:\n    def sum(self, xs):\n        return sum(xs)\n"
    r = review._static_review(code, "good.py")
    assert r["verdict"] == "approve"
    assert r["score"] >= 95
    assert r["issues"] == []


def test_static_review_syntax_error_revise():
    bad = "def broken(:\n    pass\n"
    r = review._static_review(bad, "bad.py")
    assert r["verdict"] == "revise"
    assert any(sev == "高" for sev, _ in r["issues"])


def test_static_review_rules_flagged():
    code = "try:\n    do()\nexcept:\n    pass\n# TODO fix later\nfrom x import *\n"
    r = review._static_review(code, "rules.py")
    sevs = [sev for sev, _ in r["issues"]]
    assert "高" in sevs  # 裸 except
    assert "中" in sevs  # TODO / import *


def test_review_code_via_registry_execute():
    path = _write_tmp(".py", "def f():\n    print('hi')\n")
    try:
        reg = build_registry(DEFAULTS, base_dir=".", clients={})  # 强制纯静态评审
        out = reg.execute("review_code", {"target": path})
        assert "[code-review]" in out
        assert "VERDICT:" in out
        assert "SCORE:" in out
        # 含 print 调试残留(低) -> 仍 approve, 但应列出 ISSUES
        assert "ISSUES:" in out
    finally:
        os.unlink(path)


def test_review_code_snippet_target():
    # target 直接传代码片段 (非路径), 静态评审仍工作
    snippet = "x = 1\nexcept:\n    pass\n"
    reg = build_registry(DEFAULTS, base_dir=".", clients={})
    out = reg.execute("review_code", {"target": snippet})
    assert "VERDICT: revise" in out
    assert any(sev == "高" for sev, _ in review._static_review(snippet, "")["issues"])


def test_indented_bare_except_detected():
    # 🔴 回归: 缩进后的裸 except 此前被 re.match (start-anchored) 漏检
    code = "def handler():\n    try:\n        risky()\n    except:\n        pass\n"
    r = review._static_review(code, "h.py")
    assert any(sev == "高" and "裸 except" in desc for sev, desc in r["issues"])


def test_indented_import_star_and_empty_impl_detected():
    code = "class Svc:\n    def run(self):\n        from os import *\n        return 1\n    def noop(self):\n        pass\n"
    r = review._static_review(code, "svc.py")
    descs = [desc for sev, desc in r["issues"]]
    assert any("import *" in d for d in descs)
    assert any("疑似空实现" in d for d in descs)

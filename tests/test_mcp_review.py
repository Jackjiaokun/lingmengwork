"""mcp_review_server (code_review 静态评审) 离线测试 —— 不依赖网络/LLM, 确定性。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingmengwork.tools import mcp_review_server as m


def test_syntax_ok_approve():
    code = "def add(a, b):\n    return a + b\n"
    st = m._static_review(code, "demo.py")
    assert st["verdict"] == "approve"
    assert st["score"] == 100
    assert not any(sev == "高" for sev, _ in st["issues"])


def test_syntax_error_revise():
    code = "def bad(:\n    pass\n"
    st = m._static_review(code, "bad.py")
    assert st["verdict"] == "revise"
    assert any(sev == "高" for sev, _ in st["issues"])
    assert st["score"] < 100


def test_bare_except_high():
    code = "try:\n    x()\nexcept:\n    pass\n"
    st = m._static_review(code, "e.py")
    assert any(sev == "高" and "裸 except" in desc for sev, desc in st["issues"])


def test_missing_target_revise():
    out = m._code_review({})
    assert out.startswith("[code-review]")
    assert "VERDICT: revise" in out
    assert "SCORE: 0" in out


def test_code_review_format():
    code = "def mul(a, b):\n    return a * b\n"
    out = m._code_review({"target": code})
    assert "[code-review]" in out
    assert "VERDICT: approve" in out
    assert "SCORE:" in out
    assert "ISSUES:" in out
    assert "SUMMARY:" in out
    # 格式与前端 _parse_code_review 兼容 (server.py)
    assert "VERDICT: approve" in out


def test_import_star_medium():
    code = "from os import *\n"
    st = m._static_review(code, "s.py")
    assert any(sev == "中" and "import *" in desc for sev, desc in st["issues"])


def test_indented_bare_except_detected():
    # 🔴 回归: 缩进后的裸 except 此前被 re.match (start-anchored) 漏检
    code = "def f():\n    try:\n        g()\n    except:\n        pass\n"
    st = m._static_review(code, "e2.py")
    assert any(sev == "高" and "裸 except" in desc for sev, desc in st["issues"])


def test_indented_import_star_detected():
    code = "def f():\n    from os import *\n    return 1\n"
    st = m._static_review(code, "s2.py")
    assert any(sev == "中" and "import *" in desc for sev, desc in st["issues"])


def test_indented_empty_impl_detected():
    code = "class C:\n    def method(self):\n        pass\n"
    st = m._static_review(code, "c.py")
    assert any(sev == "中" and "疑似空实现" in desc for sev, desc in st["issues"])

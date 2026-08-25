"""WEB 代码评审可视化: 后端 _parse_code_review 解析测试 (纯函数, 不依赖 LLM/网络)。"""

from lingmengwork.web.server import _parse_code_review


def test_parse_approve_clean():
    txt = ("[code-review]\nVERDICT: approve\nSCORE: 100\nISSUES:\n- (无)\n"
           "SUMMARY: 静态评审完成: 通过语法检查, 无规则告警\n(评审来源: 静态评审)")
    d = _parse_code_review(txt)
    assert d is not None
    assert d["verdict"] == "approve"
    assert d["score"] == 100
    assert d["issues"] == []
    assert "静态评审" in d["summary"]
    assert "静态评审" in d["source"]


def test_parse_revise_with_severity():
    txt = ("[code-review]\nVERDICT: revise\nSCORE: 60\nISSUES:\n"
           "- [高] 裸 except 吞异常 (line 59)\n- [中] import * 污染命名空间 (line 3)\n- [低] 行超 120 字符 (line 10)\n"
           "SUMMARY: 命中 3 项规则\n(评审来源: 静态评审)")
    d = _parse_code_review(txt)
    assert d["verdict"] == "revise"
    assert d["score"] == 60
    sevs = [i["sev"] for i in d["issues"]]
    assert sevs == ["高", "中", "低"]
    assert d["issues"][0]["desc"].startswith("裸 except")


def test_parse_none_for_plain_text():
    assert _parse_code_review("just some text") is None
    assert _parse_code_review("") is None
    assert _parse_code_review(None) is None


def test_parse_critic_source():
    txt = ("[code-review]\nVERDICT: approve\nSCORE: 92\nISSUES:\n- (无)\n"
           "SUMMARY: 整体良好\n(评审来源: LLM 评审 + 静态)")
    d = _parse_code_review(txt)
    assert d["verdict"] == "approve"
    assert d["source"] == "LLM 评审 + 静态"

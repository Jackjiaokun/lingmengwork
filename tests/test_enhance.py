"""专家/技能 提示词增强 模块测试。"""
import json
import os
import tempfile
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lingmengwork.agent import enhance as E


def _write(tmp, data):
    p = os.path.join(tmp, "prompts_enhance.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return p


def test_load_missing_seed_false_returns_empty():
    with tempfile.TemporaryDirectory() as t:
        d = E.load(t, seed=False)
        assert d == {"experts": [], "skills": []}


def test_load_missing_seeds_default_library():
    with tempfile.TemporaryDirectory() as t:
        d = E.load(t)  # seed=True 默认
        # 文件被自动播种
        assert os.path.isfile(os.path.join(t, "prompts_enhance.json"))
        assert len(d["experts"]) == len(E.DEFAULT_LIBRARY["experts"])
        assert len(d["skills"]) == len(E.DEFAULT_LIBRARY["skills"])
        # 播种内容等同预设
        assert d == E._default_library()


def test_default_library_is_multidomain():
    lib = E._default_library()
    names = [e["name"] for e in lib["experts"]]
    # 覆盖多行业: 软件/金融/法律/医疗/教育/电商/产品/翻译/科研/运维
    for kw in ["架构师", "量化", "法律", "医疗", "教育", "营销", "产品", "翻译", "科研", "DevOps"]:
        assert any(kw in n for n in names), "缺少行业专家: " + kw
    # 预设默认不自动激活, 避免系统提示臃肿
    assert all(not e.get("enabled") for e in lib["experts"])
    assert all(not s.get("auto") for s in lib["skills"])
    # 名称唯一
    assert len({e["name"] for e in lib["experts"]}) == len(lib["experts"])
    assert len({s["name"] for s in lib["skills"]}) == len(lib["skills"])


def test_reset_to_defaults_overwrites():
    with tempfile.TemporaryDirectory() as t:
        # 先写入自定义内容
        E.save(t, {"experts": [{"name": "自定义专家", "prompt": "x", "enabled": True}], "skills": []})
        # 再恢复预设
        E.reset_to_defaults(t)
        d = E.load(t, seed=False)
        assert "自定义专家" not in [e["name"] for e in d["experts"]]
        assert len(d["experts"]) == len(E.DEFAULT_LIBRARY["experts"])
        assert len(d["skills"]) == len(E.DEFAULT_LIBRARY["skills"])


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as t:
        data = {
            "experts": [{"name": "Python架构师", "prompt": "用类型注解", "enabled": True}],
            "skills": [{"name": "安全自检", "prompt": "检查注入", "auto": False}],
        }
        E.save(t, data)
        got = E.load(t)
        assert got["experts"][0]["name"] == "Python架构师"
        assert got["skills"][0]["name"] == "安全自检"


def test_default_active():
    data = {
        "experts": [
            {"name": "A", "prompt": "x", "enabled": True},
            {"name": "B", "prompt": "y", "enabled": False},
        ],
        "skills": [
            {"name": "S1", "prompt": "z", "auto": True},
            {"name": "S2", "prompt": "w", "auto": False},
        ],
    }
    exp, skl = E.default_active(data)
    assert exp == ["A"]
    assert skl == ["S1"]


def test_build_block_empty_when_nothing_matches():
    data = {"experts": [{"name": "A", "prompt": "x"}], "skills": []}
    assert E.build_enhancement_block(["不存在"], [], data) == ""


def test_build_block_injects_prompts():
    data = {
        "experts": [{"name": "Python架构师", "prompt": "请使用类型注解与 dataclass。"}],
        "skills": [{"name": "安全自检", "prompt": "输出前检查 SQL 注入。"}],
    }
    blk = E.build_enhancement_block(["Python架构师"], ["安全自检"], data)
    assert "提示词增强" in blk
    assert "Python架构师" in blk
    assert "请使用类型注解" in blk
    assert "安全自检" in blk
    assert "检查 SQL 注入" in blk


def test_build_block_partial_match_by_keyword():
    data = {"experts": [{"name": "Python后端架构师", "prompt": "x"}], "skills": []}
    blk = E.build_enhancement_block(["后端"], [], data)
    assert "Python后端架构师" in blk


def test_build_block_skips_empty_prompt():
    data = {"experts": [{"name": "空", "prompt": "   "}], "skills": []}
    assert E.build_enhancement_block(["空"], [], data) == ""


def test_load_corrupt_returns_empty():
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "prompts_enhance.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{不是合法json")
        assert E.load(t) == {"experts": [], "skills": []}

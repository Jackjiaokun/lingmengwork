"""Phase 11 流水线×自主深度融合测试: 编程域步骤驱动自主回路 + 抽代码落盘 + 编译评审门。

无 LLM 时 autonomous 退化为空轨迹, 调用方回退 dispatch 蓝图, 不破坏现有行为。
"""
import os

from lingmengwork import goal_pipeline as gp
from lingmengwork import autonomous as au


def _fake_llm(prompt, system=None):
    s = system or ""
    if "最关键的下一步" in s:   # 仅匹配 PLAN_SYS (OBS_SYS 文案也含"行动计划", 故用唯一标记)
        return "## 本轮目标\n实现 hello\n\n```python\ndef hi():\n    return 'hello'\n```"
    if "执行观察员" in s:       # 仅匹配 OBS_SYS
        return "## 观察\n已实现 hi 函数"
    if "Critic" in s:
        return '{"done": true, "score": 90, "note": "ok"}'
    return ""


def test_extract_code_blocks():
    text = "前言\n```python\nprint(1)\n```\n中间\n```js\nconsole.log(2)\n```"
    blocks = gp._extract_code_blocks(text)
    assert len(blocks) == 2
    assert blocks[0][0] == "python" and "print(1)" in blocks[0][1]
    assert blocks[1][0] == "js"


def test_autonomous_execute_writes_and_compiles(tmp_path):
    out = gp._autonomous_execute("写一个 hello 函数", "上下文", _fake_llm, max_iter=3, out_root=str(tmp_path))
    assert out["reached"] is True
    assert out["iterations"] == 1
    assert len(out["files"]) == 1
    rv = out["review"]
    assert rv["checked"] == 1 and rv["passed"] == 1
    fpath = os.path.join(str(tmp_path), "code", gp._slugify("写一个 hello 函数"), out["files"][0])
    assert os.path.isfile(fpath)
    # 编译校验真通过
    import py_compile
    py_compile.compile(fpath, doraise=True)


def test_autonomous_execute_bad_code_fails_compile(tmp_path):
    def bad_llm(prompt, system=None):
        s = system or ""
        if "最关键的下一步" in s:
            return "```python\ndef broken(:\n    pass\n```"
        if "Critic" in s:
            return '{"done": false, "score": 40, "note": "syntax"}'
        return ""
    out = gp._autonomous_execute("坏代码", "ctx", bad_llm, max_iter=1, out_root=str(tmp_path))
    rv = out["review"]
    assert rv["checked"] == 1 and rv["failed"] == 1 and rv["passed"] == 0


def test_autonomous_execute_no_llm_empty():
    out = gp._autonomous_execute("目标", "ctx", None, max_iter=2, out_root="/tmp/nope")
    assert out["files"] == []
    # 自主回路恒记录 max_iter 条轨迹(即便无 LLM, 内容为占位空串)
    assert out["iterations"] == 2


def test_pipeline_code_domain_has_autonomous_no_llm():
    res = gp.run_pipeline("为登录页增加记住我功能", llm_call=None, do_selfcheck=False,
                          do_render=False, do_learn=False, do_autonomous=True, max_autonomous_iter=2)
    assert res["ok"] is True
    code_items = [ex for ex in res["execute"] if ex["domain"] == "code"]
    assert code_items, "应至少出现一个编程域执行项"
    for ex in code_items:
        assert isinstance(ex.get("autonomous"), dict), "编程域执行项应含 autonomous 字段"
        # 无 LLM -> 无代码文件 -> 回退 dispatch 蓝图, plan 非空
        assert ex.get("plan")


def test_pipeline_do_autonomous_false():
    res = gp.run_pipeline("写一个快速排序", llm_call=None, do_selfcheck=False,
                          do_render=False, do_learn=False, do_autonomous=False)
    assert res["ok"] is True
    for ex in res["execute"]:
        assert ex.get("autonomous") is None, "do_autonomous=False 时不应有 autonomous 字段"

"""工具结果结构化抽取单测 (批次13 主题A)。"""
from lingmengwork.tools.registry import _extract_struct


def test_object():
    r = _extract_struct('{"name":"a","age":3,"tags":["x","y"]}')
    assert r["is_json"] is True
    assert r["kind"] == "object"
    assert r["n"] == 3
    assert set(r["keys"]) >= {"name", "age", "tags"}
    assert "sample" in r


def test_array():
    r = _extract_struct('[{"id":1},{"id":2,"k":"v"}]')
    assert r["is_json"] is True
    assert r["kind"] == "array"
    assert r["n"] == 2
    assert set(r["keys"]) >= {"id", "k"}


def test_scalar():
    r = _extract_struct('42')
    assert r["is_json"] is True and r["kind"] == "scalar"


def test_embedded_json_in_text():
    txt = "查询结果如下:\n```json\n{\"ok\": true, \"count\": 5}\n```\n完成"
    r = _extract_struct(txt)
    assert r["is_json"] is True and r["kind"] == "object"
    assert r["n"] == 2


def test_nested_braces_balanced():
    # 字符串内含花括号, 不应误截断
    txt = '{"msg":"a{b}c", "n":1}'
    r = _extract_struct(txt)
    assert r["is_json"] is True and r["n"] == 2


def test_non_json():
    assert _extract_struct("just text no json")["is_json"] is False
    assert _extract_struct("")["is_json"] is False
    assert _extract_struct(None)["is_json"] is False


def test_extract_records_structured_in_recent():
    # 端到端: 通过 registry 执行一个返回 JSON 的工具, 检查 recent 带 structured
    import time
    from lingmengwork.tools.registry import build_registry, reset_stats, get_stats, _IMPLS, _READONLY_TOOLS
    from lingmengwork import config as cfgmod
    cfg = cfgmod.load_config()
    reg = build_registry(cfg, permission_mode="bypassPermissions")
    # 注入一个返回 JSON 的探针工具
    def _probe(args, ctx):
        return '{"status":"ok","items":[1,2,3]}'
    _IMPLS["__struct_probe__"] = _probe
    _READONLY_TOOLS.add("__struct_probe__")
    reset_stats()
    reg.execute("__struct_probe__", {})
    s = get_stats()
    ev = s["recent"][-1]
    assert ev["structured"]["is_json"] is True
    assert ev["structured"]["kind"] == "object"
    assert ev["structured"]["n"] == 2
    # 清理
    _IMPLS.pop("__struct_probe__", None)
    _READONLY_TOOLS.discard("__struct_probe__")
    reset_stats()

"""Phase 110 工具隔离测试 (零网络, 标准库). 覆盖 7 个契约生成/校验/编码工具的正常与降级路径."""
import json

from lingmengwork.tools import suite_phase110 as s

SCHEMA = '{"type":"object","properties":{"a":{"type":"string"},"n":{"type":"integer"}},"required":["a"]}'


# --- openapi_gen -----------------------------------------------------------
def test_openapi_gen_post_body():
    out = json.loads(s.openapi_gen({"schema": SCHEMA, "path": "/pets", "method": "post"}, {}))
    assert out["openapi"] == "3.0.3"
    op = out["paths"]["/pets"]["post"]
    assert op["requestBody"]["content"]["application/json"]["schema"]["type"] == "object"
    assert "parameters" not in op


def test_openapi_gen_get_query_params():
    out = json.loads(s.openapi_gen({"schema": SCHEMA, "path": "/pets", "method": "get"}, {}))
    op = out["paths"]["/pets"]["get"]
    names = [p["name"] for p in op["parameters"]]
    assert "a" in names and "n" in names
    # required 字段应标记 required=True
    req = {p["name"]: p["required"] for p in op["parameters"]}
    assert req["a"] is True and req["n"] is False


def test_openapi_gen_bad_method():
    assert s.openapi_gen({"schema": SCHEMA, "method": "trace"}, {}).startswith("[openapi_gen]")


# --- json_minify -----------------------------------------------------------
def test_json_minify_strips_comments_and_trailing_comma():
    raw = '{\n  // 注释\n  "a": 1, /* 块注释 */ "b": [1, 2,],\n}\n'
    assert s.json_minify({"json": raw}, {}) == '{"a":1,"b":[1,2]}'


def test_json_minify_keeps_string_content():
    raw = '{"u": "http://x.com/*not*/a#b", "n": 2}'
    out = json.loads(s.json_minify({"json": raw}, {}))
    assert out["u"] == "http://x.com/*not*/a#b"


def test_json_minify_bad():
    assert s.json_minify({"json": "{oops"}, {}).startswith("[json_minify]")


# --- regex_test ------------------------------------------------------------
def test_regex_test_matches_and_groups():
    out = json.loads(s.regex_test({"pattern": r"(\w+)@(\w+)", "text": "me@ex and you@ex"}, {}))
    assert out["count"] == 2
    assert out["matches"][0]["groups"] == ["me", "ex"]


def test_regex_test_replace_and_flags():
    out = json.loads(s.regex_test({"pattern": "abc", "text": "ABC abc", "flags": "i",
                                   "replace": "X"}, {}))
    assert out["count"] == 2
    assert out["replaced"] == "X X"


def test_regex_test_bad_pattern():
    assert s.regex_test({"pattern": "([", "text": "x"}, {}).startswith("[regex_test]")


# --- semver_compare --------------------------------------------------------
def test_semver_equal_and_less():
    assert json.loads(s.semver_compare({"a": "1.2.3", "b": "1.2.3"}, {}))["relation"] == "a==b"
    assert json.loads(s.semver_compare({"a": "1.2.3", "b": "1.10.0"}, {}))["relation"] == "a<b"


def test_semver_prerelease_lower():
    out = json.loads(s.semver_compare({"a": "1.0.0-alpha", "b": "1.0.0"}, {}))
    assert out["relation"] == "a<b" and out["a_prerelease"] == "alpha"


def test_semver_invalid():
    assert s.semver_compare({"a": "1.2", "b": "1.0.0"}, {}).startswith("[semver_compare]")


# --- sql_validate ----------------------------------------------------------
def test_sql_validate_clean():
    out = json.loads(s.sql_validate({"sql": "SELECT * FROM t WHERE id = 1;"}, {}))
    assert out["ok"] is True and out["issues"] == []


def test_sql_validate_missing_where():
    out = json.loads(s.sql_validate({"sql": "DELETE FROM t"}, {}))
    assert any(i["type"] == "missing_where" for i in out["issues"])


def test_sql_validate_unclosed_paren_and_string():
    out = json.loads(s.sql_validate({"sql": "SELECT count( FROM t"}, {}))
    assert any(i["type"] == "unclosed_paren" for i in out["issues"])
    out2 = json.loads(s.sql_validate({"sql": "SELECT 'abc FROM t"}, {}))
    assert any(i["type"] == "unterminated_string" for i in out2["issues"])


# --- cron_validate ---------------------------------------------------------
def test_cron_validate_ok():
    out = json.loads(s.cron_validate({"cron": "*/5 9 * * 1-5"}, {}))
    assert out["ok"] is True and out["fields"]["minute"] == "*/5"


def test_cron_validate_out_of_range():
    out = json.loads(s.cron_validate({"cron": "70 9 * * *"}, {}))
    assert out["ok"] is False
    assert any(i["field"] == "minute" for i in out["issues"])


def test_cron_validate_bad_field_count():
    out = json.loads(s.cron_validate({"cron": "* * *"}, {}))
    assert out["ok"] is False and out["issues"][0]["type"] == "field_count"


# --- base64_codec ----------------------------------------------------------
def test_base64_roundtrip():
    enc = s.base64_codec({"text": "灵梦work", "mode": "encode"}, {})
    dec = s.base64_codec({"text": enc, "mode": "decode"}, {})
    assert dec == "灵梦work"


def test_base64_urlsafe_differs_for_sensitive_bytes():
    std = s.base64_codec({"text": "a?b>c~d", "mode": "encode"}, {})
    url = s.base64_codec({"text": "a?b>c~d", "mode": "encode", "urlsafe": "1"}, {})
    assert "+" not in url and "/" not in url
    assert std != url or ("+" not in std and "/" not in std)

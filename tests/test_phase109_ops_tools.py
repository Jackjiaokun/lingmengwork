"""Phase 109 工具隔离测试 (零网络, 标准库). 覆盖 7 个解析/文档/检查工具的正常与降级路径."""
import base64
import json

from lingmengwork.tools import suite_phase109 as s


def _tok(payload):
    h = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).decode().rstrip("=")
    p = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return "%s.%s.sig" % (h, p)


# --- jwt_decode ------------------------------------------------------------
def test_jwt_decode_roundtrip():
    out = json.loads(s.jwt_decode({"token": _tok({"sub": "123"})}, {}))
    assert out["header"]["alg"] == "HS256"
    assert out["payload"]["sub"] == "123"
    assert out["signature_present"] is True


def test_jwt_decode_expired_flag():
    out = json.loads(s.jwt_decode({"token": _tok({"exp": 1000000000})}, {}))
    assert out["expired"] is True
    assert out["expires_in_sec"] < 0


def test_jwt_decode_bad_format():
    out = s.jwt_decode({"token": "not.a.jwt.or.this"}, {})
    assert out.startswith("[jwt_decode]")


# --- url_parse -------------------------------------------------------------
def test_url_parse_full():
    out = json.loads(s.url_parse({"url": "https://u:p@ex.com:8443/a/b?x=1&y=2#f"}, {}))
    assert out["scheme"] == "https" and out["host"] == "ex.com" and out["port"] == 8443
    assert out["path"] == "/a/b" and out["fragment"] == "f"
    assert out["query"] == {"x": "1", "y": "2"}


def test_url_parse_password_masked():
    out = json.loads(s.url_parse({"url": "https://u:secret@ex.com"}, {}))
    assert out["password"] == "***" and "secret" not in json.dumps(out)


# --- markdown_toc ----------------------------------------------------------
def test_markdown_toc_levels():
    out = s.markdown_toc({"markdown": "# A\n## B\n### C\n"}, {})
    assert "- [A](#a)" in out and "  - [B](#b)" in out and "    - [C](#c)" in out


def test_markdown_toc_skips_fence():
    md = "# A\n\n```python\n# not a heading\n```\n\n## B\n"
    out = s.markdown_toc({"markdown": md}, {})
    assert "not a heading" not in out and "[B](#b)" in out


def test_markdown_toc_max_level():
    out = s.markdown_toc({"markdown": "# A\n## B\n### C", "max_level": 2}, {})
    assert "[C]" not in out


# --- text_stats ------------------------------------------------------------
def test_text_stats_counts():
    out = json.loads(s.text_stats({"text": "hello 世界 hello"}, {}))
    assert out["chars"] == 14 and out["chars_cjk"] == 2
    assert out["words_en"] == 2
    assert out["top_words"][0] == {"word": "hello", "count": 2}


def test_text_stats_empty():
    assert s.text_stats({"text": ""}, {}).startswith("[text_stats]")


# --- csv_to_markdown -------------------------------------------------------
def test_csv_to_markdown_table():
    out = s.csv_to_markdown({"csv": "a,b\n1,2"}, {})
    lines = out.splitlines()
    assert lines[0] == "| a | b |"
    assert lines[1] == "| :--- | :--- |"
    assert lines[2] == "| 1 | 2 |"


def test_csv_to_markdown_center():
    out = s.csv_to_markdown({"csv": "a,b\n1,2", "align": "center"}, {})
    assert ":---:" in out


# --- env_lint --------------------------------------------------------------
def test_env_lint_detects_issues():
    out = json.loads(s.env_lint({"env": "A=1\nA=2\nB=bad value\nC=1 # note\nD=\n"}, {}))
    types = {i["type"] for i in out["issues"]}
    assert "duplicate_key" in types
    assert "unquoted_space" in types
    assert "inline_comment" in types
    assert "empty_value" in types
    assert out["ok"] is False


def test_env_lint_clean():
    out = json.loads(s.env_lint({"env": 'A=1\nB="two words"\n'}, {}))
    assert out["ok"] is True and out["keys"] == 2 and out["issues"] == []


# --- requirements_diff -----------------------------------------------------
def test_requirements_diff_all_kinds():
    out = json.loads(s.requirements_diff(
        {"a": "requests==2.0\nflask==1.0\n", "b": "requests==2.1\nfastapi\n"}, {}))
    assert [x["name"] for x in out["added"]] == ["fastapi"]
    assert [x["name"] for x in out["removed"]] == ["flask"]
    assert out["changed"] == [{"name": "requests", "from": "==2.0", "to": "==2.1"}]
    assert out["unchanged"] == 0

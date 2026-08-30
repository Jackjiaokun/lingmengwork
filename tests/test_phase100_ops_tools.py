# -*- coding: utf-8 -*-
"""Phase 100 隔离测试: 7 工具功能 + 优雅降级."""
import os
import sys
import json

sys.path.insert(0, r"D:\开发\配置AI应用\lingmengwork")

from lingmengwork.tools import suite_phase100 as m

CTX = {"roots": [os.getcwd()]}


def test_webhook_dispatch_dry_run():
    out = m.webhook_dispatch({
        "event": "push",
        "routes": {"push": "http://127.0.0.1:9/hook", "noop": "http://x"},
        "body": {"a": 1},
        "dry_run": True,
    }, CTX)
    assert "webhook_dispatch" in out and "dry_run" in out
    assert "http://127.0.0.1:9/hook" in out


def test_webhook_dispatch_missing_event():
    out = m.webhook_dispatch({}, CTX)
    assert "缺 event" in out


def test_sql_lint_warns():
    out = m.sql_lint({"sql": "select * from t where id=1"}, CTX)
    assert "告警" in out and "SELECT *" in out
    # 关键字小写告警
    assert "小写" in out


def test_sql_lint_delete_no_where():
    out = m.sql_lint({"sql": "DELETE FROM t"}, CTX)
    assert "缺少 WHERE" in out


def test_sql_lint_clean():
    out = m.sql_lint({"sql": "SELECT id, name FROM t WHERE id = 1 LIMIT 10"}, CTX)
    assert "未发现" in out


def test_json_schema_gen():
    sample = {"name": "x", "age": 3, "tags": ["a"], "meta": {"ok": True}}
    out = m.json_schema_gen({"text": json.dumps(sample)}, CTX)
    assert "json_schema_gen" in out
    sch = json.loads(out.split("\n", 1)[1])
    assert sch["properties"]["age"]["type"] == "integer"
    assert sch["properties"]["tags"]["type"] == "array"
    assert "name" in sch["required"]


def test_json_schema_gen_file(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"x": 1}), encoding="utf-8")
    out = m.json_schema_gen({"file": str(p)}, CTX)
    assert "integer" in out


def test_cron_next_n():
    out = m.cron_next_n({"expression": "0 9 * * 1-5", "count": 3}, CTX)
    assert "接下来 3 次" in out
    # 工作日 9:00
    lines = [l.strip() for l in out.splitlines() if l.strip().startswith("20")]
    assert len(lines) == 3


def test_cron_next_n_bad_segments():
    out = m.cron_next_n({"expression": "0 9"}, CTX)
    assert "5 段" in out


def test_diff_patch_apply():
    original = "line1\nline2\nline3\n"
    patch = "--- a\n+++ b\n@@ -1,3 +1,3 @@\n line1\n-line2\n+lineTWO\n line3\n"
    out = m.diff_patch({"original": original, "patch": patch}, CTX)
    assert "diff_patch" in out
    assert "lineTWO" in out and "line2" not in out.split("lineTWO")[0].splitlines()[-1] or "lineTWO" in out
    assert out.count("line1") >= 1


def test_diff_patch_missing():
    out = m.diff_patch({"original": "x"}, CTX)
    assert "缺 patch" in out


def test_yaml_merge_dict():
    a = "name: app\ncfg:\n  a: 1\n  b: 2\n"
    b = "cfg:\n  b: 3\n  c: 4\nver: 2\n"
    out = m.yaml_merge({"a": a, "b": b}, CTX)
    assert "yaml_merge" in out
    merged = json.loads(out.split("\n", 1)[1])
    assert merged["name"] == "app"
    assert merged["cfg"]["a"] == 1 and merged["cfg"]["b"] == 3 and merged["cfg"]["c"] == 4
    assert merged["ver"] == 2


def test_yaml_merge_list():
    a = "items:\n  - x\n  - y\n"
    b = "items:\n  - z\n"
    out = m.yaml_merge({"a": a, "b": b}, CTX)
    merged = json.loads(out.split("\n", 1)[1])
    assert merged["items"] == ["x", "y", "z"]


def test_yaml_merge_missing():
    out = m.yaml_merge({"a": "x:"}, CTX)
    assert "需要 a/b" in out


def test_hash_verify_builtin(tmp_path):
    import hashlib
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    exp = hashlib.sha256(b"hello").hexdigest()
    out = m.hash_verify({"file": str(p), "expected": exp, "algo": "sha256"}, CTX)
    assert "一致" in out
    out2 = m.hash_verify({"file": str(p), "expected": "deadbeef", "algo": "sha256"}, CTX)
    assert "不一致" in out2


def test_hash_verify_missing():
    out = m.hash_verify({"file": "nope.bin", "expected": "abc"}, CTX)
    assert "不存在" in out


def test_hash_verify_bad_algo():
    out = m.hash_verify({"file": "x", "expected": "a", "algo": "md9"}, CTX)
    assert "不支持" in out


def test_all_registered():
    from lingmengwork.tools import registry as reg
    for nm in ["webhook_dispatch", "sql_lint", "json_schema_gen", "cron_next_n",
               "diff_patch", "yaml_merge", "hash_verify"]:
        assert nm in reg._IMPLS
        assert any(s["name"] == nm for s in reg.TOOL_SCHEMAS)

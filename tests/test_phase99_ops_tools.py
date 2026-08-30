# -*- coding: utf-8 -*-
"""Phase 99 工具隔离测试 (不触发全量 pytest 挂死)."""
import json
import os
import tempfile

import pytest

import lingmengwork.tools.suite_phase99 as m

CTX = {"roots": [tempfile.gettempdir()]}


def _w(name, content):
    p = os.path.join(tempfile.gettempdir(), name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


# --- webhook_emit -----------------------------------------------------------
def test_webhook_emit_dry_run():
    out = m.webhook_emit({"url": "https://example.com/h", "body": {"a": 1}, "dry_run": True}, CTX)
    assert "dry_run" in out and "https://example.com/h" in out and "application/json" in out


def test_webhook_emit_signed_dry():
    out = m.webhook_emit({"url": "https://x.test/h", "body": "hi", "secret": "k", "dry_run": True}, CTX)
    assert "X-Signature" in out and "sha256=" in out and "X-Timestamp" in out


def test_webhook_emit_missing_url():
    assert "缺 url" in m.webhook_emit({}, CTX)


# --- sql_explain ------------------------------------------------------------
def test_sql_explain_select():
    out = m.sql_explain({"sql": "SELECT id, name FROM users WHERE age > 10"}, CTX)
    assert "select" in out and "users" in out and "id" in out and "name" in out


def test_sql_explain_insert():
    out = m.sql_explain({"sql": "INSERT INTO t(a,b) VALUES (1,2)"}, CTX)
    assert "insert" in out and "t" in out and "a" in out and "b" in out


def test_sql_explain_missing():
    assert "缺 sql" in m.sql_explain({}, CTX)


# --- csv_to_json ------------------------------------------------------------
def test_csv_to_json():
    p = _w("ph99.csv", "name,age\nalice,30\nbob,25\n")
    out = m.csv_to_json({"file": p}, CTX)
    rows = json.loads(out)
    assert len(rows) == 2 and rows[0]["name"] == "alice" and rows[1]["age"] == "25"


def test_csv_to_json_missing():
    assert "缺 file" in m.csv_to_json({}, CTX)


# --- hash_file --------------------------------------------------------------
def test_hash_file_sha256():
    import hashlib
    data = b"hello world"
    p = os.path.join(tempfile.gettempdir(), "ph99_hash.bin")
    with open(p, "wb") as f:
        f.write(data)
    out = m.hash_file({"file": p, "algorithms": "sha256"}, CTX)
    assert hashlib.sha256(data).hexdigest() in out


def test_hash_file_multi():
    data = b"abc"
    p = os.path.join(tempfile.gettempdir(), "ph99_h2.bin")
    with open(p, "wb") as f:
        f.write(data)
    out = m.hash_file({"file": p, "algorithms": ["md5", "sha1"]}, CTX)
    assert "md5:" in out and "sha1:" in out


def test_hash_file_missing():
    assert "缺 file" in m.hash_file({}, CTX)


# --- cron_parse -------------------------------------------------------------
def test_cron_parse_star():
    out = m.cron_parse({"expression": "* * * * *"}, CTX)
    assert "下次运行" in out and "周日" in out


def test_cron_parse_bad_segments():
    assert "需 5 段" in m.cron_parse({"expression": "* * *"}, CTX)


def test_cron_parse_describe():
    out = m.cron_parse({"expression": "0 12 * * 1"}, CTX)
    assert "周一" in out and "12" in out


# --- text_diff --------------------------------------------------------------
def test_text_diff_same():
    assert "完全相同" in m.text_diff({"a": "x\ny", "b": "x\ny"}, CTX)


def test_text_diff_diff():
    out = m.text_diff({"a": "a\nb\nc", "b": "a\nB\nc"}, CTX)
    assert "+" in out and "-" in out


def test_text_diff_lists():
    out = m.text_diff({"a": ["x", "y"], "b": ["x", "z"]}, CTX)
    assert "y" in out and "z" in out


# --- yaml_query -------------------------------------------------------------
def test_yaml_query_text_path():
    text = "db:\n  host: localhost\n  port: 5432\n  tags:\n    - a\n    - b\n"
    out = m.yaml_query({"text": text, "query": "db.port"}, CTX)
    assert "5432" in out


def test_yaml_query_list_index():
    text = "items:\n  - name: x\n  - name: y\n"
    out = m.yaml_query({"text": text, "query": "items[1].name"}, CTX)
    assert "y" in out


def test_yaml_query_full():
    text = "a: 1\nb:\n  c: hello\n"
    out = m.yaml_query({"text": text}, CTX)
    assert "hello" in out


def test_yaml_query_missing():
    text = "a: 1\n"
    out = m.yaml_query({"text": text, "query": "x.y.z"}, CTX)
    assert "未命中" in out

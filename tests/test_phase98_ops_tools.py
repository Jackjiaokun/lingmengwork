import sys
import os
import hmac
import hashlib
import time
import string
import pytest

ROOT = r"D:\开发\配置AI应用\lingmengwork"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lingmengwork.tools import suite_phase98 as m


def _ctx(tmp_path):
    return {"roots": [str(tmp_path)]}


# ---- webhook_verify ----
def test_webhook_verify_valid(tmp_path):
    ctx = _ctx(tmp_path)
    secret, payload = "topsecret", "hello"
    ts = 1000000
    body = "%d.%s" % (ts, payload)
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    out = m.webhook_verify({"payload": payload, "secret": secret,
                            "signature": "sha256=" + sig, "timestamp": ts, "tolerance": 300}, ctx)
    assert "签名有效" in out


def test_webhook_verify_invalid(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.webhook_verify({"payload": "a", "secret": "s", "signature": "sha256=deadbeef"}, ctx)
    assert "签名无效" in out


def test_webhook_verify_replay(tmp_path):
    ctx = _ctx(tmp_path)
    secret, payload = "s", "x"
    ts = int(time.time()) - 1000
    body = "%d.%s" % (ts, payload)
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    out = m.webhook_verify({"payload": payload, "secret": secret, "signature": sig,
                            "timestamp": ts, "tolerance": 300}, ctx)
    assert "重放风险" in out


def test_webhook_verify_missing(tmp_path):
    ctx = _ctx(tmp_path)
    assert "缺 secret" in m.webhook_verify({"payload": "x", "signature": "y"}, ctx)
    assert "缺 signature" in m.webhook_verify({"payload": "x", "secret": "s"}, ctx)


# ---- sql_format ----
def test_sql_format_inline(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.sql_format({"sql": "select a,b from t where a=1 and b=2 order by a"}, ctx)
    assert "SELECT" in out and "FROM" in out and "\n" in out


def test_sql_format_file(tmp_path):
    ctx = _ctx(tmp_path)
    p = tmp_path / "q.sql"
    p.write_text("select * from users where id=1", encoding="utf-8")
    out = m.sql_format({"file": "q.sql"}, ctx)
    assert "SELECT" in out and "FROM" in out


# ---- csv_diff ----
def test_csv_diff_key(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "a.csv").write_text("id,name\n1,a\n2,b\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,name\n1,a\n2,c\n", encoding="utf-8")
    out = m.csv_diff({"a": "a.csv", "b": "b.csv", "key": "id"}, ctx)
    assert "修改" in out and "name" in out


def test_csv_diff_nokey(tmp_path):
    ctx = _ctx(tmp_path)
    (tmp_path / "a.csv").write_text("x\n1\n2\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("x\n1\n3\n", encoding="utf-8")
    out = m.csv_diff({"a": "a.csv", "b": "b.csv"}, ctx)
    assert "差异" in out


# ---- json_schema_validate ----
def test_json_schema_validate_pass(tmp_path):
    ctx = _ctx(tmp_path)
    data = '{"name":"张三","age":3}'
    schema = '{"type":"object","required":["name","age"],"properties":{"name":{"type":"string"},"age":{"type":"integer"}}}'
    out = m.json_schema_validate({"data": data, "schema": schema}, ctx)
    assert "校验通过" in out


def test_json_schema_validate_fail(tmp_path):
    ctx = _ctx(tmp_path)
    data = '{"name":"张三"}'
    schema = '{"type":"object","required":["name","age"],"properties":{"name":{"type":"string"},"age":{"type":"integer"}}}'
    out = m.json_schema_validate({"data": data, "schema": schema}, ctx)
    assert "校验未通过" in out and "age" in out


def test_json_schema_validate_type(tmp_path):
    ctx = _ctx(tmp_path)
    data = '{"age":"not_int"}'
    schema = '{"type":"object","properties":{"age":{"type":"integer"}}}'
    out = m.json_schema_validate({"data": data, "schema": schema}, ctx)
    assert "校验未通过" in out


# ---- release_tag ----
def test_release_tag_bump(tmp_path):
    ctx = _ctx(tmp_path)
    assert "1.3.0" in m.release_tag({"version": "1.2.3", "bump": "minor"}, ctx)
    assert "2.0.0" in m.release_tag({"version": "1.2.3", "bump": "major"}, ctx)
    assert "1.2.4" in m.release_tag({"version": "1.2.3", "bump": "patch"}, ctx)


def test_release_tag_compare(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.release_tag({"version": "2.0.0", "compare": "1.5.0"}, ctx)
    assert "2.0.0 更新" in out


def test_release_tag_invalid(tmp_path):
    ctx = _ctx(tmp_path)
    assert "非法版本号" in m.release_tag({"version": "v1"}, ctx)


# ---- log_tail ----
def test_log_tail(tmp_path):
    ctx = _ctx(tmp_path)
    lines = ["line%d" % i for i in range(20)]
    (tmp_path / "app.log").write_text("\n".join(lines), encoding="utf-8")
    out = m.log_tail({"file": "app.log", "n": 5}, ctx)
    assert "line19" in out and "line15" in out and "line14" not in out
    out2 = m.log_tail({"file": "app.log", "n": 50, "grep": "line1"}, ctx)
    assert "line1" in out2 and "line0" not in out2


# ---- password_generate ----
def test_password_generate(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.password_generate({"length": 20, "count": 3}, ctx)
    pwds = [l for l in out.split("\n") if l and len(l) == 20]
    assert len(pwds) == 3


def test_password_no_symbols(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.password_generate({"length": 12, "symbol": False, "upper": False, "digit": False}, ctx)
    pw = [l for l in out.split("\n") if l and len(l) == 12][0]
    assert all(c in string.ascii_lowercase for c in pw)


def test_password_readable(tmp_path):
    ctx = _ctx(tmp_path)
    out = m.password_generate({"length": 30, "readable": True}, ctx)
    pw = [l for l in out.split("\n") if l and len(l) == 30][0]
    ambiguous = set("l1IoO0|`'\";:.,")
    assert not (set(pw) & ambiguous)

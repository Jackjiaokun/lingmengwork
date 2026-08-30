# -*- coding: utf-8 -*-
"""Phase 103 隔离测试: 数据工程 / 安全合规续集 7 工具."""
import json
import os
import pytest

from lingmengwork.tools import suite_phase103 as m
import lingmengwork.tools.registry as reg


def _ctx(tmp_path):
    return {"cwd": str(tmp_path)}


def test_yaml_to_json_nested(tmp_path):
    out = m.yaml_to_json({"text": "name: alice\nage: 30\nattrs:\n  height: 170\n  vip: true\n"}, _ctx(tmp_path))
    obj = json.loads(out)
    assert obj == {"name": "alice", "age": 30, "attrs": {"height": 170, "vip": True}}


def test_yaml_to_json_list_of_maps(tmp_path):
    out = m.yaml_to_json({"text": "- name: a\n  v: 1\n- name: b\n  v: 2\n"}, _ctx(tmp_path))
    obj = json.loads(out)
    assert obj == [{"name": "a", "v": 1}, {"name": "b", "v": 2}]


def test_yaml_to_json_scalar_types(tmp_path):
    out = m.yaml_to_json({"text": "a: 1\nb: 2.5\nc: true\nd: false\ne: null\nf: ~\n"}, _ctx(tmp_path))
    obj = json.loads(out)
    assert obj["a"] == 1 and obj["b"] == 2.5 and obj["c"] is True and obj["d"] is False
    assert obj["e"] is None and obj["f"] is None


def test_yaml_to_json_comment(tmp_path):
    out = m.yaml_to_json({"text": "# header\nk: v  # inline\n"}, _ctx(tmp_path))
    obj = json.loads(out)
    assert obj == {"k": "v"}


def test_json_to_yaml_roundtrip(tmp_path):
    obj = {"a": 1, "b": [1, 2, {"c": 3}], "d": {"e": "x:y"}}
    text = json.dumps(obj, ensure_ascii=False)
    yml = m.json_to_yaml({"text": text}, _ctx(tmp_path))
    back = json.loads(m.yaml_to_json({"text": yml}, _ctx(tmp_path)))
    assert back == obj


def test_xml_to_csv(tmp_path):
    xml = "<root><row id=\"1\"><name>x</name></row><row id=\"2\"><name>y</name></row></root>"
    out = m.xml_to_csv({"text": xml}, _ctx(tmp_path))
    assert "id,name" in out
    assert "1,x" in out and "2,y" in out


def test_toml_to_yaml(tmp_path):
    toml = "[server]\nhost = \"127.0.0.1\"\nport = 8080\n"
    out = m.toml_to_yaml({"text": toml}, _ctx(tmp_path))
    assert "host: 127.0.0.1" in out
    assert "port: 8080" in out


def test_license_compat_incompatible(tmp_path):
    out = m.license_compat({"licenses": "GPL, Proprietary"}, _ctx(tmp_path))
    assert "不兼容" in out


def test_license_compat_permissive(tmp_path):
    out = m.license_compat({"licenses": "MIT, Apache-2.0"}, _ctx(tmp_path))
    assert "兼容" in out
    assert "不兼容" not in out


def test_dep_outdated_unpinned(tmp_path):
    f = tmp_path / "requirements.txt"
    f.write_text("flask\nrequests==2.0\n", encoding="utf-8")
    out = m.dep_outdated({"path": "requirements.txt"}, _ctx(tmp_path))
    assert "未固定" in out
    assert "flask" in out


def test_file_classify_png(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nrest")
    out = m.file_classify({"file": "a.png"}, _ctx(tmp_path))
    assert "image/png" in out


def test_file_classify_pdf(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-1.4 header")
    out = m.file_classify({"file": "a.pdf"}, _ctx(tmp_path))
    assert "application/pdf" in out


def test_file_classify_text(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello world\n", encoding="utf-8")
    out = m.file_classify({"file": "a.txt"}, _ctx(tmp_path))
    assert "text/plain" in out


def test_all_registered():
    new7 = ["yaml_to_json", "json_to_yaml", "xml_to_csv", "toml_to_yaml", "license_compat", "dep_outdated", "file_classify"]
    for nm in new7:
        assert nm in reg._IMPLS
        assert any(s["name"] == nm for s in reg.TOOL_SCHEMAS)
        assert nm in reg._READONLY_TOOLS
        assert nm in reg._CACHEABLE_TOOLS

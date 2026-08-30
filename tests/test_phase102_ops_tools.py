# -*- coding: utf-8 -*-
"""Phase 102 隔离测试: 数据工程 / 安全合规续集 7 工具."""
import json
import os
import tempfile

import pytest

from lingmengwork.tools import suite_phase102 as m


def _ctx(tmp_path):
    return {"roots": [str(tmp_path)], "cwd": str(tmp_path), "session_id": "t"}


def test_xml_to_json_string():
    out = m.xml_to_json({"xml": "<root><a>1</a><b>x</b></root>"}, _ctx(""))
    assert '"root"' in out
    assert '"a": "1"' in out


def test_xml_to_json_file(tmp_path):
    p = tmp_path / "x.xml"
    p.write_text("<note><to>A</to><from>B</from></note>", encoding="utf-8")
    out = m.xml_to_json({"file": str(p)}, _ctx(tmp_path))
    assert '"note"' in out
    assert '"to": "A"' in out


def test_xml_to_json_attrs():
    out = m.xml_to_json({"xml": '<root id="7"><c>v</c></root>'}, _ctx(""))
    assert "@attributes" in out
    assert '"id": "7"' in out


def test_xml_to_json_out_file(tmp_path):
    out = m.xml_to_json({"xml": "<r><x>1</x></r>", "out_file": "o.json"}, _ctx(tmp_path))
    assert "已写出" in out
    assert os.path.isfile(str(tmp_path / "o.json"))


def test_json_to_sql_dict():
    out = m.json_to_sql({"json": '{"name":"a","age":3}', "table": "t"}, _ctx(""))
    assert "INSERT INTO t (name, age) VALUES ('a', 3);" in out


def test_json_to_sql_array_writes(tmp_path):
    out = m.json_to_sql({"json": '[{"k":1},{"k":2}]', "table": "tt",
                          "out_file": "o.sql"}, _ctx(tmp_path))
    assert "已写出 2 条" in out
    txt = (tmp_path / "o.sql").read_text(encoding="utf-8")
    assert txt.count("INSERT INTO tt") == 2


def test_json_to_sql_bad_table():
    out = m.json_to_sql({"json": '{"a":1}', "table": "bad table!"}, _ctx(""))
    assert "非法" in out


def test_toml_to_json_string():
    out = m.toml_to_json({"toml": '[sec]\nname = "x"\nnum = 3\n'}, _ctx(""))
    assert '"name": "x"' in out
    assert '"num": 3' in out


def test_toml_to_json_file(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[a]\nk = "v"\n', encoding="utf-8")
    out = m.toml_to_json({"file": str(p)}, _ctx(tmp_path))
    assert '"k": "v"' in out


def test_json_patch_add():
    out = m.json_patch({"json": '{"a":1}', "patch": '[{"op":"add","path":"/b","value":2}]'}, _ctx(""))
    assert '"b": 2' in out


def test_json_patch_remove():
    out = m.json_patch({"json": '{"a":1,"b":2}', "patch": '[{"op":"remove","path":"/a"}]'}, _ctx(""))
    assert '"a"' not in out
    assert '"b": 2' in out


def test_json_patch_test_fail():
    out = m.json_patch({"json": '{"a":1}', "patch": '[{"op":"test","path":"/a","value":9}]'}, _ctx(""))
    assert "test 失败" in out


def test_secret_mask_password():
    text = 'config password="s3cr3tpass" end'
    out = m.secret_mask({"text": text}, _ctx(""))
    assert "[REDACTED]" in out
    assert "s3cr3tpass" not in out


def test_secret_mask_aws_key():
    text = "key=AKIAIOSFODNN7EXAMPLEZ more"
    out = m.secret_mask({"text": text}, _ctx(""))
    assert "[REDACTED]" in out
    assert "AKIAIOSFODNN7EXAMPLEZ" not in out


def test_secret_mask_clean():
    out = m.secret_mask({"text": "nothing secret here"}, _ctx(""))
    assert "未发现敏感信息" in out


def test_secret_mask_out_file(tmp_path):
    out = m.secret_mask({"text": 'password="abcdefgh"', "out_file": "m.txt"}, _ctx(tmp_path))
    assert "已掩码" in out
    assert "[REDACTED]" in (tmp_path / "m.txt").read_text(encoding="utf-8")


def test_sbom_gen(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"lodash":"^4.17.0"},"devDependencies":{"jest":"^29"}}',
        encoding="utf-8")
    out = m.sbom_gen({"path": str(tmp_path)}, _ctx(tmp_path))
    d = json.loads(out)
    assert d["component_count"] >= 2
    names = [c["name"] for c in d["components"]]
    assert "lodash" in names and "jest" in names


def test_dep_graph(tmp_path):
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import os\n", encoding="utf-8")
    out = m.dep_graph({"path": str(tmp_path)}, _ctx(tmp_path))
    d = json.loads(out)
    assert d["module_count"] == 2
    assert any(e["to"] == "b" for e in d["edges"])

"""Phase 108 工具隔离测试 (零网络, 标准库). 覆盖 7 个工程向工具的正常与降级路径."""
import json

from lingmengwork.tools import suite_phase108 as s

SCHEMA = ('{"type":"object","properties":{"a":{"type":"string"},'
          '"b":{"type":"array","items":{"type":"integer"}}},"required":["a"]}')


# --- json_schema_to_go -----------------------------------------------------
def test_go_struct_and_tags():
    out = s.json_schema_to_go({"schema": SCHEMA, "name": "User", "package": "model"}, {})
    assert "package model" in out
    assert "type User struct {" in out
    assert 'A string `json:"a"`' in out              # 必填: 无 omitempty
    assert 'B []int `json:"b,omitempty"`' in out     # 可选: 带 omitempty


def test_go_nested_before_main():
    sch = ('{"type":"object","properties":{"o":{"type":"object",'
           '"properties":{"x":{"type":"number"}},"required":["x"]}},'
           '"required":["o"]}')
    out = s.json_schema_to_go({"schema": sch, "name": "Root"}, {})
    assert out.index("type O struct") < out.index("type Root struct")
    assert "X float64" in out


def test_go_bad():
    out = s.json_schema_to_go({"schema": "nope"}, {})
    assert out.startswith("[json_schema_to_go]")


# --- json_schema_to_java ---------------------------------------------------
def test_java_pojo():
    out = s.json_schema_to_java({"schema": SCHEMA, "name": "User"}, {})
    assert "public class User {" in out
    assert "private String a;" in out
    assert "public String getA()" in out and "public void setA(String a)" in out


def test_java_generic_boxed():
    """Java 泛型不支持基本类型: 必须是 List<Integer> 而非 List<int>。"""
    out = s.json_schema_to_java({"schema": SCHEMA, "name": "User"}, {})
    assert "List<Integer>" in out
    assert "List<int>" not in out


def test_java_bad():
    out = s.json_schema_to_java({"schema": "{"}, {})
    assert out.startswith("[json_schema_to_java]")


# --- markdown_table_to_csv -------------------------------------------------
def test_md_table_to_csv():
    md = "| name | age |\n| --- | --- |\n| x | 1 |\n| y | 2 |"
    out = s.markdown_table_to_csv({"markdown": md}, {})
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0] == "name,age"
    assert "x,1" in lines and "y,2" in lines
    assert not any("---" in l for l in lines)  # 分隔行已跳过


def test_md_table_no_table():
    out = s.markdown_table_to_csv({"markdown": "just text"}, {})
    assert out.startswith("[markdown_table_to_csv]")


# --- sql_to_json -----------------------------------------------------------
def test_sql_to_json_columns():
    sql = ("CREATE TABLE users (\n  id INTEGER PRIMARY KEY,\n"
           "  name TEXT NOT NULL,\n  age INT\n);")
    data = json.loads(s.sql_to_json({"sql": sql}, {}))
    assert data["table"] == "users"
    cols = {c["name"]: c for c in data["columns"]}
    assert cols["id"]["constraints"] == ["primary_key"]
    assert cols["name"]["type"] == "TEXT" and "not_null" in cols["name"]["constraints"]
    assert cols["age"]["constraints"] == []


def test_sql_to_json_no_create():
    out = s.sql_to_json({"sql": "SELECT 1;"}, {})
    assert out.startswith("[sql_to_json]")


# --- env_to_json -----------------------------------------------------------
def test_env_to_json():
    out = s.env_to_json({"env": "# comment\nexport A=1\nB=\"x\"\nC='y'\n"}, {})
    data = json.loads(out)
    assert data == {"A": "1", "B": "x", "C": "y"}


def test_env_to_json_empty():
    assert json.loads(s.env_to_json({"env": "# only comment"}, {})) == {}


# --- dockerfile_lint -------------------------------------------------------
def test_dockerfile_lint_issues():
    df = ("FROM python:latest\n"
          "RUN apt-get update && apt-get install -y curl\n"
          "RUN sudo echo hi\n"
          "ADD local.txt /tmp/")
    data = json.loads(s.dockerfile_lint({"dockerfile": df}, {}))
    rules = {i["rule"] for i in data["issues"]}
    assert "from-latest" in rules
    assert "run-sudo" in rules
    assert "add-vs-copy" in rules
    assert data["ok"] is True  # 无 error 级问题


def test_dockerfile_lint_no_from_is_error():
    data = json.loads(s.dockerfile_lint({"dockerfile": "RUN echo hi"}, {}))
    assert data["ok"] is False
    assert any(i["rule"] == "no-from" for i in data["issues"])


# --- gitignore_gen ---------------------------------------------------------
def test_gitignore_gen_multi_stack():
    out = s.gitignore_gen({"stacks": "python,node"}, {})
    assert "__pycache__/" in out and "node_modules/" in out
    assert "# ===== python =====" in out and "# ===== node =====" in out


def test_gitignore_gen_unknown_and_empty():
    out = s.gitignore_gen({"stacks": "python,erlang"}, {})
    assert "未知模板" in out and "erlang" in out
    assert s.gitignore_gen({"stacks": ""}, {}).startswith("[gitignore_gen]")

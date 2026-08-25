"""设置中心 (批次14): _config_path / _set_scalar_in_toml / _settings_get 单测。"""
import tomllib

import pytest

import lingmengwork.web.server as S

SAMPLE = '''
[llm]
backend = "sensenova"

[agent]
max_iterations = 32
tool_result_max_chars = 6000

[agent.security]
destructive_guard = "block"
audit_log = true

[mcp]
enabled = true
'''

CFG_KEYS = [f["key"] for g in S._SETTINGS_SCHEMA for f in g["fields"]]


def test_config_path_hits_existing(monkeypatch, tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(S, "DEFAULT_CONFIG_PATHS", [p])
    assert S._config_path() == p


def test_config_path_falls_back_first_candidate(monkeypatch, tmp_path):
    missing = tmp_path / "nope.toml"
    monkeypatch.setattr(S, "DEFAULT_CONFIG_PATHS", [missing])
    assert S._config_path() == missing


def test_set_scalar_replaces_existing():
    new, applied = S._set_scalar_in_toml(SAMPLE, "agent", "max_iterations", 64, "int")
    assert applied
    cfg = tomllib.loads(new)
    assert cfg["agent"]["max_iterations"] == 64
    # 同段其它标量保留
    assert cfg["agent"]["tool_result_max_chars"] == 6000


def test_set_scalar_replaces_nested_section():
    new, applied = S._set_scalar_in_toml(SAMPLE, "agent.security", "destructive_guard", "off", "string")
    assert applied
    cfg = tomllib.loads(new)
    assert cfg["agent"]["security"]["destructive_guard"] == "off"
    # 同段其它字段保留
    assert cfg["agent"]["security"]["audit_log"] is True


def test_set_scalar_inserts_missing_key():
    new, applied = S._set_scalar_in_toml(SAMPLE, "mcp", "log_level", "info", "string")
    assert applied
    cfg = tomllib.loads(new)
    assert cfg["mcp"]["log_level"] == "info"
    assert cfg["mcp"]["enabled"] is True


def test_fmt_toml_value():
    assert S._fmt_toml_value("k", True, "bool") == "true"
    assert S._fmt_toml_value("k", False, "bool") == "false"
    assert S._fmt_toml_value("k", 0, "int") == "0"
    assert S._fmt_toml_value("k", 42, "int") == "42"
    # 字符串含引号需转义
    assert S._fmt_toml_value("k", 'he said "hi"', "string") == '"he said \\"hi\\""'


def test_cfg_get_nested():
    assert S._cfg_get({"a": {"b": {"c": 1}}}, "a.b.c") == 1
    assert S._cfg_get({"a": {}}, "a.x.y") is None
    assert S._cfg_get({"a": 1}, "a.b") is None


def test_settings_get_structure(monkeypatch, tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(S, "DEFAULT_CONFIG_PATHS", [p])
    data = S.Handler._settings_get(None)
    assert data["path"] == str(p)
    assert data["exists"] is True
    assert data["raw"] == SAMPLE
    assert isinstance(data["schema"], list) and len(data["schema"]) > 0
    for k in CFG_KEYS:
        assert k in data["values"], "schema 字段应在 values 中: %s" % k
    assert data["version"]


def test_settings_get_handles_missing_file(monkeypatch, tmp_path):
    missing = tmp_path / "absent.toml"
    monkeypatch.setattr(S, "DEFAULT_CONFIG_PATHS", [missing])
    data = S.Handler._settings_get(None)
    assert data["exists"] is False
    assert data["raw"] == ""
    # schema/values 仍应完整返回, 供前端展示
    assert len(data["schema"]) > 0
    for k in CFG_KEYS:
        assert k in data["values"]

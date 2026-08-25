import os
import tempfile

from lingmengwork.config import load_config, resolve_roots


def test_default_backend_is_sensenova():
    cfg = load_config()
    assert cfg["llm"]["backend"] == "sensenova"


def test_openai_api_key_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-123")
    cfg = load_config()
    # backend 默认 ollama, 但 openai 段的 api_key 也应能从 env 注入
    assert cfg["llm"]["openai"]["api_key"] == "sk-test-123"


def test_explicit_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "config.toml"
    p.write_text(
        '[llm]\nbackend = "mock"\n\n[llm.mock]\nmodel = "my-mock"\n',
        encoding="utf-8",
    )
    cfg = load_config(str(p))
    assert cfg["llm"]["backend"] == "mock"
    assert cfg["llm"]["mock"]["model"] == "my-mock"


def test_resolve_roots_absolute_and_relative(tmp_path):
    cfg = {"agent": {"security": {"allowed_roots": ["."]}}}
    roots = resolve_roots(cfg, base_dir=str(tmp_path))
    assert roots[0] == tmp_path.resolve()

import json

import pytest

from relate_codemem import config


def write_settings(root, name, env):
    path = root / ".claude" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"env": env}))
    config.reset(root)


def test_default_is_local(project):
    cfg = config.embedding_config()
    assert (cfg["provider"], cfg["source"]) == ("local", "default")
    assert "MiniLM" in cfg["model"]


def test_install_default_then_project_override(project, monkeypatch):
    config.save_global({"embeddings": {"provider": "ollama", "model": "nomic-embed-text"}})
    cfg = config.embedding_config()
    assert (cfg["provider"], cfg["model"], cfg["url"]) == ("ollama", "nomic-embed-text", "http://localhost:11434")

    write_settings(project, "settings.json", {"RELATE_CODEMEM_EMBEDDINGS": "openai"})
    cfg = config.embedding_config()
    assert (cfg["provider"], cfg["model"], cfg["source"]) == ("openai", "text-embedding-3-small", ".claude/settings.json")

    write_settings(project, "settings.local.json", {"RELATE_CODEMEM_EMBEDDINGS": "none"})
    assert config.embedding_config()["provider"] == "none"
    assert config.embedding_config()["source"] == ".claude/settings.local.json"

    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "local")
    assert config.embedding_config()["source"] == "environment"


def test_field_overrides_and_aliases(project):
    write_settings(project, "settings.local.json", {
        "RELATE_CODEMEM_EMBEDDINGS": "openai-compatible", "RELATE_CODEMEM_EMBEDDING_MODEL": "mistral-embed",
        "RELATE_CODEMEM_EMBEDDING_URL": "https://api.mistral.ai/v1",
        "RELATE_CODEMEM_EMBEDDING_API_KEY_ENV": "MISTRAL_API_KEY", "RELATE_CODEMEM_EMBEDDING_DIMENSIONS": "1024"})
    cfg = config.embedding_config()
    assert (cfg["provider"], cfg["model"], cfg["api_key_env"], cfg["dimensions"]) == (
        "openai", "mistral-embed", "MISTRAL_API_KEY", 1024)


def test_unknown_provider_is_an_error(project):
    write_settings(project, "settings.local.json", {"RELATE_CODEMEM_EMBEDDINGS": "magic"})
    with pytest.raises(config.ConfigError):
        config.embedding_config()


def test_project_options_file(project):
    (project / ".claude").mkdir()
    (project / ".claude" / "relate-codemem.json").write_text(json.dumps({"meetings_dirs": ["notes"], "bogus": 1}))
    config.reset(project)
    cfg = config.project_config()
    assert cfg["meetings_dirs"] == ["notes"] and "bogus" not in cfg and cfg["chunk_chars"] == 1800

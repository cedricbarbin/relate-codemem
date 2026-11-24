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


def test_setup_installs_every_skill(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    from relate_codemem import project as setup
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = setup.install_skills()
    assert sorted(p.name for p in root.iterdir()) == sorted(setup.SKILLS)
    for name in setup.SKILLS:
        text = (root / name / "SKILL.md").read_text()
        assert text.startswith(f"---\nname: {name}\n") and "description:" in text


def test_sharedb_needs_a_confirmation_and_keeps_secrets_ignored(project, capsys, monkeypatch):
    import shutil
    import subprocess

    from relate_codemem import cli, project as setup
    (project / ".gitignore").write_text("node_modules/\n# relate-codemem store (local)\n.claude/relate/\n")
    home = project / ".claude" / "relate"
    (home / "db").mkdir(parents=True)
    for name in ("db/manifest.json", "db.auth-token", "memory.log", "state.json", "deps.json", "embeddings.json", ".schema-v2"):
        (home / name).write_text("x")
    monkeypatch.setattr(setup.store, "run", lambda *a, **k: [[]])        # no relate needed for this test
    monkeypatch.setattr(setup.store, "ensure_store", lambda: None)

    assert cli.main(["sharedb"]) == 1                                    # not a terminal, no --yes: refused
    out = capsys.readouterr().out
    assert "NOT recommended on a public repository" in out and "prompts" in out and "Not changed" in out
    assert ".claude/relate/" in (project / ".gitignore").read_text().splitlines()

    assert cli.main(["sharedb", "--yes"]) == 0
    lines = (project / ".gitignore").read_text().splitlines()
    assert "node_modules/" in lines and ".claude/relate/" not in lines and "!.claude/relate/db/" in lines
    if shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=project, check=True, **config.NO_WINDOW)

        def ignored(name):
            return subprocess.run(["git", "check-ignore", "-q", f".claude/relate/{name}"], cwd=project,
                                  **config.NO_WINDOW).returncode == 0

        assert not any(ignored(n) for n in ("db/manifest.json", "deps.json", "embeddings.json", ".schema-v2"))
        assert all(ignored(n) for n in ("db.auth-token", "memory.log", "state.json", "write.lock", "spool/events.jsonl"))

    # init leaves a shared store shared, and --off restores the local rule
    before = (project / ".gitignore").read_text()
    setup.init_project(project, {})
    assert (project / ".gitignore").read_text() == before
    assert cli.main(["sharedb", "--off"]) == 0
    lines = (project / ".gitignore").read_text().splitlines()
    assert ".claude/relate/" in lines and "!.claude/relate/db/" not in lines and "node_modules/" in lines
    assert "git rm -r --cached" in capsys.readouterr().out

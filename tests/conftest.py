import shutil

import pytest

from relate_codemem import config


@pytest.fixture
def project(tmp_path, monkeypatch):
    """An empty project directory with an isolated user configuration."""
    root = tmp_path / "proj"
    root.mkdir()
    for var in list(__import__("os").environ):
        if var.startswith(config.ENV) or var == "CLAUDE_PROJECT_DIR":
            monkeypatch.delenv(var)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.chdir(root)
    config.reset(root)
    yield root
    config.reset()


needs_relate = pytest.mark.skipif(shutil.which("relate") is None, reason="relate binary not installed")

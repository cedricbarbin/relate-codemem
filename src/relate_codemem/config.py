"""Paths and layered configuration.

Three layers, most specific first:

1. the process environment, then the `env` block of the project's
   `.claude/settings.local.json`, then of `.claude/settings.json`
   (variables named RELATE_CODEMEM_*);
2. the user configuration written by `relate-codemem setup`
   (~/.config/relate-codemem/config.json, %APPDATA% on Windows);
3. built-in defaults (local embeddings).

Indexing options that describe the repository (directories, exclusions) live in
the project file `.claude/relate-codemem.json`.
"""
import json
import os
import subprocess
from pathlib import Path

# On Windows a console program started by a process that has no console (a hook
# launched by a desktop app, the detached embedding pass) opens its own visible
# window. CREATE_NO_WINDOW gives the child a hidden console instead, which its
# own children inherit.
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}

APP = "relate-codemem"
ENV = "RELATE_CODEMEM_"
SCHEMA_VERSION = 2

PROJECT_DEFAULTS = {
    "decisions_dirs": ["docs/decisions", "docs/adr", "adr"],
    "meetings_dirs": ["docs/meetings", "meetings"],
    "exclude_dirs": [".git", ".claude", "node_modules", ".venv", "venv", "__pycache__", "dist",
                     "build", "target", ".next", ".nuxt", "vendor", ".idea", ".vscode", "coverage",
                     ".pytest_cache", ".mypy_cache", ".tox", "DerivedData", "Pods", ".gradle"],
    "exclude_files": ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
                      "Cargo.lock", "uv.lock", "composer.lock", "Gemfile.lock"],
    "max_file_bytes": 500000,
    "chunk_chars": 1800,
    "max_prompt_chars": 6000,
    "max_reply_chars": 8000,
}

PROVIDERS = ("local", "ollama", "openai", "none")
EMBEDDING_DEFAULTS = {
    "local": {"model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
              "min_similarity": 0.62},
    "ollama": {"model": "bge-m3", "url": "http://localhost:11434", "min_similarity": 0.60},
    "openai": {"model": "text-embedding-3-small", "url": "https://api.openai.com/v1",
               "api_key_env": "OPENAI_API_KEY", "min_similarity": 0.60},
    "fake": {"model": "hash-16", "min_similarity": 0.55},        # deterministic, for tests
    "none": {},
}
_ALIASES = {"fastembed": "local", "off": "none", "false": "none", "no": "none", "0": "none",
            "disabled": "none", "openai-compatible": "openai", "api": "openai"}


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------- user-level locations

def user_config_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP


def user_cache_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP / "cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP


def global_config_path():
    return user_config_dir() / "config.json"


def load_global():
    try:
        return json.loads(global_config_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_global(data):
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return path


# ---------------------------------------------------------------- project root

_state = {"root": None, "settings_env": None, "project": None}


def reset(root=None):
    """Forget cached lookups; optionally pin the project root (init, tests)."""
    _state.update(root=Path(root).resolve() if root else None, settings_env=None, project=None)


def _git_toplevel(cwd):
    try:
        out = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], **NO_WINDOW,
                             capture_output=True, text=True, timeout=10)
        return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def project_root():
    """Project the command applies to: Claude Code's project directory when a hook or a
    tool call runs us, else the nearest initialised ancestor, else the git root, else cwd."""
    if _state["root"]:
        return _state["root"]
    for var in (ENV + "PROJECT", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(var)
        if value and Path(value).is_dir():
            _state["root"] = Path(value).resolve()
            return _state["root"]
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".claude" / "relate").is_dir() or (candidate / ".claude" / f"{APP}.json").is_file():
            _state["root"] = candidate
            return candidate
    _state["root"] = _git_toplevel(cwd) or cwd
    return _state["root"]


# ---------------------------------------------------------------- settings

def _settings_env():
    """RELATE_CODEMEM_* variables declared in the project's Claude Code settings files.
    Read from the files so that a terminal run and a hook run agree."""
    if _state["settings_env"] is None:
        found = {}
        for name in ("settings.json", "settings.local.json"):          # local wins
            path = project_root() / ".claude" / name
            try:
                env = json.loads(path.read_text("utf-8")).get("env") or {}
            except (OSError, ValueError, AttributeError):
                continue
            for key, value in env.items():
                if key.startswith(ENV) and value not in (None, ""):
                    found[key] = (str(value), f".claude/{name}")
        _state["settings_env"] = found
    return _state["settings_env"]


def setting(name):
    """-> (value, source) for RELATE_CODEMEM_<name>, or (None, None)."""
    key = ENV + name
    if os.environ.get(key):
        return os.environ[key], "environment"
    return _settings_env().get(key, (None, None))


def store_home():
    value, _ = setting("HOME")
    return Path(value) if value else project_root() / ".claude" / "relate"


def project_config_path():
    return project_root() / ".claude" / f"{APP}.json"


def project_config():
    if _state["project"] is None:
        cfg = dict(PROJECT_DEFAULTS)
        try:
            data = json.loads(project_config_path().read_text("utf-8"))
            cfg.update({k: v for k, v in data.items() if k in PROJECT_DEFAULTS})
        except (OSError, ValueError):
            pass
        _state["project"] = cfg
    return _state["project"]


def normalise_provider(name):
    name = (name or "").strip().lower()
    name = _ALIASES.get(name, name)
    if name not in EMBEDDING_DEFAULTS:
        raise ConfigError(f"unknown embedding provider '{name}' (choose: {', '.join(PROVIDERS)})")
    return name


def embedding_config():
    """Effective embedding settings: {provider, model, url, api_key_env, dimensions,
    min_similarity, source}. Default when nothing is configured: local."""
    stored = load_global().get("embeddings") or {}
    value, source = setting("EMBEDDINGS")
    if value:
        provider = normalise_provider(value)
    elif stored.get("provider"):
        provider, source = normalise_provider(stored["provider"]), str(global_config_path())
    else:
        provider, source = "local", "default"
    cfg = dict(EMBEDDING_DEFAULTS[provider], provider=provider, source=source)
    if stored.get("provider") and normalise_provider(stored["provider"]) == provider:
        cfg.update({k: v for k, v in stored.items() if k != "provider" and v not in (None, "")})
    for key, var, cast in (("model", "EMBEDDING_MODEL", str), ("url", "EMBEDDING_URL", str),
                           ("api_key_env", "EMBEDDING_API_KEY_ENV", str),
                           ("dimensions", "EMBEDDING_DIMENSIONS", int),
                           ("min_similarity", "EMBEDDING_MIN_SIMILARITY", float)):
        raw, _ = setting(var)
        if raw:
            try:
                cfg[key] = cast(raw)
            except ValueError:
                raise ConfigError(f"{ENV}{var}={raw!r} is not a valid {cast.__name__}")
    return cfg

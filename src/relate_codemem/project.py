"""Machine setup (`setup`), project initialisation (`init`) and diagnostics (`doctor`)."""
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from importlib import resources
from pathlib import Path

from . import __version__, config, parse, store

HOOK_COMMAND = "relate-codemem hook"
HOOKS = {
    "SessionStart": (None, 120), "UserPromptSubmit": (None, 20),
    "PostToolUse": ("Edit|Write|MultiEdit|NotebookEdit|Read|Bash", 60),
    "Stop": (None, 90), "PreCompact": (None, 90), "SessionEnd": (None, 90),
}
PERMISSIONS = ["Bash(relate-codemem:*)", "Bash(rcm:*)"]
MARK_BEGIN, MARK_END = "<!-- relate-codemem:begin -->", "<!-- relate-codemem:end -->"
SKILL_NAME = "relate-codemem-init"


def template(name):
    return resources.files("relate_codemem").joinpath("templates", name).read_text("utf-8")


def merge_json(path, mutate):
    data = {}
    if path.exists():
        data = json.loads(path.read_text("utf-8") or "{}")
    mutate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")


def embedding_overrides(provider=None, model=None, url=None, api_key_env=None, dimensions=None):
    """CLI options -> the fields stored in a config layer."""
    out = {}
    if provider:
        out["provider"] = config.normalise_provider(provider)
    for key, value in (("model", model), ("url", url), ("api_key_env", api_key_env),
                       ("dimensions", dimensions)):
        if value:
            out[key] = value
    return out


# ---------------------------------------------------------------- machine setup

def skill_dir():
    return Path.home() / ".claude" / "skills" / SKILL_NAME


def install_skill():
    dest = skill_dir()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(template("skill.md"), "utf-8")
    return dest


def setup(overrides, skill=True):
    """Record the machine-wide default embedding provider and install the global skill."""
    data = config.load_global()
    if overrides.get("provider"):
        data["embeddings"] = overrides
    elif overrides:
        data.setdefault("embeddings", {"provider": "local"}).update(overrides)
    else:
        data.setdefault("embeddings", {"provider": "local"})
    path = config.save_global(data)
    print(f"default embeddings: {data['embeddings']['provider']}  ({path})")
    if skill:
        print(f"Claude Code skill:  /{SKILL_NAME}  ({install_skill()})")
    print()
    return doctor(project=False)


# ---------------------------------------------------------------- project init

def init_project(target, overrides=None):
    target = Path(target).resolve()
    if not target.is_dir():
        raise store.RelateError(f"{target} is not a directory")
    if target == Path.home().resolve():
        raise store.RelateError("refusing to initialise the home directory; run this inside a project")
    config.reset(target)
    claude = target / ".claude"

    def settings(data):
        hooks = data.setdefault("hooks", {})
        for event, (matcher, timeout) in HOOKS.items():
            groups = hooks.setdefault(event, [])
            if any(HOOK_COMMAND == h.get("command") for g in groups for h in g.get("hooks", [])):
                continue
            group = {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": timeout}]}
            groups.append({"matcher": matcher, **group} if matcher else group)
        allow = data.setdefault("permissions", {}).setdefault("allow", [])
        allow += [p for p in PERMISSIONS if p not in allow]

    merge_json(claude / "settings.json", settings)
    merge_json(target / ".mcp.json", lambda d: d.setdefault("mcpServers", {}).update(
        {"relate-codemem": {"command": "relate-codemem", "args": ["mcp"]}}))

    cfg_path = claude / f"{config.APP}.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps(config.PROJECT_DEFAULTS, indent=2) + "\n", "utf-8")

    if overrides:                               # per-project embedding choice, not committed
        def local(data):
            env = data.setdefault("env", {})
            for key, var in (("provider", "EMBEDDINGS"), ("model", "EMBEDDING_MODEL"),
                             ("url", "EMBEDDING_URL"), ("api_key_env", "EMBEDDING_API_KEY_ENV"),
                             ("dimensions", "EMBEDDING_DIMENSIONS")):
                if overrides.get(key):
                    env[config.ENV + var] = str(overrides[key])
        merge_json(claude / "settings.local.json", local)

    section = f"{MARK_BEGIN}\n{template('claude-section.md').strip()}\n{MARK_END}\n"
    claude_md = target / "CLAUDE.md"
    text = claude_md.read_text("utf-8") if claude_md.exists() else "# CLAUDE.md\n\n"
    if MARK_BEGIN in text and MARK_END in text:
        text = text[:text.index(MARK_BEGIN)] + section + text[text.index(MARK_END) + len(MARK_END):].lstrip("\n")
    else:
        text = text.rstrip("\n") + "\n\n" + section
    claude_md.write_text(text, "utf-8")

    pcfg = config.project_config()
    for folder, name in ((pcfg["decisions_dirs"][0], "decision-template.md"),
                         (pcfg["meetings_dirs"][0], "meeting-template.md")):
        dest = target / folder
        dest.mkdir(parents=True, exist_ok=True)
        if not (dest / "README.md").exists():
            (dest / "README.md").write_text(template(name), "utf-8")

    ignore = target / ".gitignore"
    lines = ignore.read_text("utf-8").splitlines() if ignore.exists() else []
    if ".claude/relate/" not in lines:
        ignore.write_text("\n".join(lines + ["# relate-codemem store (local)", ".claude/relate/"]) + "\n", "utf-8")
    config.reset(target)
    ecfg = config.embedding_config()
    print(f"relate-codemem initialised in {target}")
    print(f"  hooks, permissions   .claude/settings.json")
    print(f"  MCP server           .mcp.json")
    print(f"  indexing options     .claude/{config.APP}.json")
    print(f"  instructions         CLAUDE.md")
    print(f"  embeddings           {ecfg['provider']}" + (f":{ecfg['model']}" if ecfg.get("model") else "")
          + f"  (from {ecfg['source']})")


# ---------------------------------------------------------------- doctor

def _check(ok, label, detail="", fix=""):
    print(f"  [{'ok' if ok else '!!'}] {label}" + (f": {detail}" if detail else ""))
    if not ok and fix:
        print(f"       -> {fix}")
    return bool(ok)


def doctor(project=True):
    """Report what is installed and what is missing. Returns 0 when everything needed works."""
    print(f"relate-codemem {__version__}  (python {sys.version.split()[0]}, {sys.platform})")
    good = True
    try:
        out = subprocess.run([store.relate_bin(), "--version"], capture_output=True, text=True, timeout=20,
                             **config.NO_WINDOW)
        good &= _check(out.returncode == 0, "relate", out.stdout.strip().splitlines()[0] if out.stdout else "")
    except (store.RelateError, OSError, subprocess.SubprocessError, IndexError):
        fix = ('powershell -ExecutionPolicy ByPass -c "irm https://relatedb.ai/install.ps1 | iex"'
               if os.name == "nt" else "curl -sSfL https://relatedb.ai/install.sh | bash")
        good &= _check(False, "relate", "not found", fix)
    good &= _check(shutil.which("relate-codemem"), "relate-codemem on PATH", shutil.which("relate-codemem") or "not found",
                   "Claude Code hooks call `relate-codemem hook`; run `uv tool update-shell` and restart the terminal")
    _check(shutil.which("git"), "git", "found" if shutil.which("git") else "not found (file listing falls back to a directory walk)")

    _check(True, "code parser", parse.describe())
    try:
        ecfg = config.embedding_config()
    except config.ConfigError as exc:
        _check(False, "embeddings", str(exc))
        return 1
    name = ecfg["provider"] + (f":{ecfg['model']}" if ecfg.get("model") else "")
    source = f"from {ecfg['source']}"
    if ecfg["provider"] == "none":
        _check(True, "embeddings", f"off, full-text search only ({source})")
    elif ecfg["provider"] == "local":
        try:
            import fastembed  # noqa: F401
            good &= _check(True, "embeddings", f"{name} ({source})")
        except ImportError:
            good &= _check(False, "embeddings", f"{name}: fastembed is not installed ({source})",
                           "reinstall with the local extra: uv tool install --force 'relate-codemem[local]'")
    elif ecfg["provider"] == "ollama":
        try:
            with urllib.request.urlopen(ecfg["url"].rstrip("/") + "/api/tags", timeout=5) as resp:
                models = [m.get("name", "") for m in json.loads(resp.read()).get("models", [])]
            has = any(m.split(":")[0] == ecfg["model"].split(":")[0] for m in models)
            good &= _check(has, "embeddings", f"{name} at {ecfg['url']} ({source})", f"ollama pull {ecfg['model']}")
        except (OSError, ValueError):
            good &= _check(False, "embeddings", f"{name}: no Ollama server at {ecfg['url']} ({source})",
                           "start Ollama (https://ollama.com), then: ollama pull " + ecfg["model"])
    else:
        key_env = ecfg.get("api_key_env") or ""
        good &= _check(not key_env or os.environ.get(key_env), "embeddings",
                       f"{name} at {ecfg['url']}, key in ${key_env} ({source})",
                       f"export {key_env}=... (the key is read from the environment, never stored)")
    _check((skill_dir() / "SKILL.md").exists(), "Claude Code skill", f"/{SKILL_NAME}",
           "relate-codemem setup")

    if project:
        root = config.project_root()
        try:
            settings = json.loads((root / ".claude" / "settings.json").read_text("utf-8"))
        except (OSError, ValueError):
            settings = {}
        hooked = [e for e in HOOKS if any(HOOK_COMMAND == h.get("command")
                  for g in settings.get("hooks", {}).get(e, []) for h in g.get("hooks", []))]
        print(f"project: {root}")
        _check(len(hooked) == len(HOOKS), "hooks", f"{len(hooked)}/{len(HOOKS)} registered",
               "relate-codemem init")
        _check((store.db() / "manifest.json").exists(), "store",
               str(store.db()), "relate-codemem index")
    return 0 if good else 1

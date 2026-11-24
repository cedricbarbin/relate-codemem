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
# Claude Code skills installed for the user, each usable as a slash command: name -> template
SKILLS = {SKILL_NAME: "skill.md", "relate-codemem-ask": "skill-ask.md", "relate-codemem-impact": "skill-impact.md",
          "relate-codemem-playground": "skill-playground.md", "relate-codemem-sharedb": "skill-sharedb.md"}


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


def install_skills():
    root = skill_dir().parent
    for name, source in SKILLS.items():
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "SKILL.md").write_text(template(source), "utf-8")
    return root


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
        print(f"Claude Code skills: {', '.join('/' + name for name in SKILLS)}  ({install_skills()})")
    print()
    return doctor(project=False)


# ---------------------------------------------------------------- sharing the store

LOCAL_STORE = ["# relate-codemem store (local)", ".claude/relate/"]
# Everything in the store directory stays out of git except the database and the files that
# describe it. The access token, logs, locks, spool and machine-specific state are never shared.
SHARED_STORE = ["# relate-codemem store, shared through git (relate-codemem sharedb)",
                ".claude/relate/*", "!.claude/relate/db/", "!.claude/relate/deps.json",
                "!.claude/relate/embeddings.json", "!.claude/relate/.schema-v*"]

SHARE_WARNING = """Sharing the memory store through git

The store in .claude/relate/db will be tracked by git, so that a commit and a push send it
to everyone who can read the repository. It contains more than code structure:

  - what was typed in Claude Code sessions (prompts), Claude's replies and the shell
    commands that were run, which can include names, internal URLs or secrets pasted by mistake;
  - file paths, symbol signatures and excerpts of documents, decisions and meeting notes,
    with the names of meeting participants;
  - all of it stays in the git history even after the store is changed or removed.

This is NOT recommended on a public repository: anyone could read those sessions.

Practical limits: the store is binary data that changes at every session, so the repository
grows and two people cannot merge their versions: one of them wins. Commit it while no
Claude Code session is writing to it."""


def share_store(off=False, yes=False):
    """Switch .gitignore between a local store and a store tracked by git."""
    root = config.project_root()
    ignore = root / ".gitignore"
    lines = ignore.read_text("utf-8").splitlines() if ignore.exists() else []
    kept = [line for line in lines if line not in LOCAL_STORE + SHARED_STORE]
    if off:
        ignore.write_text("\n".join(kept + LOCAL_STORE) + "\n", "utf-8")
        print("The store is local again: .gitignore ignores .claude/relate/.\n"
              "If it was already committed, it is still in the repository and its history. To stop tracking it:\n"
              "  git rm -r --cached .claude/relate\n"
              "Removing it from past commits needs a history rewrite.")
        return 0
    print(SHARE_WARNING + "\n")
    if not yes:
        if not sys.stdin or not sys.stdin.isatty():
            print("Not changed. Run it again with --yes once you have read the warning above.")
            return 1
        if input("Type 'yes' to let git track the store: ").strip().lower() != "yes":
            print("Not changed.")
            return 1
    ignore.write_text("\n".join(kept + SHARED_STORE) + "\n", "utf-8")
    try:                                    # fold pending writes into the store files before they are committed
        with store.write_lock():
            store.run(".checkpoint\n")
    except (store.RelateError, OSError, ValueError, subprocess.SubprocessError):
        pass
    print("Done: .gitignore now lets git track .claude/relate/db and its description files.\n"
          "Nothing was added or committed. When you are ready:\n"
          "  git add .gitignore .claude/relate\n"
          "  git status          # check what is about to be shared\n"
          "The access token of the playground, logs and local state stay ignored.\n"
          "To go back: relate-codemem sharedb --off")
    return 0


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
    if not any(line.startswith(".claude/relate/") for line in lines):      # local, or shared by sharedb
        ignore.write_text("\n".join(lines + [LOCAL_STORE[0], LOCAL_STORE[1]]) + "\n", "utf-8")
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
    missing = [name for name in SKILLS if not (skill_dir().parent / name / "SKILL.md").exists()]
    _check(not missing, "Claude Code skills", ", ".join("/" + name for name in SKILLS)
           + (f" (missing: {', '.join(missing)})" if missing else ""), "relate-codemem setup")

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

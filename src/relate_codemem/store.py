"""Access to the relate store: one directory store per project, driven through the
`relate` CLI. relate allows one writer process per store, so every write goes through
a file lock."""
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import config

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class RelateError(RuntimeError):
    pass


class Busy(RelateError):
    pass


def home():
    return config.store_home()


def db():
    return home() / "db"


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def log(msg):
    try:
        home().mkdir(parents=True, exist_ok=True)
        path = home() / "memory.log"
        if path.exists() and path.stat().st_size > 2_000_000:
            path.replace(path.with_name("memory.log.1"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- locks

def _try_lock(fh):
    try:
        if os.name == "nt":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh):
    try:
        if os.name == "nt":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:
        pass


class file_lock:
    """Exclusive cross-process lock on a file in the store directory.
    timeout=0 fails at once with Busy when another process holds it."""

    def __init__(self, name, timeout=45):
        self.name, self.timeout, self.fh = name, timeout, None

    def __enter__(self):
        home().mkdir(parents=True, exist_ok=True)
        self.fh = open(home() / self.name, "a+")
        deadline = time.time() + self.timeout
        while not _try_lock(self.fh):
            if time.time() >= deadline:
                self.fh.close()
                raise Busy(f"memory store is busy ({self.name})")
            time.sleep(0.1)
        return self

    def __exit__(self, *exc):
        _unlock(self.fh)
        self.fh.close()


def write_lock():
    return file_lock("write.lock", 45)


# ---------------------------------------------------------------- relate CLI

def relate_bin():
    found = shutil.which("relate")
    if found:
        return found
    candidates = [Path.home() / ".local" / "bin" / "relate"]
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        candidates = [Path(local) / "Programs" / "relate" / "relate.exe"]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    raise RelateError("relate binary not found; install it from https://relatedb.ai "
                      "(see `relate-codemem doctor`)")


def run(script, params=None, read_only=False, timeout=180):
    """Run a `;`-separated script; returns one list of row dicts per statement.
    Parameters are bound as the single map `$p`."""
    cmd = [relate_bin(), "query", "--database", str(db()), "--mode", "envelope", "--max-rows", "0"]
    if read_only:
        cmd.append("--read-only")
    tmp = None
    try:
        if params is not None:
            fd, tmp = tempfile.mkstemp(prefix="codemem-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(params, fh, ensure_ascii=False)
            cmd += ["--fetch-params", f"p={tmp}"]
        env = dict(os.environ, RELATE_AGENT_MODE="0")
        for attempt in range(6):
            proc = subprocess.run(cmd, input=script, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env, timeout=timeout)
            if proc.returncode != 5:            # 5 = store locked by another opener: retry
                break
            time.sleep(0.25 * (attempt + 1))
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    results, error = [], None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if "error" in obj:
            error = obj["error"]
        elif "rows" in obj:
            results.append(obj["rows"])
    if proc.returncode != 0 or error:
        msg = (error or {}).get("message") or proc.stderr.strip() or f"exit {proc.returncode}"
        raise RelateError(f"relate failed ({proc.returncode}): {msg}")
    return results


def read(statement, params=None):
    ensure_store()
    res = run(statement, params, read_only=True)
    return res[-1] if res else []


def read_many(script, params=None):
    ensure_store()
    return run(script, params, read_only=True)


def write(script, params=None):
    """Caller holds write_lock(). The script runs as one transaction."""
    return run(".begin\n" + script.strip().rstrip(";") + ";\n.commit\n", params)


SCHEMA = """
CREATE CONSTRAINT project_key IF NOT EXISTS FOR (n:Project) REQUIRE n.key IS UNIQUE;
CREATE CONSTRAINT file_path IF NOT EXISTS FOR (n:File) REQUIRE n.path IS UNIQUE;
CREATE CONSTRAINT file_revision_id IF NOT EXISTS FOR (n:FileRevision) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT symbol_id IF NOT EXISTS FOR (n:Symbol) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT module_name IF NOT EXISTS FOR (n:Module) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (n:Decision) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT note_id IF NOT EXISTS FOR (n:Note) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT meeting_id IF NOT EXISTS FOR (n:Meeting) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT person_name IF NOT EXISTS FOR (n:Person) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT session_id IF NOT EXISTS FOR (n:Session) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT prompt_id IF NOT EXISTS FOR (n:Prompt) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT reply_id IF NOT EXISTS FOR (n:Reply) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT tool_call_id IF NOT EXISTS FOR (n:ToolCall) REQUIRE n.id IS UNIQUE;
CREATE INDEX symbol_name IF NOT EXISTS FOR (n:Symbol) ON (n.name);
CREATE INDEX decision_slot IF NOT EXISTS FOR (n:Decision) ON (n.slot);
CREATE INDEX note_slot IF NOT EXISTS FOR (n:Note) ON (n.slot);
CREATE FULLTEXT INDEX memory_text IF NOT EXISTS FOR (n:Searchable) ON EACH [n.title, n.text, n.terms];
MERGE (p:Project {key: 'project'}) SET p.name = $p.name, p.root = $p.root, p.schemaVersion = $p.version
"""


def ensure_store():
    marker = home() / f".schema-v{config.SCHEMA_VERSION}"
    if marker.exists() and (db() / "manifest.json").exists():
        return
    with write_lock():
        if marker.exists() and (db() / "manifest.json").exists():
            return
        db().mkdir(parents=True, exist_ok=True)
        (home() / "spool").mkdir(parents=True, exist_ok=True)
        root = config.project_root()
        run(SCHEMA, {"name": root.name, "root": str(root), "version": config.SCHEMA_VERSION})
        marker.write_text(now_iso())
        log("store initialised")

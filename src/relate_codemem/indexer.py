"""Incremental indexing of the project tree into the graph, with content hashes and
file revisions."""
import json
import os
import subprocess
from pathlib import Path

from . import config, deps, parse, store
from .extract import (cfg, chunk_text, classify, excluded, extract_imports, extract_symbols,
                      parse_decision, parse_meeting, sha, split_terms, under)


def git(*args):
    try:
        out = subprocess.run(["git", "-C", str(config.project_root()), *args], capture_output=True, text=True,
                             **config.NO_WINDOW,
                             timeout=20)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def git_info():
    return {"commit": git("rev-parse", "--short", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD")}


def rel_path(path):
    """Project-relative POSIX path, or None when the path is outside the project."""
    try:
        p = Path(path)
        if not p.is_absolute():
            p = config.project_root() / p
        return p.resolve().relative_to(config.project_root().resolve()).as_posix()
    except (ValueError, OSError):
        return None


def list_files():
    out = git("ls-files", "-co", "--exclude-standard", "-z")
    if out is not None and (config.project_root() / ".git").exists():
        candidates = [p for p in out.split("\0") if p]
    else:
        candidates = []
        for root, dirs, files in os.walk(config.project_root()):
            dirs[:] = [d for d in dirs if d not in cfg()["exclude_dirs"]]
            for name in files:
                candidates.append((Path(root) / name).relative_to(config.project_root()).as_posix())
    return sorted(p for p in candidates if not excluded(p) and classify(p))


def read_text(path):
    full = config.project_root() / path
    try:
        if full.stat().st_size > cfg()["max_file_bytes"]:
            return None
        data = full.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    # Newlines are normalised so that a CRLF checkout hashes and parses like an LF one.
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


INDEX_SCRIPT = """
UNWIND $p.files AS r
MERGE (f:File {path: r.path})
SET f:Searchable, f.title = r.path, f.terms = r.terms, f.kind = r.kind, f.lang = r.lang,
    f.hash = r.hash, f.size = r.size, f.lines = r.lines, f.revision = r.rev,
    f.indexedAt = datetime($p.now), f.gitCommit = $p.commit, f.deleted = false
MERGE (v:FileRevision {id: r.path + '@' + toString(r.rev)})
  ON CREATE SET v.path = r.path, v.rev = r.rev, v.hash = r.hash, v.size = r.size, v.lines = r.lines,
                v.at = datetime($p.now), v.gitCommit = $p.commit, v.gitBranch = $p.branch,
                v.session = $p.session
MERGE (v)-[:REVISION_OF]->(f);

UNWIND $p.files AS r
MATCH (v:FileRevision {id: r.path + '@' + toString(r.rev)})
MATCH (o:FileRevision {id: r.path + '@' + toString(r.rev - 1)})
MERGE (v)-[:PREVIOUS]->(o);

UNWIND $p.sessions AS sid
MERGE (s:Session {id: sid}) ON CREATE SET s.startedAt = datetime($p.now)
WITH s
UNWIND $p.files AS r
MATCH (v:FileRevision {id: r.path + '@' + toString(r.rev)})
MERGE (s)-[:PRODUCED]->(v);

UNWIND $p.files AS r
MATCH (f:File {path: r.path})<-[:DEFINED_IN]-(s:Symbol) WHERE NOT s.id IN r.symbolIds
DETACH DELETE s;

UNWIND $p.files AS r
MATCH (f:File {path: r.path})<-[:PART_OF]-(c:Chunk) WHERE c.ord >= r.chunkCount
DETACH DELETE c;

UNWIND $p.files AS r
MATCH (f:File {path: r.path})-[i:IMPORTS|DEPENDS_ON]->()
DELETE i;

UNWIND $p.symbols AS s
MATCH (f:File {path: s.path})
MERGE (x:Symbol {id: s.id}) ON CREATE SET x.sinceRev = s.rev
WITH x, s, f, (x.hash IS NULL OR x.hash <> s.hash) AS changed
SET x:Searchable, x.name = s.name, x.qualname = s.qualname, x.kind = s.kind, x.path = s.path,
    x.startLine = s.startLine, x.endLine = s.endLine, x.signature = s.signature,
    x.title = s.title, x.text = s.text, x.terms = s.terms,
    x.changedRev = CASE WHEN changed THEN s.rev ELSE x.changedRev END, x.hash = s.hash,
    x.embedding = CASE WHEN changed THEN null ELSE x.embedding END
MERGE (x)-[:DEFINED_IN]->(f);

UNWIND $p.chunks AS c
MATCH (f:File {path: c.path})
MERGE (x:Chunk {id: c.id})
WITH x, c, f, (x.text IS NULL OR x.text <> c.text OR x.title <> c.title) AS changed
SET x:Searchable, x.path = c.path, x.ord = c.ord, x.kind = c.kind, x.title = c.title,
    x.text = c.text, x.terms = c.terms, x.startLine = c.startLine,
    x.embedding = CASE WHEN changed THEN null ELSE x.embedding END
MERGE (x)-[:PART_OF]->(f);

UNWIND $p.imports AS i
MATCH (f:File {path: i.path})
MERGE (m:Module {name: i.module})
MERGE (f)-[:IMPORTS]->(m);

UNWIND $p.adrs AS d
MATCH (f:File {path: d.path})
MERGE (x:Decision {id: d.id}) ON CREATE SET x.recordedAt = datetime($p.now)
WITH x, d, f, (x.text IS NULL OR x.text <> d.text) AS changed
SET x.embedding = CASE WHEN changed THEN null ELSE x.embedding END,
    x:Searchable, x.title = d.title, x.text = d.text, x.terms = d.terms, x.status = d.status,
    x.date = d.date, x.source = 'file', x.path = d.path, x.updatedAt = datetime($p.now),
    x.closedAt = CASE WHEN d.closed THEN coalesce(x.closedAt, datetime($p.now)) ELSE null END
MERGE (x)-[:DOCUMENTED_IN]->(f);

UNWIND $p.meetings AS m
MATCH (f:File {path: m.path})
MERGE (x:Meeting {id: m.path})
SET x:Searchable, x.title = m.title, x.date = m.date, x.text = m.summary, x.terms = m.terms,
    x.path = m.path
MERGE (x)-[:RECORDED_IN]->(f)
WITH x, m
UNWIND m.participants AS name
MERGE (p:Person {name: name})
MERGE (p)-[:ATTENDED]->(x);

UNWIND $p.deleted AS d
MATCH (f:File {path: d.path})
SET f.deleted = true, f.deletedAt = datetime($p.now), f.hash = null, f.revision = d.rev
MERGE (v:FileRevision {id: d.path + '@' + toString(d.rev)})
  ON CREATE SET v.path = d.path, v.rev = d.rev, v.deleted = true, v.at = datetime($p.now),
                v.gitCommit = $p.commit, v.gitBranch = $p.branch, v.session = $p.session
MERGE (v)-[:REVISION_OF]->(f)
WITH f, v, d
OPTIONAL MATCH (o:FileRevision {id: d.path + '@' + toString(d.rev - 1)})
FOREACH (x IN CASE WHEN o IS NULL THEN [] ELSE [o] END | MERGE (v)-[:PREVIOUS]->(x));

UNWIND $p.deleted AS d
MATCH (f:File {path: d.path})<-[:DEFINED_IN|PART_OF]-(n)
DETACH DELETE n;

UNWIND $p.deleted AS d
MATCH (f:File {path: d.path})-[r:DEPENDS_ON|IMPORTS]->()
DELETE r;

UNWIND $p.deleted AS d
MATCH (f:File {path: d.path})<-[:DOCUMENTED_IN]-(x:Decision)
SET x.closedAt = coalesce(x.closedAt, datetime($p.now)), x.status = 'deleted'
"""

DEPS_SCRIPT = """
UNWIND $p.paths AS path
MATCH (f:File {path: path})-[r:DEPENDS_ON]->()
DELETE r;

UNWIND $p.deps AS d
MATCH (f:File {path: d.path})
MATCH (t:File {path: d.target}) WHERE t.deleted = false
MERGE (f)-[r:DEPENDS_ON]->(t)
SET r.via = d.via
"""


CALLS_SCRIPT = """
UNWIND $p.paths AS path
MATCH (:File {path: path})<-[:DEFINED_IN]-(:Symbol)-[r:CALLS]->()
DELETE r;

UNWIND $p.calls AS c
MATCH (a:Symbol {id: c.caller})
MATCH (b:Symbol {id: c.callee})
MERGE (a)-[r:CALLS]->(b)
SET r.via = c.via, r.distance = c.distance, r.candidates = c.candidates
"""


def deps_path():
    return store.home() / "deps.json"


def state_path():
    return store.home() / "state.json"


def load_state():
    try:
        return json.loads(state_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state), "utf-8")
    tmp.replace(state_path())


def index(paths=None, full=False, session=None, quiet=False):
    """Incremental index. A file whose sha256 differs from the stored one gets a new
    revision (File.revision + 1, a FileRevision node chained by PREVIOUS); unchanged
    files cost a stat() only. `paths` limits the run; without it, deletions are detected."""
    store.ensure_store()
    with store.write_lock():
        known = {r["path"]: r for r in store.run(
            "MATCH (f:File) WHERE f.revision IS NOT NULL "
            "RETURN f.path AS path, f.hash AS hash, f.revision AS rev", read_only=True)[-1]}
        loaded = None if full else deps.load(deps_path())
        if loaded is None and not full and known:
            # a store indexed before dependency facts existed (or with another format)
            full, paths = True, None
            if not quiet:
                print("dependency facts are missing, outdated or built with another parser: "
                      "analysing every file again")
        facts, old_edges, old_calls = loaded or ({}, {}, {})
        state = {} if full else load_state()
        if paths:
            targets = []
            for raw in paths:
                rp = rel_path(raw)
                if rp and (config.project_root() / rp).is_dir():
                    targets += [p for p in list_files() if under(p, [rp])]
                elif rp:
                    targets.append(rp)
            all_files = None
        else:
            targets = all_files = list_files()
        changed, deleted, new_state = [], [], dict(state)
        for path in dict.fromkeys(targets):
            full_path = config.project_root() / path
            kind = classify(path)
            if not full_path.is_file() or excluded(path) or not kind:
                if path in known and known[path]["hash"] is not None:
                    deleted.append({"path": path, "rev": known[path]["rev"] + 1})
                    new_state.pop(path, None)
                continue
            st = full_path.stat()
            cached = state.get(path)
            prev = known.get(path)
            if (cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size
                    and prev and prev["hash"] == cached[2]):
                continue
            text = read_text(path)
            if text is None:
                continue
            digest = sha(text)
            new_state[path] = [st.st_mtime_ns, st.st_size, digest]
            if prev and prev["hash"] == digest and not full:
                continue
            rev = prev["rev"] + (0 if prev["hash"] == digest else 1) if prev else 1
            changed.append((path, kind, text, digest, rev, st.st_size))
        if all_files is not None:
            present = set(all_files)
            for path, row in known.items():
                if path not in present and row["hash"] is not None:
                    deleted.append({"path": path, "rev": row["rev"] + 1})
                    new_state.pop(path, None)

        info = git_info()
        base = {"now": store.now_iso(), "commit": info["commit"], "branch": info["branch"],
                "session": session, "sessions": [session] if session else []}
        counts = {"symbols": 0, "chunks": 0}
        for start in range(0, max(len(changed), 1), 150):
            batch = changed[start:start + 150]
            p = dict(base, files=[], symbols=[], chunks=[], imports=[], adrs=[], meetings=[],
                     deleted=deleted if start == 0 else [])
            for path, (kind, lang), text, digest, rev, size in batch:
                symbols, chunks = [], []
                facts.pop(path, None)
                if kind == "code":
                    symbols = extract_symbols(path, text, lang, rev)
                    imports = extract_imports(path, text, lang)
                    for module in dict.fromkeys(module for module, _ in imports if module):
                        p["imports"].append({"path": path, "module": module[:200]})
                    facts[path] = deps.facts(path, text, lang, symbols, imports)
                    sites = parse.calls(path, text, lang)
                    if sites is not None:
                        facts[path].update(deps.call_facts(path, text, lang, symbols, sites))
                elif kind in ("doc", "decision", "meeting"):
                    meta, chunks = chunk_text(path, text, kind)
                    if kind == "decision":
                        p["adrs"].append(parse_decision(path, text, meta))
                    elif kind == "meeting":
                        p["meetings"].append(parse_meeting(path, text, meta))
                p["files"].append({"path": path, "kind": kind, "lang": lang, "hash": digest,
                                   "size": size, "lines": text.count("\n") + 1, "rev": rev,
                                   "symbolIds": [s["id"] for s in symbols],
                                   "chunkCount": len(chunks), "terms": split_terms(path)})
                p["symbols"] += symbols
                p["chunks"] += chunks
            if p["files"] or p["deleted"]:
                store.write(INDEX_SCRIPT, p)
                if not quiet and len(changed) > 150:
                    print(f"  {min(start + 150, len(changed))}/{len(changed)} files", flush=True)
                counts["symbols"] += len(p["symbols"])
                counts["chunks"] += len(p["chunks"])
        if changed or deleted:
            # Dependencies are resolved over the facts of every file, so that an unchanged
            # file gains or loses an edge when a name it uses appears or disappears elsewhere.
            gone = {d["path"] for d in deleted}
            for path in gone:
                facts.pop(path, None)
            touched = {c[0] for c in changed}
            live = ({path for path, row in known.items() if row["hash"] is not None} | touched) - gone
            edges = deps.resolve(facts, live)
            rewrite = sorted(path for path, found in edges.items()
                             if path in touched or found != old_edges.get(path, {}))
            for start in range(0, len(rewrite), 400):
                part = rewrite[start:start + 400]
                store.write(DEPS_SCRIPT, {"paths": part, "deps": [
                    {"path": path, "target": target, "via": via}
                    for path in part for target, via in sorted(edges[path].items())]})
            # Calls are resolved after dependencies: a call only reaches what its file can see.
            calls = deps.resolve_calls(facts, edges, live)
            # every re-analysed file is rewritten, so that a full run also clears calls it no longer finds
            rewrite = sorted(path for path in set(calls) | set(old_calls) | touched
                             if path in live and (path in touched or calls.get(path) != old_calls.get(path)))
            for start in range(0, len(rewrite), 200):
                part = rewrite[start:start + 200]
                store.write(CALLS_SCRIPT, {"paths": part, "calls": [
                    {"caller": a, "callee": b, "via": via, "distance": distance, "candidates": candidates}
                    for path in part for a, b, via, distance, candidates in calls.get(path, ())]})
            deps.save(deps_path(), facts, edges, calls)
        save_state(new_state)
    summary = (f"indexed {len(changed)} changed file(s), {len(deleted)} deleted, "
               f"{counts['symbols']} symbols, {counts['chunks']} chunks")
    if changed or deleted:
        store.log(summary + (f" [session {session}]" if session else ""))
    if not quiet:
        print(summary)
    return len(changed), len(deleted)

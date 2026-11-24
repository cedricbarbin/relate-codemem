"""Reads (search, recall, file and symbol views, status) and the explicit memory writes
(decisions and notes)."""
import re
import subprocess

from . import config, embeddings, store
from .events import spool_dir
from .extract import slugify, split_terms
from .indexer import rel_path


def current_session():
    try:
        return (store.home() / "current_session").read_text().strip() or None
    except OSError:
        return None


REMEMBER_SCRIPT = """
MERGE (x:{label} {{id: $p.id}})
  ON CREATE SET x:Searchable, x.title = $p.title, x.text = $p.text, x.terms = $p.terms,
                x.rationale = $p.rationale, x.slot = $p.slot, x.status = $p.status,
                x.date = $p.date, x.source = 'session', x.recordedAt = datetime($p.now);

UNWIND $p.sessions AS sid
MATCH (x:{label} {{id: $p.id}})
MERGE (s:Session {{id: sid}}) ON CREATE SET s.startedAt = datetime($p.now)
MERGE (x)-[:MADE_IN]->(s);

UNWIND $p.files AS path
MATCH (x:{label} {{id: $p.id}})
MERGE (f:File {{path: path}})
MERGE (x)-[:AFFECTS]->(f);

MATCH (new:{label} {{id: $p.id}})
MATCH (old:{label})
WHERE old <> new AND old.closedAt IS NULL
  AND (old.id IN $p.supersedes OR (new.slot IS NOT NULL AND old.slot = new.slot))
SET old.closedAt = new.recordedAt, old.status = 'superseded'
MERGE (new)-[:SUPERSEDES]->(old)
RETURN old.id AS replaced, old.title AS title
"""


def remember(label, title, text, rationale=None, slot=None, files=None, supersedes=None):
    store.ensure_store()
    now = store.now_iso()
    ident = f"{label.lower()}:{now[:19].replace(':', '').replace('-', '')}-{slugify(title)}"
    session = current_session()
    paths = [p for p in (rel_path(f.strip()) for f in (files or "").split(",") if f.strip()) if p]
    full = text if not rationale else f"{text}\n\nRationale: {rationale}"
    p = {"id": ident, "title": title[:200], "text": full, "rationale": rationale, "slot": slot,
         "status": "accepted" if label == "Decision" else "current", "date": now[:10], "now": now,
         "terms": split_terms(title, slot or ""), "sessions": [session] if session else [],
         "files": paths, "supersedes": [s.strip() for s in (supersedes or "").split(",") if s.strip()]}
    with store.write_lock():
        replaced = store.write(REMEMBER_SCRIPT.format(label=label), p)[-1]
    print(f"stored {ident}")
    for r in replaced:
        print(f"  supersedes {r['replaced']} ({r['title']})")



KINDS = {"code": ("Symbol", "File"), "doc": ("Chunk",), "decision": ("Decision", "Chunk"),
         "meeting": ("Meeting", "Chunk"), "session": ("Prompt", "Reply"), "note": ("Note",)}


def one_line(text, limit=220):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


STOPWORDS = set("""the and for are was were with that this from have has had not but what which who whom how why when
where does did can could should would will into about over than then them they their there here our your you its
les des une est sont avec pour dans sur par que qui quoi quel quelle quels quelles comment pourquoi quand
nous vous ils elles leur leurs mon ton son mes tes ses notre votre ces cet cette aux pas plus fait faire avoir
être etre été ont avons utilise utilisent use used using""".split())

SEARCH_COLS = ("RETURN labels(n) AS labels, n.id AS id, n.path AS path, n.title AS title, n.kind AS kind, "
               "substring(n.text, 0, 400) AS text, n.startLine AS line, n.status AS status, n.date AS date, "
               "toString(n.at) AS at, n.closedAt IS NOT NULL AS closed, n.deleted AS deleted, score "
               "ORDER BY score DESC")


def search(query, k=10, kind=None, include_closed=False, semantic=True):
    """Hybrid recall: a full-text list and a vector list, fused by reciprocal rank."""
    words = re.sub(r"[^\w]+", " ", query, flags=re.UNICODE).lower().split()
    words = [w for w in dict.fromkeys(words + split_terms(query).split())
             if w not in STOPWORDS and (len(w) > 2 or not w.isalpha())]
    q = " ".join(words)
    if not q and not semantic:
        print("empty query")
        return
    fetch = k * 8 + 20
    lists = {"text": store.read("MATCH (n:Searchable) SEARCH n IN (FULLTEXT INDEX memory_text FOR $p.q "
                          "LIMIT $p.fetch) SCORE AS score " + SEARCH_COLS, {"q": q, "fetch": fetch}) if q else []}
    note = None
    ecfg = config.embedding_config()
    marker = embeddings.read_marker()
    if semantic and ecfg["provider"] != "none" and embeddings.marker_matches(ecfg, marker):
        try:
            rows = store.read(f"MATCH (n:Searchable) SEARCH n IN (VECTOR INDEX {embeddings.VECTOR_INDEX} FOR "
                              f"vector($p.v, {int(marker['dimensions'])}, FLOAT32) LIMIT $p.fetch) SCORE AS score "
                              + SEARCH_COLS, {"v": embeddings.embed_texts([query], ecfg)[0], "fetch": fetch})
            lists["vec"] = [r for r in rows if r["score"] >= ecfg["min_similarity"]]
        except (store.RelateError, subprocess.SubprocessError, OSError, ValueError) as exc:
            note = f"semantic search unavailable ({one_line(str(exc), 160)}); full-text only"
    elif semantic and ecfg["provider"] != "none":
        note = "full-text only (no vectors for the current embedding model: run `relate-codemem embed`)"

    def keep(r, label):
        if kind and (label not in KINDS[kind] or (label == "Chunk" and r["kind"] != kind)
                     or (label == "File" and r["kind"] not in ("code", "config"))):
            return False
        return include_closed or not (r["closed"] or r["deleted"])

    fused = {}
    for source, rows in lists.items():
        rank = 0
        for r in rows:
            label = next((l for l in r["labels"] if l != "Searchable"), "?")
            if not keep(r, label):
                continue
            rank += 1
            hit = fused.setdefault((label, r["id"] or r["path"]), {"row": r, "label": label, "rrf": 0.0, "by": []})
            hit["rrf"] += 1.0 / (60 + rank)
            hit["by"].append(source)
    hits = sorted(fused.values(), key=lambda h: -h["rrf"])[:k]
    if note:
        print(f"({note})")
    for h in hits:
        r, label = h["row"], h["label"]
        where = r["path"] or r["id"] or ""
        if r["line"] and r["path"]:
            where = f"{r['path']}:{r['line']}"
        extra = " ".join(x for x in (r["status"], r["date"], (r["at"] or "")[:16],
                                     "CLOSED" if r["closed"] else "") if x)
        print(f"[{'+'.join(h['by'])}] {label}  {where}  {('(' + extra + ')') if extra else ''}")
        if label != "File":
            print(f"       {one_line(r['title'], 100)}")
            if r["text"]:
                print(f"       {one_line(r['text'], 160 if label == 'Symbol' else 220)}")
            if label in ("Decision", "Note") and r["id"]:
                print(f"       id: {r['id']}")
    if not hits:
        print("no match (full-text is word-based, no stemming: try other word forms)")


def recall():
    res = store.read_many(
        "MATCH (d:Decision) WHERE d.closedAt IS NULL "
        "RETURN d.id AS id, d.title AS title, d.status AS status, d.date AS date, d.path AS path, d.slot AS slot "
        "ORDER BY coalesce(d.date, '') DESC LIMIT 20;"
        "MATCH (n:Note) WHERE n.closedAt IS NULL "
        "RETURN n.id AS id, n.title AS title, n.text AS text, n.slot AS slot, n.date AS date "
        "ORDER BY n.recordedAt DESC LIMIT 20;"
        "MATCH (m:Meeting) RETURN m.title AS title, m.date AS date, m.path AS path, "
        "COLLECT { MATCH (p:Person)-[:ATTENDED]->(m) RETURN p.name ORDER BY p.name } AS people "
        "ORDER BY coalesce(m.date, '') DESC LIMIT 8;"
        "MATCH (s:Session) WITH s ORDER BY s.startedAt DESC LIMIT 6 "
        "RETURN s.id AS id, toString(s.startedAt) AS at, s.branch AS branch, "
        "COLLECT { MATCH (q:Prompt)-[:IN_SESSION]->(s) RETURN q.title ORDER BY q.at LIMIT 6 } AS prompts, "
        "COLLECT { MATCH (s)-[:PRODUCED]->(v:FileRevision) RETURN DISTINCT v.path ORDER BY v.path LIMIT 12 } AS files;"
        "MATCH (v:FileRevision) RETURN v.path AS path, v.rev AS rev, toString(v.at) AS at, "
        "v.deleted AS deleted, v.gitCommit AS commit ORDER BY v.at DESC, v.path LIMIT 15")
    decisions, notes, meetings, sessions, revisions = res
    print(f"# Open decisions ({len(decisions)})")
    for d in decisions:
        print(f"- {d['date'] or '?'} [{d['status']}] {d['title']}  <{d['path'] or d['id']}>"
              + (f" slot={d['slot']}" if d["slot"] else ""))
    print(f"\n# Notes ({len(notes)})")
    for n in notes:
        print(f"- {n['date']} {one_line(n['text'], 200)}  <{n['id']}>" + (f" slot={n['slot']}" if n["slot"] else ""))
    print(f"\n# Meetings ({len(meetings)})")
    for m in meetings:
        print(f"- {m['date'] or '?'} {m['title']}  <{m['path']}>"
              + (f" with {', '.join(m['people'])}" if m["people"] else ""))
    print(f"\n# Recent sessions ({len(sessions)})")
    for s in sessions:
        print(f"- {(s['at'] or '')[:16]} {s['id'][:8]}" + (f" on {s['branch']}" if s["branch"] else ""))
        for q in s["prompts"]:
            print(f"    > {one_line(q, 110)}")
        if s["files"]:
            print(f"    files: {', '.join(s['files'])}")
    print(f"\n# Latest file revisions")
    for v in revisions:
        print(f"- {(v['at'] or '')[:16]} {v['path']} r{v['rev']}" + (" DELETED" if v["deleted"] else "")
              + (f" @{v['commit']}" if v["commit"] else ""))


def show_file(path):
    rp = rel_path(path) or path
    res = store.read_many(
        "MATCH (f:File {path: $p.path}) RETURN f.kind AS kind, f.lang AS lang, f.hash AS hash, "
        "f.revision AS rev, f.lines AS lines, toString(f.indexedAt) AS at, f.deleted AS deleted;"
        "MATCH (v:FileRevision)-[:REVISION_OF]->(:File {path: $p.path}) "
        "RETURN v.rev AS rev, v.hash AS hash, toString(v.at) AS at, v.gitCommit AS commit, "
        "v.session AS session, v.deleted AS deleted ORDER BY v.rev DESC LIMIT 12;"
        "MATCH (s:Symbol)-[:DEFINED_IN]->(:File {path: $p.path}) "
        "RETURN s.kind AS kind, s.qualname AS name, s.startLine AS line, s.endLine AS endLine, "
        "s.sinceRev AS since, s.changedRev AS changed ORDER BY s.startLine LIMIT 200;"
        "MATCH (f:File {path: $p.path}) RETURN "
        "COLLECT { MATCH (f)-[r:DEPENDS_ON]->(t:File) WHERE t.deleted = false "
        "RETURN t.path + CASE WHEN coalesce(r.via, 'import') = 'import' THEN '' ELSE ' (' + r.via + ')' END "
        "ORDER BY t.path } AS uses, "
        "COLLECT { MATCH (o:File)-[:DEPENDS_ON]->(f) WHERE o.deleted = false RETURN o.path ORDER BY o.path } AS usedBy, "
        "COLLECT { MATCH (f)-[:IMPORTS]->(m:Module) RETURN m.name ORDER BY m.name LIMIT 40 } AS imports;"
        "MATCH (d)-[:AFFECTS|DOCUMENTED_IN]->(:File {path: $p.path}) WHERE d:Decision OR d:Note "
        "RETURN labels(d) AS labels, d.id AS id, d.title AS title, d.status AS status, d.date AS date "
        "ORDER BY coalesce(d.date, '') DESC LIMIT 20;"
        "MATCH (t:ToolCall)-[:MODIFIED]->(:File {path: $p.path}) MATCH (t)-[:IN_SESSION]->(s:Session) "
        "WITH s, count(t) AS edits, max(t.at) AS last ORDER BY last DESC LIMIT 8 "
        "RETURN s.id AS id, edits, toString(last) AS last, "
        "COLLECT { MATCH (q:Prompt)-[:IN_SESSION]->(s) RETURN q.title ORDER BY q.at LIMIT 3 } AS prompts",
        {"path": rp})
    info, revs, symbols, links, decisions, sessions = res
    if not info:
        print(f"{rp}: not in the memory graph (run `relate-codemem index {rp}`)")
        return
    f = info[0]
    print(f"{rp}  kind={f['kind']} lang={f['lang']} revision={f['rev']} lines={f['lines']} "
          f"hash={(f['hash'] or '-')[:12]} indexed={(f['at'] or '')[:16]}" + (" DELETED" if f["deleted"] else ""))
    print("\nRevisions:")
    for v in revs:
        print(f"  r{v['rev']} {(v['at'] or '')[:16]} {(v['hash'] or 'deleted')[:12]}"
              + (f" @{v['commit']}" if v["commit"] else "") + (f" session {v['session'][:8]}" if v["session"] else ""))
    if symbols:
        print("\nSymbols:")
        for s in symbols:
            print(f"  {s['line']}-{s['endLine']} {s['kind']} {s['name']} (since r{s['since']}, changed r{s['changed']})")
    if links:
        l = links[0]
        for name, key in (("Depends on", "uses"), ("Used by", "usedBy"), ("Imports", "imports")):
            if l[key]:
                print(f"\n{name}: {', '.join(l[key])}")
    if decisions:
        print("\nDecisions / notes:")
        for d in decisions:
            print(f"  {d['date'] or '?'} [{d['status']}] {d['title']}  <{d['id']}>")
    if sessions:
        print("\nSessions that modified it:")
        for s in sessions:
            print(f"  {(s['last'] or '')[:16]} {s['id'][:8]} ({s['edits']} edit(s))")
            for q in s["prompts"]:
                print(f"      > {one_line(q, 100)}")


def show_symbol(name):
    link = ("t.qualname + ' <' + t.path + '>' + ' (' + r.via + "
            "CASE WHEN r.candidates > 1 THEN ', 1 of ' + toString(r.candidates) ELSE '' END + ')'")
    rows = store.read("MATCH (s:Symbol) WHERE s.name = $p.name OR s.qualname = $p.name "
                "RETURN s.kind AS kind, s.qualname AS name, s.path AS path, s.startLine AS line, "
                "s.signature AS signature, s.changedRev AS changed, "
                "COLLECT { MATCH (s)-[r:CALLS]->(t:Symbol) RETURN " + link + " ORDER BY t.qualname LIMIT 40 } AS calls, "
                "COLLECT { MATCH (t:Symbol)-[r:CALLS]->(s) RETURN " + link + " ORDER BY t.qualname LIMIT 40 } AS callers "
                "ORDER BY s.path, s.startLine LIMIT 50",
                {"name": name})
    if not rows:
        return search(name, kind="code")
    for s in rows:
        print(f"{s['path']}:{s['line']}  {s['kind']} {s['name']}  (changed at r{s['changed']})\n    {s['signature']}")
        for label, key in (("calls", "calls"), ("called by", "callers")):
            for line in s[key]:
                print(f"    {label}: {line}")


def status():
    store.ensure_store()
    rows = store.read("MATCH (n) UNWIND labels(n) AS label WITH label, count(*) AS n WHERE label <> 'Searchable' "
                "RETURN label, n ORDER BY label")
    pending = sum(1 for f in spool_dir().glob("*.jsonl") for _ in f.open(encoding="utf-8"))
    print(f"store: {store.db()}")
    print(f"project: {config.project_root()}")
    for r in rows:
        print(f"  {r['label']:<14}{r['n']}")
    print(f"spooled events not yet flushed: {pending}")
    ecfg = config.embedding_config()
    if ecfg["provider"] == "none":
        print("embeddings: off (full-text search only)")
    else:
        e = store.read("MATCH (n:Searchable) WHERE n.text IS NOT NULL AND NOT n:File AND NOT n:Meeting "
                       "RETURN count(n) AS total, count(n.embedding) AS done")[0]
        ready = embeddings.marker_matches(ecfg)
        print(f"embeddings: {e['done'] if ready else 0}/{e['total']} items, {ecfg['provider']}:{ecfg['model']} "
              f"(from {ecfg['source']})" + ("" if ready else " - run `relate-codemem embed`"))


MODEL_DOC = """Graph model (labels, key property, relationships)
  File {path}            kind code|doc|decision|meeting|config, lang, hash (sha256), revision, lines, deleted
  FileRevision {id}      id = path@rev; rev, hash, at, gitCommit, gitBranch, session, deleted
  Symbol {id}            id = path::qualname; name, kind, signature, startLine, endLine, hash, sinceRev, changedRev
  Chunk {id}             id = path#ord; title (heading trail), text, startLine, kind doc|decision|meeting
  Module {name}          import target that is not a project file
  Decision {id}          title, text, status, date, slot, source file|session, recordedAt, closedAt
  Note {id}              title, text, slot, date, recordedAt, closedAt
  Meeting {id}           id = path; title, date     Person {name}
  Session {id}           startedAt, lastAt, endedAt, branch, gitCommit, source
  Prompt {id} / Reply {id}   title, text, at, session        ToolCall {id}  tool, summary, path, at
  (FileRevision)-[:REVISION_OF]->(File)     (FileRevision)-[:PREVIOUS]->(FileRevision)
  (Symbol)-[:DEFINED_IN]->(File)            (Chunk)-[:PART_OF]->(File)
  (File)-[:DEPENDS_ON {via}]->(File)        (File)-[:IMPORTS]->(Module)
  (Symbol)-[:CALLS {via, distance, candidates}]->(Symbol)   needs the tree-sitter extra
  CALLS.via = receiver | self | new | scope | name (by name only); candidates > 1 = ambiguous
  DEPENDS_ON.via = import | qualified | wildcard | package (same package, no import)
  (Decision)-[:DOCUMENTED_IN]->(File)       (Decision|Note)-[:AFFECTS]->(File)
  (Decision|Note)-[:SUPERSEDES]->(same)     (Decision|Note)-[:MADE_IN]->(Session)
  (Meeting)-[:RECORDED_IN]->(File)          (Person)-[:ATTENDED]->(Meeting)
  (Prompt|Reply|ToolCall)-[:IN_SESSION]->(Session)   (Reply)-[:ANSWERS]->(Prompt)
  (ToolCall)-[:MODIFIED]->(File)            (Session)-[:READ {count}]->(File)
  (Session)-[:PRODUCED]->(FileRevision)
Full-text index `memory_text` over :Searchable (title, text, terms): File, Symbol, Chunk,
Decision, Note, Meeting, Prompt, Reply. Current = closedAt IS NULL."""

"""Standard questions about the code graph, shared by the command line and the MCP server.

Each function takes a symbol name (a simple name or a qualified one such as
`Invoice.total`), runs read-only Cypher and returns text. They need the links that the
`parse` extra produces (CALLS, EXTENDS, OVERRIDES)."""
import re

from . import indexer, store

TYPE_KINDS = ["class", "interface", "enum", "struct", "record", "object", "trait", "type", "protocol",
              "annotation", "module"]


def _find(name):
    return store.read(
        "MATCH (s:Symbol) WHERE s.qualname = $p.name OR s.name = $p.name "
        "RETURN s.id AS id, s.kind AS kind, s.qualname AS name, s.path AS path, s.startLine AS line "
        "ORDER BY s.path, s.startLine LIMIT 25", {"name": name})


def _missing(name):
    return (f"No symbol named `{name}` in the code graph. Try `relate-codemem search {name} --kind code`, "
            "or run `relate-codemem index` if the file is new.")


def _head(s):
    return f"{s['kind']} {s['name']}  <{s['path']}:{s['line']}>"


def _note(row):
    extra = [row["via"]] if row.get("via") else []
    if row.get("inherited"):
        extra.append("inherited")
    if (row.get("candidates") or 1) > 1:
        extra.append(f"1 of {row['candidates']}")
    return f" ({', '.join(extra)})" if extra else ""


def calls_of(name):
    """What a class (through all its methods) or a single method calls, inside and outside the project."""
    found = _find(name)
    if not found:
        return _missing(name)
    out = []
    for s in found:
        inside, outside = store.read_many(
            "MATCH (s:Symbol {id: $p.id}) "
            "MATCH (m:Symbol) WHERE m.id = s.id OR (m.path = s.path AND m.qualname STARTS WITH s.qualname + '.') "
            "MATCH (m)-[r:CALLS]->(t:Symbol) WHERE NOT (t.path = s.path AND t.qualname STARTS WITH s.qualname + '.') "
            "AND t.id <> s.id "
            "RETURN m.qualname AS caller, t.qualname AS callee, t.kind AS kind, t.path AS path, r.via AS via, "
            "r.inherited AS inherited, r.candidates AS candidates ORDER BY callee, caller LIMIT 400;"
            "MATCH (s:Symbol {id: $p.id}) "
            "MATCH (m:Symbol) WHERE m.id = s.id OR (m.path = s.path AND m.qualname STARTS WITH s.qualname + '.') "
            "MATCH (m)-[:CALLS]->(e:External) "
            "RETURN m.qualname AS caller, e.name AS callee, e.scope AS scope, e.root AS root "
            "ORDER BY callee, caller LIMIT 400", {"id": s["id"]})
        out.append(_head(s))
        if not inside and not outside:
            out.append("  no call recorded")
        grouped = {}
        for r in inside:
            grouped.setdefault((r["callee"], r["path"], r["kind"]), []).append(r)
        if grouped:
            out.append(f"  calls inside the project ({len(grouped)}):")
            for (callee, path, kind), rows in grouped.items():
                callers = ", ".join(sorted({r["caller"] for r in rows}))
                out.append(f"    {kind} {callee}  <{path}>{_note(rows[0])}   from {callers}")
        grouped = {}
        for r in outside:
            grouped.setdefault((r["callee"], r["scope"]), []).append(r["caller"])
        if grouped:
            out.append(f"  calls outside the project ({len(grouped)}):")
            for (callee, scope), callers in grouped.items():
                out.append(f"    {callee}  ({scope})   from {', '.join(sorted(set(callers)))}")
    return "\n".join(out)


def implementations(name):
    """The types that extend or implement a type, or the methods that redefine a method, at any depth."""
    found = _find(name)
    if not found:
        return _missing(name)
    out = []
    for s in found:
        is_type = s["kind"] in TYPE_KINDS
        rel = "EXTENDS" if is_type else "OVERRIDES"
        rows = store.read(
            f"MATCH p = (t:Symbol)-[:{rel}*1..12]->(s:Symbol {{id: $p.id}}) "
            "RETURN t.kind AS kind, t.qualname AS name, t.path AS path, t.startLine AS line, min(length(p)) AS depth "
            "ORDER BY depth, name LIMIT 300", {"id": s["id"]})
        out.append(_head(s))
        if not rows:
            out.append("  no implementation or subtype in the project" if is_type else "  not redefined in the project")
        for r in rows:
            out.append(f"  {'  ' * (r['depth'] - 1)}{r['kind']} {r['name']}  <{r['path']}:{r['line']}>"
                       + ("" if r["depth"] == 1 else f"  (level {r['depth']})"))
    return "\n".join(out)


def impact(name, depth=3):
    """What may be affected by a change to a method or a class: the methods that call it, directly
    or through other methods, up to `depth` calls away, with their classes and files.

    A call made on a supertype can run a redefinition, so the callers of the methods that the
    target redefines are included; for a class, so are its subtypes."""
    depth = max(1, min(int(depth), 6))
    found = _find(name)
    if not found:
        return _missing(name)
    out = []
    for s in found:
        targets, callers, subtypes = store.read_many(
            "MATCH (s:Symbol {id: $p.id}) "
            "MATCH (m:Symbol) WHERE m.id = s.id OR (m.path = s.path AND m.qualname STARTS WITH s.qualname + '.') "
            "OPTIONAL MATCH (m)-[:OVERRIDES*1..12]->(up:Symbol) "
            "WITH collect(DISTINCT m.id) + collect(DISTINCT up.id) AS ids, "
            "     [x IN collect(DISTINCT up.qualname) WHERE x IS NOT NULL] AS above "
            "RETURN ids, above;"
            "MATCH (s:Symbol {id: $p.id}) "
            "MATCH (m:Symbol) WHERE m.id = s.id OR (m.path = s.path AND m.qualname STARTS WITH s.qualname + '.') "
            "OPTIONAL MATCH (m)-[:OVERRIDES*1..12]->(up:Symbol) "
            "WITH s, collect(DISTINCT m) + collect(DISTINCT up) AS targets "
            "UNWIND targets AS t "
            f"MATCH p = (c:Symbol)-[:CALLS*1..{depth}]->(t) "
            "WHERE NOT (c.path = s.path AND (c.id = s.id OR c.qualname STARTS WITH s.qualname + '.')) "
            "RETURN c.kind AS kind, c.qualname AS name, c.path AS path, c.startLine AS line, min(length(p)) AS distance "
            "ORDER BY distance, path, name LIMIT 500;"
            "MATCH p = (t:Symbol)-[:EXTENDS*1..12]->(s:Symbol {id: $p.id}) "
            "RETURN t.kind AS kind, t.qualname AS name, t.path AS path, min(length(p)) AS depth ORDER BY depth, name LIMIT 200",
            {"id": s["id"]})
        out.append(_head(s))
        above = targets[0]["above"] if targets else []
        if above:
            out.append(f"  also reached through calls on: {', '.join(sorted(above))}")
        if not callers and not subtypes:
            out.append(f"  no caller recorded within {depth} call(s)")
        for distance in sorted({c["distance"] for c in callers}):
            rows = [c for c in callers if c["distance"] == distance]
            out.append(f"  {'direct callers' if distance == 1 else f'callers {distance} calls away'} ({len(rows)}):")
            for c in rows:
                out.append(f"    {c['kind']} {c['name']}  <{c['path']}:{c['line']}>")
        if subtypes:
            out.append(f"  subtypes ({len(subtypes)}): " + ", ".join(f"{t['name']} <{t['path']}>" for t in subtypes))
        if callers:
            classes = sorted({c["name"].rsplit(".", 1)[0] for c in callers if "." in c["name"]})
            files = sorted({c["path"] for c in callers})
            if classes:
                out.append(f"  classes affected ({len(classes)}): {', '.join(classes)}")
            out.append(f"  files affected ({len(files)}): {', '.join(files)}")
    out.append("")
    out.append("Links found by name only can be wrong, and calls whose receiver has no written type are missing: "
               "treat this as a lower bound and check the callers before a risky change.")
    return "\n".join(out)


# ---------------------------------------------------------------- more questions

TEST_FILE = re.compile(
    r"(^|/)(tests?|__tests__|specs?|testing)/|(^|/)test_[^/]+\.py$|_test\.(py|go|rb|exs?)$|_spec\.rb$|"
    r"(Tests?|IT|Spec)\.(java|kt|scala|cs|swift|php)$|\.(test|spec)\.[cm]?[jt]sx?$")


def is_test(path):
    return bool(TEST_FILE.search(path))


def _expand(symbols):
    """Ids of the given symbols and, for a type, of its members."""
    rows = store.read(
        "UNWIND $p.ids AS id MATCH (s:Symbol {id: id}) "
        "MATCH (m:Symbol) WHERE m.id = s.id OR (m.path = s.path AND m.qualname STARTS WITH s.qualname + '.') "
        "RETURN DISTINCT m.id AS id LIMIT 5000", {"ids": [s["id"] for s in symbols]})
    return [r["id"] for r in rows]


def _callers(ids, depth):
    """Callers of the given symbols, and of what they redefine, up to `depth` calls away."""
    depth = max(1, min(int(depth), 6))
    return store.read(
        "MATCH (m:Symbol) WHERE m.id IN $p.ids "
        "OPTIONAL MATCH (m)-[:OVERRIDES*1..12]->(up:Symbol) "
        "WITH collect(DISTINCT m) + collect(DISTINCT up) AS targets UNWIND targets AS t "
        f"MATCH p = (c:Symbol)-[:CALLS*1..{depth}]->(t) WHERE NOT c.id IN $p.ids "
        "RETURN c.id AS id, c.kind AS kind, c.qualname AS name, c.path AS path, c.startLine AS line, "
        "min(length(p)) AS distance ORDER BY distance, path, name LIMIT 1000", {"ids": ids})


def call_path(source, target, limit=10):
    """The shortest chain of calls from one symbol to another. A call made on a supertype
    may run a redefinition, so a step can also go from a method to one that redefines it."""
    starts, ends = _find(source), _find(target)
    if not starts:
        return _missing(source)
    if not ends:
        return _missing(target)
    start_ids, end_ids = set(_expand(starts)), set(_expand(ends))
    calls, overrides, names = store.read_many(
        "MATCH (a:Symbol)-[:CALLS]->(b:Symbol) RETURN a.id AS a, b.id AS b LIMIT 400000;"
        "MATCH (a:Symbol)-[:OVERRIDES]->(b:Symbol) RETURN a.id AS a, b.id AS b LIMIT 400000;"
        "MATCH (s:Symbol) RETURN s.id AS id, s.kind AS kind, s.qualname AS name, s.path AS path, "
        "s.startLine AS line LIMIT 400000")
    steps = {}
    for r in calls:
        steps.setdefault(r["a"], []).append((r["b"], "calls"))
    for r in overrides:                       # the declaration -> what may run instead
        steps.setdefault(r["b"], []).append((r["a"], "may run"))
    info = {r["id"]: r for r in names}
    came, frontier, found = {i: None for i in start_ids}, list(start_ids), None
    for _ in range(max(1, min(int(limit), 30))):
        nxt = []
        for node in frontier:
            for other, how in steps.get(node, ()):
                if other not in came:
                    came[other] = (node, how)
                    nxt.append(other)
                    if other in end_ids:
                        found = other
                        break
            if found:
                break
        if found or not nxt:
            break
        frontier = nxt
    if found is None:
        direct = start_ids & end_ids
        return ("They are the same symbol or one contains the other." if direct else
                f"No chain of recorded calls leads from `{source}` to `{target}` within {limit} steps. "
                "A call whose receiver has no written type is not in the graph, so this does not prove there is none.")
    chain, node = [], found
    while node is not None:
        chain.append((node, came[node][1] if came[node] else ""))
        node = came[node][0] if came[node] else None
    chain.reverse()
    out = [f"{len(chain) - 1} step(s) from {source} to {target}:"]
    for index, (node, how) in enumerate(chain):
        s = info.get(node, {"kind": "?", "name": node, "path": "?", "line": 0})
        out.append(f"  {'   ' * index}{(how + ' ') if how else ''}{s['kind']} {s['name']}  <{s['path']}:{s['line']}>")
    return "\n".join(out)


def library_usage(name=None):
    """Without a name: the libraries the project uses. With one (`numpy`, `org.springframework`,
    or a module of it): the files that import it, and what is called or extended from it."""
    if not name:
        libraries, calls = store.read_many(
            "MATCH (f:File)-[r:IMPORTS]->(m:Module) WHERE r.scope IN ['external', 'stdlib'] AND f.deleted = false "
            "RETURN m.root AS library, r.scope AS scope, count(DISTINCT f) AS files ORDER BY scope, files DESC, library LIMIT 300;"
            "MATCH (:Symbol)-[:CALLS]->(e:External) RETURN e.root AS library, count(*) AS calls LIMIT 2000")
        if not libraries:
            return "No import leading out of the project is recorded (run `relate-codemem index`)."
        counts = {r["library"]: r["calls"] for r in calls}
        out = []
        for scope, title in (("external", "External libraries"), ("stdlib", "Standard library")):
            rows = [r for r in libraries if r["scope"] == scope]
            if rows:
                out.append(f"{title} ({len(rows)}):")
                out += [f"  {r['library']}  imported by {r['files']} file(s), {counts.get(r['library'], 0)} call(s) recorded"
                        for r in rows]
        return "\n".join(out)
    files, members, parents = store.read_many(
        "MATCH (f:File)-[r:IMPORTS]->(m:Module) WHERE (m.root = $p.name OR m.name = $p.name OR m.name STARTS WITH $p.name + '.' "
        "OR m.name STARTS WITH $p.name + '/' OR m.name STARTS WITH $p.name + '::') AND r.scope <> 'internal' AND f.deleted = false "
        "RETURN f.path AS path, collect(DISTINCT m.name) AS modules, r.scope AS scope ORDER BY path LIMIT 500;"
        "MATCH (s:Symbol)-[:CALLS]->(e:External) WHERE e.root = $p.name OR e.module = $p.name OR e.module STARTS WITH $p.name + '.' "
        "RETURN e.name AS member, collect(DISTINCT s.qualname + ' <' + s.path + '>') AS callers ORDER BY member LIMIT 500;"
        "MATCH (s:Symbol)-[:EXTENDS]->(e:External) WHERE e.root = $p.name OR e.module = $p.name OR e.module STARTS WITH $p.name + '.' "
        "RETURN e.name AS parent, collect(DISTINCT s.qualname + ' <' + s.path + '>') AS types ORDER BY parent LIMIT 200",
        {"name": name})
    if not files and not members and not parents:
        return (f"Nothing recorded for a library named `{name}`. Run `relate-codemem uses` to list the libraries; "
                "the name is the root of the import (`numpy`, `org.springframework`, `@nestjs/common`).")
    out = [f"{name}: imported by {len(files)} file(s)"]
    out += [f"  {f['path']}  ({', '.join(f['modules'])})" for f in files]
    if members:
        out.append(f"members called ({len(members)}):")
        out += [f"  {m['member']}   from {', '.join(m['callers'][:8])}" + (" ..." if len(m["callers"]) > 8 else "")
                for m in members]
    if parents:
        out.append(f"types extended ({len(parents)}):")
        out += [f"  {p['parent']}   by {', '.join(p['types'])}" for p in parents]
    out.append("")
    out.append("Only calls whose receiver is tied to an import are recorded: files that import the library "
               "without a recorded call may still use it.")
    return "\n".join(out)


def why(path):
    """Why a file is the way it is: the decisions and notes that affect it, the documents that
    mention it and the sessions that changed it, with what was asked in them."""
    rp = indexer.rel_path(path) or path
    stem = rp.rsplit("/", 1)[-1]
    info, linked, mentions, sessions = store.read_many(
        "MATCH (f:File {path: $p.path}) RETURN f.revision AS rev, f.deleted AS deleted, toString(f.indexedAt) AS at;"
        "MATCH (d)-[:AFFECTS|DOCUMENTED_IN]->(:File {path: $p.path}) WHERE d:Decision OR d:Note "
        "RETURN labels(d) AS labels, d.title AS title, d.text AS text, d.rationale AS rationale, d.status AS status, "
        "d.date AS date, d.slot AS slot, d.closedAt IS NULL AS current ORDER BY current DESC, coalesce(d.date, '') DESC LIMIT 20;"
        "MATCH (c:Chunk) WHERE c.kind IN ['decision', 'meeting', 'doc'] AND c.path <> $p.path "
        "AND (c.text CONTAINS $p.path OR c.text CONTAINS $p.stem) "
        "RETURN c.kind AS kind, c.path AS path, c.title AS title, c.startLine AS line ORDER BY kind, path LIMIT 15;"
        "MATCH (t:ToolCall)-[:MODIFIED]->(:File {path: $p.path}) MATCH (t)-[:IN_SESSION]->(s:Session) "
        "WITH s, count(t) AS edits, max(t.at) AS last ORDER BY last DESC LIMIT 6 "
        "RETURN s.id AS id, edits, toString(last) AS last, "
        "COLLECT { MATCH (q:Prompt)-[:IN_SESSION]->(s) RETURN q.title ORDER BY q.at LIMIT 4 } AS prompts",
        {"path": rp, "stem": stem})
    if not info:
        return f"{rp}: not in the memory graph (run `relate-codemem index {rp}`)."
    out = [f"{rp}  revision {info[0]['rev']}" + (" DELETED" if info[0]["deleted"] else "")]
    if linked:
        out.append("Decisions and notes about it:")
        for d in linked:
            label = "note" if "Note" in d["labels"] else "decision"
            out.append(f"  [{label}, {d['status'] or 'open'}{'' if d['current'] else ', closed'}] {d['date'] or '?'}  {d['title']}")
            out.append(f"      {(d['text'] or '').strip()[:500]}")
            if d["rationale"] and d["rationale"].strip() not in (d["text"] or ""):
                out.append(f"      why: {d['rationale'].strip()[:400]}")
    if mentions:
        out.append("Documents that mention it:")
        out += [f"  [{m['kind']}] {m['path']}:{m['line']}  {m['title']}" for m in mentions]
    if sessions:
        out.append("Sessions that changed it:")
        for s in sessions:
            out.append(f"  {(s['last'] or '')[:16]}  {s['edits']} edit(s), session {s['id'][:8]}")
            out += [f"      asked: {p}" for p in s["prompts"] if p]
    if not (linked or mentions or sessions):
        out.append("No decision, note, document or recorded session explains this file. "
                   "`git log --follow` on it is the next place to look.")
    return "\n".join(out)


def tests_for(name, depth=4):
    """The tests that reach a method or a class through recorded calls, nearest first."""
    found = _find(name)
    if not found:
        return _missing(name)
    ids = _expand(found)
    callers = [c for c in _callers(ids, depth) if is_test(c["path"])]
    out = [_head(s) for s in found]
    if callers:
        out.append(f"tests that reach it ({len(callers)}), by number of calls in between:")
        out += [f"  {c['distance']}  {c['kind']} {c['name']}  <{c['path']}:{c['line']}>" for c in callers]
        files = sorted({c["path"] for c in callers})
        out.append(f"test files ({len(files)}): {', '.join(files)}")
    else:
        near = store.read(
            "UNWIND $p.paths AS path MATCH (t:File)-[:DEPENDS_ON]->(:File {path: path}) WHERE t.deleted = false "
            "RETURN DISTINCT t.path AS path ORDER BY path LIMIT 200", {"paths": sorted({s["path"] for s in found})})
        near = [r["path"] for r in near if is_test(r["path"])]
        out.append(f"no test reaches it through recorded calls within {depth} call(s).")
        if near:
            out.append(f"test files that depend on its file, and may cover it: {', '.join(near)}")
    out.append("")
    out.append("A test file is recognised by its name or directory. A test that reaches the symbol through "
               "a fixture, a framework or a call without a written type is not seen.")
    return "\n".join(out)


def _changed_lines(ref):
    """{path: set of changed line numbers, or None for a whole new file} between `ref` and the working tree."""
    if not re.fullmatch(r"[\w./~^@{}-]+", ref) or ref.startswith("-"):
        raise ValueError(f"not a git revision: {ref}")
    diff = indexer.git("diff", "-U0", "--no-color", "--no-ext-diff", ref)
    if diff is None:
        raise ValueError(f"git could not diff against `{ref}` (is this a git repository, does the revision exist?)")
    changed, path = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            if path:
                changed.setdefault(path, set())
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))?", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                changed[path].update(range(start, start + max(count, 1)))
    for new in (indexer.git("ls-files", "-o", "--exclude-standard") or "").splitlines():
        changed[new] = None
    return changed


def diff_impact(ref="HEAD", depth=3):
    """What the uncommitted changes (or the changes since `ref`) may affect: the symbols whose
    lines changed, their callers, and the tests that reach them."""
    try:
        changed = _changed_lines(ref)
    except ValueError as exc:
        return str(exc)
    if not changed:
        return f"No difference between `{ref}` and the working tree."
    rows = store.read(
        "UNWIND $p.paths AS path MATCH (s:Symbol)-[:DEFINED_IN]->(:File {path: path}) "
        "RETURN s.id AS id, s.kind AS kind, s.qualname AS name, s.path AS path, s.startLine AS line, "
        "s.endLine AS endLine ORDER BY path, line LIMIT 5000", {"paths": sorted(changed)})
    touched = []
    for s in rows:
        lines = changed[s["path"]]
        if lines is None or any(s["line"] <= n <= (s["endLine"] or s["line"]) for n in lines):
            touched.append(s)
    # a class is touched whenever one of its members is: keep the innermost symbols
    inner = [s for s in touched if not any(o is not s and o["path"] == s["path"]
                                           and o["name"].startswith(s["name"] + ".") for o in touched)]
    out = [f"Changes since {ref}: {len(changed)} file(s), {len(inner)} symbol(s) touched"]
    without = sorted(p for p in changed if not any(s["path"] == p for s in rows))
    for s in inner:
        out.append(f"  {s['kind']} {s['name']}  <{s['path']}:{s['line']}>")
    if without:
        out.append(f"  files with no indexed symbol: {', '.join(without)}")
    if not inner:
        return "\n".join(out)
    callers = _callers([s["id"] for s in inner], depth)
    code = [c for c in callers if not is_test(c["path"])]
    tests = [c for c in callers if is_test(c["path"])]
    for title, group in (("callers", code), ("tests that reach the changes", tests)):
        if group:
            out.append(f"{title} ({len(group)}), by number of calls in between:")
            out += [f"  {c['distance']}  {c['kind']} {c['name']}  <{c['path']}:{c['line']}>" for c in group[:120]]
            if len(group) > 120:
                out.append(f"  ... and {len(group) - 120} more")
    if not code:
        out.append("no caller recorded outside the changed symbols.")
    if not tests:
        out.append("no test reaches the changed symbols through recorded calls.")
    files = sorted({c["path"] for c in code} - set(changed))
    if files:
        out.append(f"other files affected ({len(files)}): {', '.join(files)}")
    out.append("")
    out.append("Symbols are located with the line numbers of the last index: run `relate-codemem index` first if "
               "files were edited outside Claude Code. The list of callers is a lower bound.")
    return "\n".join(out)

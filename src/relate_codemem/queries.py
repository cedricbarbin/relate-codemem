"""Standard questions about the code graph, shared by the command line and the MCP server.

Each function takes a symbol name (a simple name or a qualified one such as
`Invoice.total`), runs read-only Cypher and returns text. They need the links that the
`parse` extra produces (CALLS, EXTENDS, OVERRIDES)."""
from . import store

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

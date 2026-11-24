"""File-to-file dependencies, resolved from declared symbols. No store access.

`facts()` reduces a source file to what resolution needs: its package, the names it
declares, its imports and the names it uses. `resolve()` turns the facts of every
file into dependency edges. Facts are kept on disk, so an unchanged file is never
read again, yet it gains or loses an edge when another file starts or stops
declaring a name it uses.

The analysis is lexical: a name that appears in the code of a file links it to the
file that declares that name in a visible package. There is no type inference.
"""
import json
import posixpath
import re

from . import parse

VERSION = 2

# origin of an edge, strongest first
VIA = ("import", "qualified", "wildcard", "package")

JVM = {"java", "kotlin", "scala"}
PACKAGE_LANGS = JVM | {"csharp", "php", "go"}
TYPE_KINDS = {"class", "interface", "enum", "struct", "record", "object", "trait", "type",
              "protocol", "annotation"}
# languages whose top-level functions are visible package-wide or importable by name
FUNCTION_EXPORTS = {"kotlin", "scala", "go", "php"}

_PACKAGE_RX = {
    "java": re.compile(r"^\s*package\s+([\w.]+)", re.M),
    "kotlin": re.compile(r"^\s*package\s+([\w.]+)", re.M),
    "scala": re.compile(r"^\s*package\s+([\w.]+)", re.M),
    "csharp": re.compile(r"^\s*namespace\s+([\w.]+)", re.M),
    "php": re.compile(r"^\s*namespace\s+([\w\\]+)\s*[;{]", re.M),
}
_NOT_CODE = re.compile(
    r'"""[\s\S]*?"""|/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`[^`]*`')
_HASH_COMMENT = re.compile(r"#[^\n]*")
_IDENT = re.compile(r"[A-Za-z_]\w*")
_CAPITALISED = re.compile(r"\b[A-Z]\w*")
_CALLED = re.compile(r"\b([a-z_]\w*)\s*\(")
_DOTTED = re.compile(r"\b[a-z_]\w*(?:\.[a-z_]\w*)*(?:\.[A-Z]\w*)+")
_BACKSLASHED = re.compile(r"\\?[A-Za-z_]\w*(?:\\[A-Za-z_]\w*)+")
_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "when", "match", "function", "fn",
             "func", "fun", "def", "new", "throw", "else", "do", "try", "synchronized", "using",
             "foreach", "elseif", "isset", "empty", "array", "list", "echo", "print", "super", "this"}
MAX_NAMES = 3000


def family(lang):
    return "jvm" if lang in JVM else lang


def code_only(text, lang):
    """The source with comments and string literals blanked out."""
    text = _NOT_CODE.sub(" ", text)
    return _HASH_COMMENT.sub(" ", text) if lang == "php" else text


def facts(path, text, lang, symbols, imports):
    """What resolution needs to know about one code file.

    `symbols` are the extracted symbols of the file, `imports` the (module, candidate
    paths) pairs from extract_imports."""
    out = {"lang": lang, "imports": [[m, c] for m, c in imports if m][:400]}
    if lang not in PACKAGE_LANGS:
        return out
    if lang == "go":
        out["package"] = "go:" + posixpath.dirname(path)
    else:
        m = _PACKAGE_RX[lang].search(text)
        name = m.group(1).replace("\\", ".").strip(".") if m else "~" + posixpath.dirname(path)
        out["package"] = f"{family(lang)}:{name}"
    lines = text.splitlines()

    def top_level_function(s):
        # a member function is reached through its type, not by its bare name
        if s["kind"] != "function" or lang not in FUNCTION_EXPORTS or s["name"] in ("main", "init"):
            return False
        if s.get("top") is not None:        # known exactly when the file went through a parser
            return s["top"]
        line = lines[s["startLine"] - 1] if 0 < s["startLine"] <= len(lines) else " "
        return line[:1] not in (" ", "\t") and not line.startswith("func (")

    exports = {s["name"] for s in symbols if s["kind"] in TYPE_KINDS or top_level_function(s)}
    out["exports"] = sorted(exports)[:MAX_NAMES]
    code = parse.code_only(path, text, lang) or code_only(text, lang)
    if lang == "go":
        used = set(_IDENT.findall(code))
    else:
        used = set(_CAPITALISED.findall(code)) | (set(_CALLED.findall(code)) - _KEYWORDS)
    out["uses"] = sorted(used - exports)[:MAX_NAMES]
    if lang == "php":
        chains = {c.replace("\\", ".").strip(".") for c in _BACKSLASHED.findall(code)}
    else:
        chains = set(_DOTTED.findall(code))
    out["qualified"] = sorted(chains)[:400]
    return out


_VAR_TYPES = (
    re.compile(r"\b([A-Z]\w*)(?:<[^<>;]*>)?(?:\[\])?[*&]?\s+[*&]?([a-z_]\w*)\s*(?=[=;,)])"),     # Type name
    re.compile(r"\b([a-z_]\w*)\s*:\s*(?:&\s*)?(?:mut\s+)?\*?([A-Z]\w*)"),                       # name: Type
    re.compile(r"\b([a-z_]\w*)\s*(?::=|=)\s*(?:new\s+)?&?([A-Z]\w*)\s*(?:<[^<>;]*>)?\s*[({]"),   # name = Type(
    re.compile(r"\b([a-z_]\w*)\s+\*?([A-Z]\w*)\s*(?=[,){])"),                                   # Go: name Type
    re.compile(r"\b([a-z_]\w*)\s*=\s*([A-Z]\w*)\.new\b"),                                      # Ruby
)


def _variable_types(code):
    """{variable: Type} read off declarations, file-wide; a name declared with two types is dropped."""
    found, clash = {}, set()
    for index, rx in enumerate(_VAR_TYPES):
        for a, b in rx.findall(code):
            name, kind = (b, a) if index == 0 else (a, b)
            if found.setdefault(name, kind) != kind:
                clash.add(name)
    return {name: kind for name, kind in found.items() if name not in clash}


def _arity(signature, lang, kind):
    """Number of parameters in a signature line, or -1 when it cannot be told."""
    text = signature
    if lang == "go" and kind == "method":           # func (d *DB) Close(): skip the receiver
        text = text[text.find(")") + 1:]
    start = text.find("(")
    if start < 0:
        return -1
    depth, count, seen = 0, 0, False
    for ch in text[start:]:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
            if depth == 0:
                break
        elif depth == 1:
            if ch == ",":
                count += 1
            elif not ch.isspace():
                seen = True
    else:
        return -1                                   # the parameter list continues on another line
    params = text[start + 1:text.find(")", start) if depth == 0 and "(" not in text[start + 1:] else None]
    if re.search(r"\.\.\.|=|\*|\bvararg\b|\bparams\b|\?:", params or text[start:]):
        return -1                                   # optional or variable parameters
    count = count + 1 if seen else 0
    if re.match(r"\s*(?:&?\s*(?:mut\s+)?self|self|cls)\b", text[start + 1:]):
        count -= 1
    return count


def call_facts(path, text, lang, symbols, sites):
    """Adds what call resolution needs to the facts of a file: its definitions, the
    supertypes written in its type signatures, and its call sites grouped by caller.

    `sites` comes from parse.calls."""
    out = {"defs": [], "supers": {}, "calls": {}}
    callables = []
    for s in symbols:
        owner = s["qualname"].rsplit(".", 1)[0] if "." in s["qualname"] else ""
        callable_ = s["kind"] in ("function", "method")
        out["defs"].append([s["id"], s["name"], owner, s["kind"],
                            _arity(s["signature"], lang, s["kind"]) if callable_ else -1])
        if callable_:
            callables.append(s)
        elif s["kind"] in TYPE_KINDS:
            after = s["signature"].split(s["name"], 1)[-1]
            supers = [n for n in dict.fromkeys(_CAPITALISED.findall(after)) if n != s["name"]]
            if supers:
                out["supers"][s["name"]] = supers[:8]
    if not callables or not sites:
        return out
    types = _variable_types(parse.code_only(path, text, lang) or code_only(text, lang))
    for line, name, kind, receiver, count in sites:
        inside = [s for s in callables if s["startLine"] <= line <= s["endLine"]]
        if not inside:
            continue
        caller = max(inside, key=lambda s: s["startLine"])["id"]
        target = ""
        if kind == "name":
            if receiver[:1].isupper():
                kind, target = "type", receiver
            elif receiver in types:
                kind, target = "type", types[receiver]
            else:
                kind, target = "unknown", receiver      # perhaps a module or a package: kept for matching
        elif kind == "expr":
            kind = "unknown"
        entry = [name, kind, target, count]
        seen = out["calls"].setdefault(caller, [])
        if entry not in seen and len(seen) < 300:
            seen.append(entry)
    return out


MAX_CANDIDATES = 3
# how a callee was chosen, most certain first
CALL_VIA = ("receiver", "self", "new", "scope", "name")


def resolve_calls(files, edges, live):
    """-> {path: [[caller id, callee id, via, distance, candidates], ...]}.

    A call can only reach a definition that is visible from its file: in the file itself
    (distance 0), in a file it depends on (1), or in a file those depend on (2). That is
    what tells homonyms apart. `via` says how the callee was chosen: "receiver" (the type
    of the receiver is written in the file), "self" (same class or a supertype), "new"
    (construction), "scope" (a function in scope) or "name" (by name only, among visible
    files). A call that still has more than MAX_CANDIDATES homonyms is left out."""
    by_file = {}           # path -> {"types": {name: [id]}, "methods": {(owner, name): [(id, arity)]},
    for path, f in files.items():   #         "functions": {name: [(id, arity)]}, "any": {name: [(id, arity)]}}
        if path not in live or "defs" not in f:
            continue
        index = by_file[path] = {"types": {}, "methods": {}, "functions": {}, "any": {}}
        for sid, name, owner, kind, arity in f["defs"]:
            if kind in TYPE_KINDS:
                index["types"].setdefault(name, []).append(sid)
            elif kind in ("function", "method"):
                index["any"].setdefault(name, []).append((sid, arity, kind == "method" or bool(owner)))
                if kind == "method" or owner:
                    index["methods"].setdefault((owner.rsplit(".", 1)[-1], name), []).append((sid, arity))
                if kind == "function":
                    index["functions"].setdefault(name, []).append((sid, arity))

    def rings(path):
        near = [t for t in edges.get(path, ()) if t in by_file]
        far = {u for t in near for u in edges.get(t, ()) if u in by_file} - set(near) - {path}
        return [[path] if path in by_file else [], near, sorted(far)]

    def types_named(name, around):
        """[(distance, path)] of the nearest files declaring a type of that name."""
        for distance, ring in enumerate(around):
            hits = [(distance, p) for p in ring if name in by_file[p]["types"]]
            if hits:
                return hits
        return []

    def methods_of(path, type_name, name, depth=0, seen=None):
        """Methods `name` of a type declared in `path`, looking up its supertypes when absent."""
        seen = seen or set()
        if (path, type_name) in seen or depth > 3:
            return []
        seen.add((path, type_name))
        found = by_file[path]["methods"].get((type_name, name), [])
        if found:
            return found
        out = []
        for parent in files[path].get("supers", {}).get(type_name, ()):
            for _, where in types_named(parent, rings(path)[:2]):
                out += methods_of(where, parent, name, depth + 1, seen)
        return out

    result = {}
    for path, f in files.items():
        if path not in by_file or not f.get("calls"):
            continue
        around = rings(path)
        distance_of = {p: d for d, ring in enumerate(around) for p in ring}
        owners = {sid: owner.rsplit(".", 1)[-1] for sid, _, owner, _, _ in f["defs"]}
        rows = {}
        for caller, sites in f["calls"].items():
            for name, kind, target, count in sites:
                hits, via = [], ""
                if kind == "type":
                    for distance, where in types_named(target, around):
                        hits += [(h, distance) for h in methods_of(where, target, name)]
                    via = "receiver"
                elif kind == "new":
                    hits = [((sid, -1), d) for d, where in types_named(name, around) for sid in by_file[where]["types"][name]]
                    via = "new"
                elif kind in ("none", "super"):
                    own = owners.get(caller, "")
                    if own and kind == "none":
                        hits = [(h, 0) for h in methods_of(path, own, name)]
                    elif own:
                        for parent in f.get("supers", {}).get(own, ()):
                            for distance, where in types_named(parent, around[:2]):
                                hits += [(h, distance) for h in methods_of(where, parent, name)]
                    via = "self"
                    if not hits and kind == "none":
                        for distance, ring in enumerate(around):
                            hits = [(h, distance) for p in ring for h in by_file[p]["functions"].get(name, ())]
                            if hits:
                                break
                        via = "scope"
                    if not hits and kind == "none":         # Type(...) without `new`
                        hits = [((sid, -1), d) for d, where in types_named(name, around)
                                for sid in by_file[where]["types"][name]]
                        via = "new"
                if not hits and (kind == "unknown" or (kind == "type" and not types_named(target, around))):
                    # By name only. A call with a receiver reaches methods; it reaches a plain
                    # function only when the receiver names its file or directory (a module, a package).
                    for ring_set in (around[0] + around[1], around[2]):
                        hits = [((sid, arity), distance_of[p]) for p in ring_set
                                for sid, arity, member in by_file[p]["any"].get(name, ())
                                if member or (target and target in (posixpath.splitext(posixpath.basename(p))[0],
                                                                    posixpath.basename(posixpath.dirname(p))))]
                        if hits:
                            break
                    via = "name"
                exact = [h for h in hits if h[0][1] == count]
                hits = exact or [h for h in hits if h[0][1] < 0] or (hits if via != "name" else [])
                targets = {h[0][0]: h[1] for h in hits if h[0][0] != caller}
                if not targets or len(targets) > MAX_CANDIDATES:
                    continue
                for callee, distance in targets.items():
                    known = rows.get((caller, callee))
                    if known is None or CALL_VIA.index(via) < CALL_VIA.index(known[0]):
                        rows[(caller, callee)] = [via, distance, len(targets)]
        if rows:
            result[path] = sorted([a, b, *rest] for (a, b), rest in rows.items())
    return result


def _nearest(paths, origin):
    """Among files declaring the same name, the one sharing the longest directory prefix."""
    if len(paths) == 1:
        return paths[0]
    here = origin.split("/")

    def shared(candidate):
        n = 0
        for a, b in zip(here, candidate.split("/")):
            if a != b:
                break
            n += 1
        return n

    return max(sorted(paths), key=shared)


def resolve(files, live):
    """files: {path: facts}, live: paths that exist. -> {path: {target: via}}."""
    packages = {}   # package key -> {name: [paths]}
    go_dirs = {}    # directory -> package key
    for path, f in files.items():
        key = f.get("package")
        if not key or path not in live:
            continue
        names = packages.setdefault(key, {})
        for name in f.get("exports", ()):
            names.setdefault(name, []).append(path)
        if key.startswith("go:"):
            go_dirs[key[3:]] = key

    edges = {}
    for path, f in files.items():
        if path not in live:
            continue
        found = {}

        def link(target, via):
            if target != path and target in live and (
                    target not in found or VIA.index(via) < VIA.index(found[target])):
                found[target] = via

        def link_name(key, name, via):
            paths = packages.get(key, {}).get(name)
            if paths:
                link(_nearest(paths, path), via)
            return bool(paths)

        def link_qualified(fam, dotted, via):
            """`a.b.C`, `a.b.C.Inner` or `a.b.C.method`: the longest prefix that is a declared name."""
            parts = dotted.split(".")
            for cut in range(len(parts) - 1, 0, -1):
                if link_name(f"{fam}:{'.'.join(parts[:cut])}", parts[cut], via):
                    return True
            return False

        def link_used(key, via):
            names = packages.get(key)
            if names:
                for name in f.get("uses", ()):
                    if name in names:
                        link(_nearest(names[name], path), via)

        lang, fam, key = f["lang"], family(f["lang"]), f.get("package")
        for module, candidates in f.get("imports", ()):
            hit = False
            if lang == "go":
                target_dir = max((d for d in go_dirs if module == d or module.endswith("/" + d)),
                                 key=len, default=None)
                if target_dir is not None and go_dirs[target_dir] != key:
                    link_used(go_dirs[target_dir], "import")
                    hit = True
            elif key:
                dotted = module.replace("\\", ".").strip(".")
                wildcard = module.endswith(".") or dotted.endswith("._")
                dotted = dotted[:-2] if dotted.endswith("._") else dotted
                if wildcard or lang == "csharp":
                    # `import a.b.*`, `using A.B;`: every used name that the package declares
                    if f"{fam}:{dotted}" in packages:
                        link_used(f"{fam}:{dotted}", "wildcard")
                        hit = True
                    else:
                        hit = link_qualified(fam, dotted, "import")
                else:
                    hit = link_qualified(fam, dotted, "import")
            if not hit:
                for candidate in candidates:
                    candidate = posixpath.normpath(candidate)
                    if candidate in live and candidate != path:
                        link(candidate, "import")
                        break
        if key:
            for dotted in f.get("qualified", ()):
                link_qualified(fam, dotted, "qualified")
            link_used(key, "package")
            if lang == "csharp":      # a namespace sees the types of its parents
                parent = key
                while "." in parent:
                    parent = parent.rsplit(".", 1)[0]
                    link_used(parent, "package")
        edges[path] = found
    return edges


def load(path):
    """-> (facts, edges, call edges), each by path; None when absent or written by another version."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION or data.get("parser") != parse.mode():
        return None
    return (data.get("files", {}), {p: dict(e) for p, e in data.get("edges", {}).items()},
            data.get("calls", {}))


def save(path, files, edges, calls):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": VERSION, "parser": parse.mode(), "files": files, "edges": edges,
                               "calls": calls},
                              separators=(",", ":")), "utf-8")
    tmp.replace(path)

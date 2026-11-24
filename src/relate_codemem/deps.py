"""File-to-file dependencies, resolved from declared symbols. No store access.

`facts()` reduces a source file to what resolution needs: its package, the names it
declares, its imports and the names it uses. `resolve()` turns the facts of every
file into dependency edges. Facts are kept on disk, so an unchanged file is never
read again, yet it gains or loses an edge when another file starts or stops
declaring a name it uses.

The analysis is lexical: a name that appears in the code of a file links it to the
file that declares that name in a visible package. There is no type inference.
"""
import ast
import json
import posixpath
import re
import sys

from . import iac, parse

VERSION = 6

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
    if lang in iac.LANGS:
        return iac.facts(path, text, lang, symbols, imports)
    out = {"lang": lang, "imports": [[m, c] for m, c in imports if m][:400],
           "names": imported_names(text, lang, imports)}
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
    out = {"defs": [], "supers": {}, "implements": {}, "calls": {}}
    callables = []
    for s in symbols:
        owner = s["qualname"].rsplit(".", 1)[0] if "." in s["qualname"] else ""
        callable_ = s["kind"] in ("function", "method")
        out["defs"].append([s["id"], s["name"], owner, s["kind"],
                            _arity(s["signature"], lang, s["kind"]) if callable_ else -1])
        if callable_:
            callables.append(s)
        elif s["kind"] in TYPE_KINDS:
            # supertypes in the order written: the superclass comes before the interfaces
            after = s["signature"].split(s["name"], 1)[-1]
            supers = [n for n in dict.fromkeys(_CAPITALISED.findall(after)) if n != s["name"]]
            if supers:
                out["supers"][s["name"]] = supers[:8]
                contract = re.split(r"\bimplements\b", after, maxsplit=1)
                if len(contract) == 2:
                    out["implements"][s["name"]] = [n for n in supers if n in _CAPITALISED.findall(contract[1])]
    if lang == "rust":                  # impl Trait for Type
        for trait, kind in re.findall(r"\bimpl(?:<[^>{]*>)?\s+(?:\w+::)*(\w+)(?:<[^>{]*>)?\s+for\s+(\w+)", text):
            out["supers"].setdefault(kind, []).append(trait)
            out["implements"].setdefault(kind, []).append(trait)
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
CALL_VIA = ("receiver", "self", "new", "scope", "name", "reference")


def resolve_calls(files, edges, live, scopes=None):
    """-> {path: {"calls": [...], "extends": [...], "overrides": [...], "external": [...]}},
    links leaving the symbols of each file.

    calls: [caller id, callee id, via, distance, candidates, inherited]. A call reaches a
    definition in its own file (distance 0) or in a file it depends on (1), and from there
    only what the type hierarchy brings: when a type does not declare the method, its
    supertypes are searched, superclass before interfaces, and the first declaration found
    is the one the call runs. `inherited` counts the levels climbed. `via` says how the
    callee was chosen: "receiver" (the type of the receiver is written in the file), "self"
    (same class or a supertype), "new" (construction), "scope" (a function in scope) or
    "name" (by name only, among visible files). A call that still has more than
    MAX_CANDIDATES homonyms is left out.

    extends: [type id, supertype id, "extends" | "implements"].
    overrides: [method id, id of the nearest method above that it redefines]. Following
    them downwards from an interface method gives what a call on the interface may run.

    external: [symbol id, "CALLS" | "EXTENDS", external id, kind, module, root, scope]. A call
    or a supertype that no project symbol answers is external when its name comes from an
    import that leads out of the project (`scopes`, from resolve()). What has no such import
    is left out: unresolved does not mean external."""
    by_file = {}
    for path, f in files.items():
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

    ring_cache = {}

    def rings(path):
        if path not in ring_cache:
            ring_cache[path] = [[path] if path in by_file else [],
                                sorted(t for t in edges.get(path, ()) if t in by_file)]
        return ring_cache[path]

    def types_named(name, path):
        """[(distance, path)] of the nearest files, seen from `path`, declaring a type of that name."""
        for distance, ring in enumerate(rings(path)):
            hits = [(distance, p) for p in ring if name in by_file[p]["types"]]
            if hits:
                return hits
        return []

    def fits(arity, count):
        return count < 0 or arity < 0 or arity == count

    def methods_of(path, type_name, name, count, seen=None, own=True):
        """-> (methods, levels climbed). The methods `name` that a type declared in `path`
        answers with: its own, or else the first found in its supertypes, in written order.
        `own=False` starts at the supertypes (a `super` call, or what a method redefines)."""
        seen = set() if seen is None else seen
        if (path, type_name) in seen or len(seen) > 60:
            return [], 0
        seen.add((path, type_name))
        if own:
            found = [m for m in by_file[path]["methods"].get((type_name, name), ()) if fits(m[1], count)]
            if found:
                return found, 0
        for parent in files[path].get("supers", {}).get(type_name, ()):
            for _, where in types_named(parent, path):
                found, levels = methods_of(where, parent, name, count, seen)
                if found:
                    return found, levels + 1
        return [], 0

    result = {}
    for path, f in files.items():
        if path not in by_file:
            continue
        outside = []
        imported, leads = f.get("names", {}), (scopes or {}).get(path, {})

        def external(source, relation, local, member, kind):
            """Records `local[.member]` as an external target when `local` was imported from outside."""
            entry = imported.get(local)
            scope = None
            if entry:           # `import os.path` binds `os`: the module is known under a longer name
                scope = leads.get(entry[0]) or next((s for m, s in leads.items() if m.startswith(entry[0] + ".")), None)
            if scope not in ("external", "stdlib"):
                return
            module = entry[0]
            name = ".".join(x for x in (module.replace("::", ".").replace("\\", "."), entry[1], member) if x)
            outside.append([source, relation, name, kind, module, module_root(module, f["lang"]), scope])

        distance_of = {p: d for d, ring in enumerate(rings(path)) for p in ring}
        owners = {sid: owner.rsplit(".", 1)[-1] for sid, _, owner, _, _ in f["defs"]}
        rows = {}
        for caller, sites in f.get("calls", {}).items():
            for name, kind, target, count in sites:
                hits, via = [], ""           # hits: (callee id, distance, inherited)
                own = owners.get(caller, "")
                if kind == "type":
                    for distance, where in types_named(target, path):
                        found, levels = methods_of(where, target, name, count)
                        hits += [(sid, distance, levels) for sid, _ in found]
                    via = "receiver"
                elif kind == "new":
                    hits = [(sid, d, 0) for d, where in types_named(name, path) for sid in by_file[where]["types"][name]]
                    via = "new"
                elif kind in ("none", "super") and own:
                    found, levels = methods_of(path, own, name, count, own=kind == "none")
                    hits = [(sid, 0, levels) for sid, _ in found]
                    via = "self"
                if not hits and kind == "none":
                    for distance, ring in enumerate(rings(path)):
                        hits = [(sid, distance, 0) for p in ring
                                for sid, arity in by_file[p]["functions"].get(name, ()) if fits(arity, count)]
                        if hits:
                            break
                    via = "scope"
                if not hits and kind == "none":         # Type(...) without `new`
                    hits = [(sid, d, 0) for d, where in types_named(name, path) for sid in by_file[where]["types"][name]]
                    via = "new"
                if not hits and (kind == "unknown" or (kind == "type" and not types_named(target, path))):
                    # By name only. A call with a receiver reaches methods; it reaches a plain
                    # function only when the receiver names its file or directory (a module, a package).
                    found = [(sid, arity, distance_of[p]) for p in distance_of
                             for sid, arity, member in by_file[p]["any"].get(name, ())
                             if member or (target and target in (posixpath.splitext(posixpath.basename(p))[0],
                                                                 posixpath.basename(posixpath.dirname(p))))]
                    exact = [h for h in found if h[1] == count]      # by name, an unknown arity proves nothing
                    hits = [(sid, distance, 0) for sid, _, distance in exact or [h for h in found if h[1] < 0]]
                    via = "name"
                targets = {sid: (distance, levels) for sid, distance, levels in hits if sid != caller}
                if not hits:
                    if kind in ("type", "unknown") and target:
                        external(caller, "CALLS", target, name, "member")
                    elif kind in ("new", "none"):
                        external(caller, "CALLS", name, "", "type" if kind == "new" or name[:1].isupper() else "function")
                if not targets or len(targets) > MAX_CANDIDATES:
                    continue
                for callee, (distance, levels) in targets.items():
                    known = rows.get((caller, callee))
                    rank = (CALL_VIA.index(via), levels)       # one link per pair: the most certain, the nearest
                    if known is None or rank < (CALL_VIA.index(known[0]), known[3]):
                        rows[(caller, callee)] = [via, distance, len(targets), levels]

        extends, overrides = [], []
        contracts = f.get("implements", {})
        for type_name, parents in f.get("supers", {}).items():
            for child in by_file[path]["types"].get(type_name, ()):
                for parent in parents:
                    found = [sid for _, where in types_named(parent, path) for sid in by_file[where]["types"][parent]]
                    if 0 < len(found) <= MAX_CANDIDATES:
                        kind = "implements" if parent in contracts.get(type_name, ()) else "extends"
                        extends += [[child, sid, kind] for sid in found if sid != child]
                    elif not found:
                        external(child, "EXTENDS", parent, "", "type")
        for (type_name, name), methods in by_file[path]["methods"].items():
            if type_name not in f.get("supers", {}) or name == type_name:
                continue
            for sid, arity in methods:
                above, _ = methods_of(path, type_name, name, arity, own=False)
                overrides += [[sid, other] for other, _ in above[:MAX_CANDIDATES] if other != sid]

        links = {"calls": sorted([a, b, *rest] for (a, b), rest in rows.items()),
                 "extends": sorted(extends), "overrides": sorted(overrides),
                 "external": sorted({tuple(row[:3]): row for row in outside}.values())}
        if any(links.values()):
            result[path] = links
    result.update(iac.resolve(files, live))         # Terraform and Ansible have their own references
    return result


_JS_IMPORT = re.compile(r"""import\s+(?:type\s+)?([^'"`;]*?)\s+from\s*['"]([^'"]+)['"]""")
_JS_REQUIRE = re.compile(r"""(?:const|let|var)\s+(\{[^}]*\}|[A-Za-z_$][\w$]*)\s*=\s*require\(\s*['"]([^'"]+)['"]\s*\)""")
_GO_IMPORT = re.compile(r'^\s*(?:import\s+)?(?:([\w.]+)\s+)?"([\w./-]+)"\s*$', re.M)


def imported_names(text, lang, imports):
    """{local name: [module, member]}: what each import makes usable in the file. `member` is
    the name inside the module, or "" when the local name stands for the module itself.
    The module is spelled as in `imports`, so that its resolution can be looked up."""
    names = {}
    if lang == "python":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return names
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    names[a.asname or a.name.split(".")[0]] = [a.name if a.asname else a.name.split(".")[0], ""]
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if node.level and not node.module:        # from . import x: x is a module
                        names[a.asname or a.name] = ["." * node.level + a.name, ""]
                    elif a.name != "*":
                        names[a.asname or a.name] = ["." * node.level + (node.module or ""), a.name]
    elif lang in ("javascript", "typescript"):
        for clause, module in _JS_IMPORT.findall(text) + _JS_REQUIRE.findall(text):
            braces = re.search(r"\{([^}]*)\}", clause)
            for part in (braces.group(1).split(",") if braces else []):
                bits = re.split(r"\s+as\s+|\s*:\s*", part.strip().removeprefix("type ").strip())
                if bits[0]:
                    names[bits[-1].strip()] = [module, bits[0].strip()]
            star = re.search(r"\*\s+as\s+([\w$]+)", clause)
            if star:
                names[star.group(1)] = [module, ""]
            default = re.match(r"\s*([A-Za-z_$][\w$]*)\s*(?:,|$)", clause)
            if default:
                names[default.group(1)] = [module, ""]
    elif lang == "go":
        for alias, module in _GO_IMPORT.findall(text):
            last = [s for s in module.split("/") if not re.fullmatch(r"v\d+", s)][-1]
            if alias not in ("_", "."):
                names[alias or last] = [module, ""]
    else:
        for module, _ in imports:
            if module and not module.startswith("?") and not module.endswith((".", "._", "*")):
                names[re.split(r"[.\\/]|::", module)[-1]] = [module, ""]
    return dict(list(names.items())[:400])


_STDLIB_PREFIX = {
    "java": ("java.", "javax.", "jdk."), "kotlin": ("kotlin.", "java.", "javax."),
    "scala": ("scala.", "java.", "javax."), "csharp": ("System",), "rust": ("std::", "core::", "alloc::"),
    "swift": ("Foundation", "Swift", "Dispatch"), "php": (),
}
_NODE_BUILTINS = {"assert", "buffer", "child_process", "cluster", "crypto", "dns", "events", "fs", "http", "http2",
                  "https", "net", "os", "path", "perf_hooks", "process", "querystring", "readline", "stream",
                  "string_decoder", "timers", "tls", "url", "util", "v8", "vm", "worker_threads", "zlib"}


def external_scope(module, lang):
    """"stdlib" when an import that is not in the project names the language's own library,
    "external" otherwise. Only the conventions that can be read off the name are used."""
    if lang == "python":
        return "stdlib" if module.split(".")[0] in getattr(sys, "stdlib_module_names", ()) else "external"
    if lang == "go":                # third-party import paths start with a domain name
        return "external" if "." in module.split("/")[0] else "stdlib"
    if lang in ("javascript", "typescript"):
        return "stdlib" if module.startswith("node:") or module.split("/")[0] in _NODE_BUILTINS else "external"
    if lang in ("c", "cpp", "objc", "terraform"):
        return "external"
    if lang == "ansible":
        return "unresolved"             # a role, a task file or a template that is not in the project
    prefixes = _STDLIB_PREFIX.get(lang, ())
    return "stdlib" if any(module == p.rstrip(".:") or module.startswith(p) for p in prefixes) else "external"


def module_root(module, lang):
    """The library an external module belongs to, as far as its name tells."""
    if lang in ("javascript", "typescript"):
        parts = module.removeprefix("node:").split("/")
        return "/".join(parts[:2]) if module.startswith("@") else parts[0]
    if lang == "go":
        parts = module.split("/")
        return "/".join(parts[:3]) if "." in parts[0] else parts[0]
    if lang == "terraform":             # registry `namespace/name[/provider]`, or an address
        return module[:80] if "://" in module or module.startswith("git::") else "/".join(module.split("/")[:2])
    if lang == "ansible":
        return module.removeprefix("role:").split("/")[0]
    parts = [p for p in re.split(r"[.\\/]|::", module) if p]
    if lang in JVM and len(parts) > 2:
        return ".".join(parts[:2])
    return parts[0] if parts else module


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


def resolve(files, live, scopes=None):
    """files: {path: facts}, live: paths that exist. -> {path: {target: via}}.

    When `scopes` is a dict it receives {path: {module: scope}}, where scope says where each
    import leads: "internal" (a file of the project), "stdlib", "external", or "unresolved"
    for a relative import whose target is missing."""
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
            if lang == "terraform" and module.startswith("."):
                # a module source is a directory: the files that declare something in it
                target = "tf:" + posixpath.normpath(posixpath.join(posixpath.dirname(path), module))
                for paths in packages.get(target, {}).values():
                    for other in paths:
                        link(other, "import")
                        hit = True
            elif lang == "go":
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
                        hit = True
                        break
            if scopes is not None and not module.startswith("?"):
                relative = module.startswith(".") or lang in ("c", "cpp", "objc", "shell") and "/" in module
                scopes.setdefault(path, {})[module] = (
                    "internal" if hit else "unresolved" if relative else external_scope(module, lang))
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
    """-> (facts, edges, symbol links, import scopes), each by path; None when absent or written by another version."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION or data.get("parser") != parse.mode():
        return None
    return (data.get("files", {}), {p: dict(e) for p, e in data.get("edges", {}).items()},
            data.get("calls", {}), data.get("imports", {}))


def save(path, files, edges, calls, imports):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": VERSION, "parser": parse.mode(), "files": files, "edges": edges,
                               "calls": calls, "imports": imports},
                              separators=(",", ":")), "utf-8")
    tmp.replace(path)

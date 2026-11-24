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

VERSION = 1

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
    """-> (facts by path, edges by path); empty when absent or written by another version."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION or data.get("parser") != parse.mode():
        return None
    return data.get("files", {}), {p: dict(e) for p, e in data.get("edges", {}).items()}


def save(path, files, edges):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": VERSION, "parser": parse.mode(), "files": files, "edges": edges},
                              separators=(",", ":")), "utf-8")
    tmp.replace(path)

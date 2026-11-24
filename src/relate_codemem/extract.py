"""Pure extraction: file classification, symbols, imports, text chunks, ADR and meeting
metadata. No store access."""
import ast
import hashlib
import posixpath
import re
from pathlib import Path

from . import config, iac, parse

CODE_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript", ".vue": "javascript",
    ".svelte": "javascript", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".kts": "kotlin", ".cs": "csharp", ".swift": "swift", ".php": "php", ".rb": "ruby",
    ".scala": "scala", ".dart": "dart", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".hpp": "cpp", ".m": "objc", ".mm": "objc", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".sql": "sql", ".lua": "lua", ".r": "r", ".ex": "elixir", ".exs": "elixir",
    ".html": "html", ".css": "css", ".scss": "css", ".cypher": "cypher", ".tf": "terraform",
    ".tfvars": "terraform",
}
DOC_EXT = {".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc", ".org"}
TRANSCRIPT_EXT = DOC_EXT | {".vtt", ".srt"}
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".gradle", ".properties"}
CONFIG_NAMES = {"Dockerfile", "Makefile", "Procfile", ".env.example"}


def cfg():
    return config.project_config()


def split_terms(*texts):
    """camelCase / snake_case / path pieces as separate lowercase words, so the
    full-text index (word tokens, no stemming) can find `parseConfig` from `config`."""
    words = []
    for text in texts:
        for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text or ""):
            parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", ident)
            parts = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", parts)
            pieces = [p.lower() for p in re.split(r"[_\s]+", parts) if p]
            if len(pieces) > 1:
                words.extend(pieces)
                words.append("".join(pieces))
            words.extend(pieces[:1] if len(pieces) == 1 else [])
    seen, out = set(), []
    for w in words:
        if w not in seen and len(w) > 1:
            seen.add(w)
            out.append(w)
    return " ".join(out)[:1500]


def slugify(text, limit=48):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "x"


def under(path, dirs):
    return any(path == d or path.startswith(d.rstrip("/") + "/") for d in dirs)


def classify(path):
    """-> (kind, lang) or None when the file is not indexed."""
    p = Path(path)
    ext = p.suffix.lower()
    if under(path, cfg()["meetings_dirs"]):
        return ("meeting", "text") if ext in TRANSCRIPT_EXT and p.name.lower() != "readme.md" else None
    if under(path, cfg()["decisions_dirs"]):
        return ("decision", "markdown") if ext in DOC_EXT and p.name.lower() not in (
            "readme.md", "template.md") else None
    if ext in DOC_EXT:
        return "doc", "markdown" if ext in (".md", ".mdx", ".markdown") else "text"
    if ext in CODE_EXT:
        return "code", CODE_EXT[ext]
    if ext in CONFIG_EXT or p.name in CONFIG_NAMES:
        return "config", ext.lstrip(".") or p.name.lower()
    lang = parse.detect(path)        # other languages, when the tree-sitter pack is installed
    return ("code", lang) if lang else None


def excluded(path):
    parts = path.split("/")
    return (any(part in cfg()["exclude_dirs"] for part in parts[:-1])
            or parts[-1] in cfg()["exclude_files"])


def sha(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def python_symbols(text):
    tree = ast.parse(text)
    lines = text.splitlines()
    out = []

    def visit(node, prefix, depth):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                is_class = isinstance(child, ast.ClassDef)
                qual = f"{prefix}.{child.name}" if prefix else child.name
                kind = "class" if is_class else ("method" if prefix else "function")
                if is_class:
                    bases = ", ".join(ast.unparse(b) for b in child.bases)
                    sig = f"class {child.name}({bases})" if bases else f"class {child.name}"
                else:
                    sig = f"def {child.name}({ast.unparse(child.args)})"
                    if isinstance(child, ast.AsyncFunctionDef):
                        sig = "async " + sig
                end = getattr(child, "end_lineno", child.lineno)
                out.append({"name": child.name, "qualname": qual, "kind": kind,
                            "startLine": child.lineno, "endLine": end, "signature": sig[:300],
                            "doc": (ast.get_docstring(child) or "")[:600],
                            "body": "\n".join(lines[child.lineno - 1:end])})
                if is_class and depth < 3:
                    visit(child, qual, depth + 1)

    visit(tree, "", 0)
    return out


_MOD = r"(?:(?:public|private|protected|internal|static|final|abstract|override|async|virtual|sealed|open|export|default|synchronized|suspend|inline|unsafe|extern|partial|readonly|pub(?:\([^)]*\))?)\s+)*"
_TYPE = ("type", re.compile(r"^\s*" + _MOD + r"(class|interface|enum|struct|trait|record|object|protocol|module|namespace|extension|impl)\s+([A-Za-z_][\w.]*)"))
PATTERNS = {
    "javascript": [_TYPE,
                   ("function", re.compile(r"^\s*" + _MOD + r"function\s*\*?\s*([A-Za-z_$][\w$]*)")),
                   ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)")),
                   ("type", re.compile(r"^\s*(?:export\s+)?(type)\s+([A-Za-z_$][\w$]*)\s*(?:<[^=]*>)?\s*=")),
                   ("method", re.compile(r"^\s+(?:(?:public|private|protected|static|async|override|readonly)\s+)*(?:get\s+|set\s+)?([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\([^)]*\)\s*(?::\s*[^{;]+)?\{"))],
    "go": [("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")),
           ("type", re.compile(r"^(type)\s+([A-Za-z_]\w*)\s+(?:struct|interface|func|map|\[)"))],
    "rust": [_TYPE, ("function", re.compile(r"^\s*" + _MOD + r"(?:const\s+)?fn\s+([A-Za-z_]\w*)"))],
    "java": [_TYPE, ("method", re.compile(r"^\s+(?:(?:public|private|protected|static|final|abstract|synchronized|default)\s+)+[\w<>\[\],.? ]+?\s+([A-Za-z_]\w*)\s*\([^;]*$"))],
    "csharp": [_TYPE, ("method", re.compile(r"^\s+(?:(?:public|private|protected|internal|static|virtual|override|async|abstract|sealed)\s+)+[\w<>\[\],.? ]+?\s+([A-Za-z_]\w*)\s*\([^;]*$"))],
    "kotlin": [_TYPE, ("function", re.compile(r"^\s*" + _MOD + r"fun\s+(?:<[^>]+>\s*)?(?:[\w.<>?]+\.)?([A-Za-z_]\w*)\s*\("))],
    "swift": [_TYPE, ("function", re.compile(r"^\s*" + _MOD + r"(?:mutating\s+)?func\s+([A-Za-z_]\w*)"))],
    "php": [_TYPE, ("function", re.compile(r"^\s*" + _MOD + r"function\s+&?([A-Za-z_]\w*)"))],
    "ruby": [_TYPE, ("function", re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)"))],
    "scala": [_TYPE, ("function", re.compile(r"^\s*" + _MOD + r"def\s+([A-Za-z_]\w*)"))],
    "dart": [_TYPE],
    "python": [_TYPE, ("function", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"))],
    "elixir": [("type", re.compile(r"^\s*(defmodule)\s+([\w.]+)")),
               ("function", re.compile(r"^\s*defp?\s+([a-z_]\w*[?!]?)"))],
    "c": [("type", re.compile(r"^(?:typedef\s+)?(struct|enum|union)\s+([A-Za-z_]\w*)\s*\{")),
          ("function", re.compile(r"^[A-Za-z_][\w\s\*&:<>,]*?\b([A-Za-z_]\w*)\s*\([^;{]*\)\s*(?:const\s*)?\{?\s*$"))],
    "shell": [("function", re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{?"))],
    "sql": [("type", re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNIQUE\s+|MATERIALIZED\s+|TEMP(?:ORARY)?\s+)?(TABLE|VIEW|FUNCTION|PROCEDURE|INDEX|TRIGGER|TYPE)\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"`]+)", re.I))],
    "lua": [("function", re.compile(r"^\s*(?:local\s+)?function\s+([\w.:]+)"))],
}
for _alias, _src in (("typescript", "javascript"), ("cpp", "c"), ("objc", "c")):
    PATTERNS[_alias] = PATTERNS[_src]
PATTERNS["cpp"] = [_TYPE] + PATTERNS["c"]
_C_KEYWORDS = {"if", "for", "while", "switch", "return", "else", "do", "sizeof", "catch", "function", "with"}


def regex_symbols(text, lang):
    patterns = PATTERNS.get(lang)
    if not patterns:
        return []
    lines = text.splitlines()
    found = []
    for i, line in enumerate(lines):
        if len(line) > 400:
            continue
        for kind, rx in patterns:
            m = rx.match(line)
            if not m:
                continue
            name = m.group(m.lastindex)
            if kind == "type":
                kind = m.group(1).lower() if m.lastindex > 1 else "type"
            if name in _C_KEYWORDS:
                continue
            doc = []
            j = i - 1
            while j >= 0 and len(doc) < 8 and re.match(r"^\s*(//|#|\*|/\*|--|\"\"\")", lines[j]):
                doc.insert(0, re.sub(r"^\s*(///?|#+|/?\*+/?|--)\s?", "", lines[j]).strip())
                j -= 1
            found.append({"name": name, "qualname": name, "kind": kind, "startLine": i + 1,
                          "signature": line.strip()[:300], "doc": " ".join(doc)[:600]})
            break
    for idx, sym in enumerate(found):
        end = found[idx + 1]["startLine"] - 1 if idx + 1 < len(found) else len(lines)
        sym["endLine"] = max(end, sym["startLine"])
        sym["body"] = "\n".join(lines[sym["startLine"] - 1:sym["endLine"]])
    return found


def extract_symbols(path, text, lang, rev):
    try:
        if lang == "python":
            raw = python_symbols(text)
        elif lang in iac.LANGS:
            raw = iac.symbols(path, text, lang)
        elif parse.curated(lang, path) or lang not in PATTERNS:
            raw = parse.symbols(path, text, lang)
        else:                       # a language with patterns but no table: patterns are the better source
            raw = None
    except (SyntaxError, ValueError, RecursionError):
        raw = None
    if raw is None:
        raw = regex_symbols(text, lang)
    seen, out = {}, []
    for s in raw[:2000]:
        n = seen[s["qualname"]] = seen.get(s["qualname"], 0) + 1
        sid = f"{path}::{s['qualname']}" + (f"~{n}" if n > 1 else "")
        out.append({"id": sid, "path": path, "rev": rev, "name": s["name"],
                    "qualname": s["qualname"], "kind": s["kind"], "startLine": s["startLine"],
                    "endLine": s["endLine"], "signature": s["signature"],
                    "hash": sha(s["body"])[:16], "title": f"{s['kind']} {s['qualname']}", "top": s.get("top"),
                    "text": s.get("text") or (s["signature"] + ("\n" + s["doc"] if s["doc"] else ""))[:900],
                    "terms": split_terms(s["qualname"], path)})
    return out


IMPORT_RX = {
    "javascript": [re.compile(r"""(?:import|export)\s[^'"`;]*?from\s*['"]([^'"]+)['"]"""),
                   re.compile(r"""^\s*import\s*['"]([^'"]+)['"]""", re.M),
                   re.compile(r"""(?:require|import)\(\s*['"]([^'"]+)['"]\s*\)""")],
    "go": [re.compile(r'^\s*(?:import\s+)?(?:[\w.]+\s+)?"([\w./-]+)"\s*$', re.M)],
    "rust": [re.compile(r"^\s*(?:pub\s+)?use\s+([\w:]+)", re.M), re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)\s*;", re.M)],
    "java": [re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.M)],
    "kotlin": [re.compile(r"^\s*import\s+([\w.]+)", re.M)],
    "scala": [re.compile(r"^\s*import\s+([\w.]+)", re.M)],
    "csharp": [re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", re.M)],
    "swift": [re.compile(r"^\s*import\s+(\w+)", re.M)],
    "php": [re.compile(r"^\s*use\s+([\w\\]+)", re.M), re.compile(r"""(?:require|include)(?:_once)?\s*\(?\s*['"]([^'"]+)['"]""")],
    "ruby": [re.compile(r"""^\s*require(?:_relative)?\s+['"]([^'"]+)['"]""", re.M)],
    "c": [re.compile(r'^\s*#\s*(?:include|import)\s*[<"]([^>"]+)[>"]', re.M)],
    "shell": [re.compile(r"^\s*(?:source|\.)\s+([\w./-]+)", re.M)],
    "dart": [re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""", re.M)],
}
for _alias, _src in (("typescript", "javascript"), ("cpp", "c"), ("objc", "c")):
    IMPORT_RX[_alias] = IMPORT_RX[_src]


def extract_imports(path, text, lang):
    """-> list of (module name, candidate project paths). A module name starting with "?" is
    a guess that only counts when one of its candidates exists."""
    out = []
    base = Path(path).parent
    if lang in iac.LANGS:
        return [(module, list(candidates)) for module, candidates in iac.imports(path, text, lang)]
    if lang == "python":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return out
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [(a.name, 0) for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [(node.module or "", node.level)]
                if node.level and not node.module:
                    mods = [(a.name, node.level) for a in node.names]
            for mod, level in mods:
                rel = mod.replace(".", "/")
                if level:
                    anchor = base
                    for _ in range(level - 1):
                        anchor = anchor.parent
                    roots = [anchor]
                    name = "." * level + mod
                else:
                    roots = [Path("."), Path("src"), Path("lib"), base]
                    name = mod
                cands = []
                for r in roots:
                    cands += [(r / (rel + ".py")).as_posix(), (r / rel / "__init__.py").as_posix()]
                if level and isinstance(node, ast.ImportFrom) and not node.module:
                    cands.append((roots[0] / "__init__.py").as_posix())   # from . import name: defined by the package
                out.append((name, cands))
                if isinstance(node, ast.ImportFrom) and node.module:
                    # `from pkg import name`: name may be a submodule. A module name starting with
                    # "?" is a guess: it becomes a dependency when the file exists, and nothing otherwise.
                    for a in node.names:
                        if a.name != "*":
                            sub = f"{rel}/{a.name}"
                            out.append((f"?{name}.{a.name}", [c for r in roots for c in (
                                (r / (sub + ".py")).as_posix(), (r / sub / "__init__.py").as_posix())]))
        return out
    for rx in IMPORT_RX.get(lang, []):
        for mod in rx.findall(text):
            cands = []
            if mod.startswith(".") or lang in ("c", "cpp", "objc", "shell", "ruby", "php"):
                target = posixpath.normpath((base / mod).as_posix())
                exts = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte", ".rb", ".dart",
                        "/index.ts", "/index.tsx", "/index.js", "/index.jsx"]
                cands = [target + e for e in exts]
                if target.endswith(".js"):
                    cands += [target[:-3] + ".ts", target[:-3] + ".tsx"]
            elif lang == "rust" and "::" not in mod:
                cands = [(base / f"{mod}.rs").as_posix(), (base / mod / "mod.rs").as_posix()]
            elif lang == "rust" and mod.startswith("crate::"):
                # the module file is the longest prefix of the path that exists under src/
                root = path[:path.rfind("/src/") + 5] if "/src/" in path else ("src/" if path.startswith("src/") else "")
                parts = mod.split("::")[1:]
                for cut in range(len(parts), 0, -1):
                    stem = root + "/".join(parts[:cut])
                    cands += [stem + ".rs", stem + "/mod.rs"]
            elif lang in ("java", "kotlin", "scala"):
                tail = mod.replace(".", "/")
                cands = [f"{root}/{tail}{ext}" for root in ("src/main/java", "src/main/kotlin", "src/main/scala", "src")
                         for ext in (".java", ".kt", ".scala")]
            out.append((mod, cands))
    return out


def parse_front_matter(text):
    meta, body, offset = {}, text, 0
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                m = re.match(r"^([A-Za-z_][\w -]*):\s*(.*)$", line)
                if m:
                    meta[m.group(1).strip().lower()] = m.group(2).strip().strip("'\"")
            cut = text.find("\n", end + 1)
            cut = len(text) if cut == -1 else cut + 1
            offset = text[:cut].count("\n")
            body = text[cut:]
    return meta, body, offset


def clean_transcript(text):
    """Drop WebVTT / SRT cue numbers and timestamps, keep the spoken lines."""
    keep = []
    for line in text.splitlines():
        s = line.strip()
        if s == "WEBVTT" or s.isdigit() or "-->" in s or s.startswith(("NOTE", "Kind:", "Language:")):
            continue
        keep.append(re.sub(r"</?v[^>]*>", lambda m: "" if m.group(0).startswith("</") else m.group(0)[3:-1].strip() + ": ", line))
    return "\n".join(keep)


def chunk_text(path, text, kind):
    """Split on markdown headings, then on paragraphs, into chunks of ~chunk_chars."""
    limit = cfg()["chunk_chars"]
    meta, body, offset = parse_front_matter(text)
    if path.lower().endswith((".vtt", ".srt")):
        body = clean_transcript(body)
    sections, heads, cur, cur_line, in_code = [], [], [], 1, False
    for i, line in enumerate(body.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(r"^(#{1,6})\s+(.*?)\s*#*$", line)
        if m:
            if any(l.strip() for l in cur):
                sections.append((" > ".join(h for _, h in heads), cur_line, cur))
            level = len(m.group(1))
            heads = [(lv, h) for lv, h in heads if lv < level] + [(level, m.group(2))]
            cur, cur_line = [], i
        else:
            cur.append(line)
    if any(l.strip() for l in cur):
        sections.append((" > ".join(h for _, h in heads), cur_line, cur))
    chunks = []
    for title, start, lines in sections:
        buf, buf_start = [], start
        for n, line in enumerate(lines):
            buf.append(line)
            size = sum(len(l) + 1 for l in buf)
            if size >= limit and (not line.strip() or size >= limit * 1.5):
                chunks.append((title, buf_start, "\n".join(buf).strip()))
                buf, buf_start = [], start + n + 1
        if any(l.strip() for l in buf):
            chunks.append((title, buf_start, "\n".join(buf).strip()))
    name = Path(path).name
    return meta, [{"id": f"{path}#{n}", "path": path, "ord": n, "kind": kind,
                   "title": (t or name)[:200], "startLine": line + offset, "text": body_[:limit * 2],
                   "terms": split_terms(t, name)}
                  for n, (t, line, body_) in enumerate(chunks[:400]) if body_]


def first_heading(text, fallback):
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    return (m.group(1) if m else fallback)[:200]


def find_date(meta, path, text):
    for cand in (meta.get("date", ""), Path(path).name, text[:600]):
        m = re.search(r"(20\d{2})[-_./]?(\d{2})[-_./]?(\d{2})", cand)
        if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
            return "-".join(m.groups())
    return None


def split_names(value):
    return [n.strip(" -*[]'\"") for n in re.split(r"[,;]", value.strip("[]")) if n.strip(" -*[]'\"")]


def parse_decision(path, text, meta):
    status = meta.get("status")
    if not status:
        m = re.search(r"^(?:[*_-]*\s*)?(?:status|statut)\s*[*_]*\s*[:：]\s*[*_]*\s*([^\n*_]+)", text, re.I | re.M)
        if not m:
            m = re.search(r"^#+\s*(?:status|statut)\s*\n+\s*([^\n]+)", text, re.I | re.M)
        status = m.group(1) if m else "accepted"
    status = status.strip().lower()[:40]
    closed = bool(re.match(r"(superseded|deprecated|rejected|remplac|obsol|rejet|aband)", status))
    title = meta.get("title") or first_heading(text, Path(path).stem)
    return {"id": f"adr:{path}", "path": path, "title": title, "status": status, "closed": closed,
            "date": find_date(meta, path, text), "text": text[:6000],
            "terms": split_terms(title, Path(path).stem)}


def parse_meeting(path, text, meta):
    title = meta.get("title") or first_heading(text, Path(path).stem)
    people = split_names(meta.get("participants") or meta.get("attendees") or "")
    if not people:
        m = re.search(r"^[*_\s-]*(?:participants?|attendees|pr[ée]sents?)[*_\s]*[:：][*_ ]*(.+)$", text, re.I | re.M)
        if m:
            people = split_names(m.group(1))
    if not people:                       # speaker labels: "Alice Martin: ..." at least twice
        counts = {}
        for name in re.findall(r"^(?:<v\s+)?([A-ZÀ-Ý][\wÀ-ÿ.'-]*(?: [A-ZÀ-Ý][\wÀ-ÿ.'-]*){0,2})>?\s*:\s", text, re.M):
            counts[name] = counts.get(name, 0) + 1
        people = [n for n, c in counts.items() if c >= 2]
    return {"path": path, "title": title, "date": find_date(meta, path, text),
            "participants": people[:40], "summary": text[:1500], "terms": split_terms(title, Path(path).stem)}

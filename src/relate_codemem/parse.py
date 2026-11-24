"""Syntax-tree extraction with tree-sitter, when the optional `parse` extra is installed.

Without it every function here reports "not available" and the callers keep their
regular-expression path. With it:

- the languages described in NODES get exact symbol extents and nesting (a method is
  `Type.method`), from tables that say what a class or a method looks like in each grammar;
- any other language the pack knows, and for which there are no patterns, gets the
  pack's own generic structure extraction, which is coarser;
- comments and string literals are told apart from code exactly, in every language.

The pack downloads a grammar the first time a language is met and keeps it in the
user cache; when that fails (no network), the file falls back to patterns.
"""
from pathlib import Path

from . import config

# node type -> symbol kind. "?" means: read the kind from the declaration's keyword.
_JAVA = {"class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum",
         "record_declaration": "record", "annotation_type_declaration": "annotation",
         "method_declaration": "function", "constructor_declaration": "function"}
_JS = {"class_declaration": "class", "abstract_class_declaration": "class", "class": "class",
       "interface_declaration": "interface", "enum_declaration": "enum", "type_alias_declaration": "type",
       "function_declaration": "function", "generator_function_declaration": "function",
       "method_definition": "function", "variable_declarator": "variable"}
_C = {"struct_specifier": "struct", "enum_specifier": "enum", "union_specifier": "struct",
      "class_specifier": "class", "namespace_definition": "namespace", "function_definition": "function"}
NODES = {
    "java": _JAVA,
    "kotlin": {"class_declaration": "?", "object_declaration": "object", "function_declaration": "function"},
    "scala": {"class_definition": "class", "object_definition": "object", "trait_definition": "trait",
              "enum_definition": "enum", "function_definition": "function", "function_declaration": "function"},
    "csharp": {"class_declaration": "class", "interface_declaration": "interface", "struct_declaration": "struct",
               "enum_declaration": "enum", "record_declaration": "record", "method_declaration": "function",
               "constructor_declaration": "function", "namespace_declaration": "namespace",
               "file_scoped_namespace_declaration": "namespace"},
    "go": {"type_spec": "type", "function_declaration": "function", "method_declaration": "function"},
    "rust": {"struct_item": "struct", "enum_item": "enum", "trait_item": "trait", "impl_item": "impl",
             "mod_item": "module", "type_item": "type", "function_item": "function",
             "function_signature_item": "function"},
    "javascript": _JS, "typescript": _JS, "tsx": _JS,
    "php": {"class_declaration": "class", "interface_declaration": "interface", "trait_declaration": "trait",
            "enum_declaration": "enum", "function_definition": "function", "method_declaration": "function"},
    "ruby": {"class": "class", "module": "module", "method": "function", "singleton_method": "function"},
    "c": _C, "cpp": _C,
    "swift": {"class_declaration": "?", "protocol_declaration": "protocol", "function_declaration": "function",
              "protocol_function_declaration": "function"},
}
# kinds whose members are qualified by the container's name
CONTAINERS = {"class", "interface", "enum", "struct", "record", "object", "trait", "protocol", "annotation",
              "impl", "module", "extension", "actor"}
# a namespace is neither a symbol nor part of a name; an impl block only lends its type's name
TRANSPARENT = {"namespace", "impl"}
_KEYWORD_KINDS = ("interface", "enum", "struct", "extension", "actor", "object", "class")
_NAME_TYPES = {"identifier", "type_identifier", "simple_identifier", "name", "constant", "property_identifier",
               "field_identifier", "namespace_identifier", "package_identifier"}
_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_SKIP_FILES = {".vue", ".svelte"}      # a script embedded in markup is not a source file of that language

# our language names that differ from the pack's
_PACK_NAMES = {"shell": "bash"}
# formats the pack can parse but that are data or markup, not code worth indexing as such
_NOT_CODE = {"csv", "tsv", "psv", "json", "json5", "jsonc", "yaml", "toml", "xml", "ini", "properties", "markdown",
             "markdown_inline", "vimdoc", "diff", "gitignore", "gitattributes", "gitcommit", "git_config",
             "git_rebase", "requirements", "po", "bibtex", "latex", "rst", "org", "comment", "regex", "printf",
             "passwd", "ssh_config", "editorconfig", "dotenv", "html", "css", "scss", "svg", "dtd", "http",
             "jsdoc", "luadoc", "phpdoc", "doxygen", "re2c", "ungrammar", "query", "hosts", "udev", "desktop"}
_GENERIC_KINDS = {"class": "class", "struct": "struct", "enum": "enum", "interface": "interface", "trait": "trait",
                  "module": "module", "function": "function", "method": "method", "impl": "impl",
                  "namespace": "namespace", "type": "type", "union": "struct", "macro": "function"}

_pack = None
_parsers = {}
_detected = {}


def enabled():
    return (config.setting("PARSER")[0] or "auto").lower() not in ("regex", "off", "none")


def cache_dir():
    return config.user_cache_dir() / "tree-sitter"


def _load():
    """The language pack module, configured to keep its grammars in our cache, or None."""
    global _pack
    if _pack is None:
        try:
            import tree_sitter_language_pack as pack
            pack.configure(pack.PackConfig(cache_dir=str(cache_dir())))
            _pack = pack if hasattr(pack, "process") else False
        except Exception:        # the extra is absent, or an older pack without this interface
            _pack = False
    return (_pack or None) if enabled() else None


def _pack_name(lang, path=""):
    if lang == "typescript" and path.endswith("x"):
        return "tsx"
    return _PACK_NAMES.get(lang, lang)


def _parser(lang, path=""):
    name = _pack_name(lang, path)
    pack = _load()
    if pack is None or Path(path).suffix.lower() in _SKIP_FILES:
        return None, name
    if name not in _parsers:
        try:
            _parsers[name] = pack.get_parser(name)      # downloads the grammar on first use
        except Exception:        # unknown language, or the download failed
            _parsers[name] = None
    return _parsers[name], name


def available():
    return _load() is not None


def curated(lang, path=""):
    return _pack_name(lang, path) in NODES


def detect(path):
    """A language name for a file the extension tables do not know, or None."""
    pack = _load()
    suffix = Path(path).suffix.lower() or Path(path).name
    if pack is None:
        return None
    if suffix not in _detected:
        try:
            name = pack.detect_language(path)
        except Exception:
            name = None
        _detected[suffix] = None if name in _NOT_CODE else name
    return _detected[suffix]


def describe():
    if not enabled():
        return "regular expressions (tree-sitter turned off)"
    pack = _load()
    if pack is None:
        return "regular expressions (install the `parse` extra, on Python 3.11 or later, for tree-sitter)"
    try:
        cached = len(pack.downloaded_languages())
        return (f"tree-sitter, {len(pack.manifest_languages())} languages available, {cached} grammar(s) "
                f"downloaded in {cache_dir()}")
    except Exception as exc:
        return f"tree-sitter (the language list is unreachable: {exc})"


def _text(node):
    return node.text.decode("utf-8", errors="replace")


def _name(node, kind):
    """The declared name of a node, or None when it declares nothing we keep."""
    if node.type == "variable_declarator":      # const f = () => ...
        value = node.child_by_field_name("value")
        if value is None or value.type not in _FUNCTION_VALUES:
            return None
    if node.type == "impl_item":
        target = node.child_by_field_name("type")
        return _text(target).split("<")[0] if target is not None else None
    if node.type == "function_definition" and node.child_by_field_name("declarator") is not None:
        target = node.child_by_field_name("declarator")      # C, C++: unwrap pointers down to the name
        while target is not None and target.type not in _NAME_TYPES | {"qualified_identifier", "operator_name",
                                                                         "destructor_name"}:
            target = target.child_by_field_name("declarator")
        return _text(target).replace("::", ".") if target is not None else None
    target = node.child_by_field_name("name")
    if target is None:
        target = next((c for c in node.named_children if c.type in _NAME_TYPES or c.type == "user_type"), None)
    if target is None or (kind in ("struct", "enum", "class") and node.child_by_field_name("body") is None
                          and node.type.endswith("_specifier")):
        return None                              # `struct S x;` mentions a type, it does not declare one
    return _text(target).strip()


def _kind(node, kind):
    if kind != "?":
        return kind
    words = set()
    for child in node.children:           # `enum class E`, `struct S`, `interface I`...
        words.add(child.type)
        if child.type == "modifiers":
            words.update(_text(child).split())
    return next((word for word in _KEYWORD_KINDS if word in words), "class")


def _doc(node):
    lines, prev = [], node.prev_sibling
    while prev is not None and "comment" in prev.type and len(lines) < 8:
        lines[:0] = [line.strip().lstrip("/*#!").rstrip("*/").strip() for line in _text(prev).splitlines()]
        prev = prev.prev_sibling
    return " ".join(line for line in lines if line)[:600]


def symbols(path, text, lang):
    """-> list of symbol dicts like extract.regex_symbols, or None when no parser applies."""
    parser, name = _parser(lang, path)
    if parser is None:
        return None
    if name not in NODES:
        return _generic_symbols(name, text)
    table = NODES[name]
    tree = parser.parse(text.encode("utf-8", errors="replace"))
    lines = text.splitlines()
    out = []

    def visit(node, prefix, inside, depth):
        for child in node.children:
            scope, within = prefix, inside
            kind = table.get(child.type) if child.is_named else None
            declared = _name(child, kind) if kind else None
            if declared:
                kind = _kind(child, kind)
                if kind == "variable":
                    kind = "function"
                receiver = child.child_by_field_name("receiver")
                if receiver is not None:          # Go: func (d *DB) Close()
                    owner = _text(receiver).strip("()").split()[-1].lstrip("*").split("[")[0]
                    qual, top = f"{owner}.{declared}", False
                    kind = "method"
                else:
                    qual = f"{prefix}.{declared}" if prefix else declared
                    top = not inside and "." not in declared
                    if kind == "function" and not top:
                        kind = "method"
                if kind == "impl":
                    scope, within = declared, True
                elif kind in TRANSPARENT:
                    pass
                else:
                    start, end = child.start_point[0] + 1, child.end_point[0] + 1
                    out.append({"name": declared.split(".")[-1], "qualname": qual, "kind": kind,
                                "startLine": start, "endLine": end, "top": top,
                                "signature": (lines[start - 1].strip() if start <= len(lines) else "")[:300],
                                "doc": _doc(child), "body": "\n".join(lines[start - 1:end])})
                    if kind in CONTAINERS:
                        scope, within = qual, within or kind != "module"
                    elif kind in ("function", "method"):
                        continue                   # local functions and classes are not indexed
            if depth < 40:
                visit(child, scope, within, depth + 1)

    visit(tree.root_node, "", False, 0)
    return out


def _generic_symbols(name, text):
    """Symbols from the pack's own structure extraction, for languages without a table."""
    try:
        result = _pack.process(text, _pack.ProcessConfig(language=name))
    except Exception:
        return None
    lines = text.splitlines()
    out = []

    def visit(items, prefix, inside):
        for item in items:
            kind = _GENERIC_KINDS.get(str(item.kind).split(".")[-1].lower())
            declared = (item.name or "").strip()
            scope, within = prefix, inside
            if kind and declared and item.span is not None:
                qual = f"{prefix}.{declared}" if prefix and not declared.startswith(prefix + ".") else declared
                if kind == "impl":
                    scope, within = declared, True
                elif kind != "namespace":
                    start, end = item.span.start_line + 1, item.span.end_line + 1
                    if kind == "function" and inside:
                        kind = "method"
                    doc, j = [], start - 2
                    while 0 <= j < len(lines) and len(doc) < 8 and lines[j].lstrip()[:2] in ("//", "/*", "--", "##", "# ", "* ", "%%"):
                        doc.insert(0, lines[j].strip().lstrip("/*#-%! ").rstrip("*/ "))
                        j -= 1
                    out.append({"name": declared.split(".")[-1], "qualname": qual, "kind": kind,
                                "startLine": start, "endLine": max(end, start), "top": not inside,
                                "signature": (item.signature or (lines[start - 1] if start <= len(lines) else "")).strip()[:300],
                                "doc": " ".join(d for d in doc if d)[:600],
                                "body": "\n".join(lines[start - 1:end])})
                    if kind in CONTAINERS:
                        scope, within = qual, within or kind != "module"
            visit(item.children, scope, within)

    visit(result.structure, "", False)
    return out or None


def code_only(path, text, lang):
    """The source with comments and string literals blanked out, or None without a parser."""
    parser, _ = _parser(lang, path)
    if parser is None:
        return None
    data = bytearray(text.encode("utf-8", errors="replace"))
    stack = [parser.parse(bytes(data)).root_node]
    while stack:
        node = stack.pop()
        kind = node.type
        if "comment" in kind or kind in ("string", "string_literal", "raw_string_literal", "char_literal",
                                         "character_literal", "interpreted_string_literal", "template_string",
                                         "encapsed_string", "heredoc", "nowdoc", "line_string_literal",
                                         "multi_line_string_literal", "string_content", "string_fragment",
                                         "verbatim_string_literal", "interpolated_string_expression",
                                         "rune_literal", "text_block"):
            for i in range(node.start_byte, node.end_byte):
                if data[i] not in (10, 13):
                    data[i] = 32
        else:
            stack.extend(node.children)
    return data.decode("utf-8", errors="replace")


def mode():
    """What the stored facts were built with; a change means every file is analysed again."""
    return "tree-sitter 1" if available() else "regex"


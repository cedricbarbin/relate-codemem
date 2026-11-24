"""Syntax-tree extraction with tree-sitter, when the optional `parse` extra is installed.

Without it every function here reports "not available" and the callers keep their
regular-expression path. With it, symbols get exact extents and nesting (a method is
`Type.method`), and comments and string literals are told apart from code exactly.

The package provides the parsers; what a class or a method looks like in each grammar
is described by the tables below, so a language is supported once it has an entry.
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

_parsers = {}


def enabled():
    return (config.setting("PARSER")[0] or "auto").lower() not in ("regex", "off", "none")


def _parser(lang, path=""):
    name = "tsx" if lang == "typescript" and path.endswith("x") else lang
    if name not in NODES or not enabled() or Path(path).suffix.lower() in _SKIP_FILES:
        return None, name
    if name not in _parsers:
        try:
            from tree_sitter_language_pack import get_parser
            _parsers[name] = get_parser(name)
        except Exception:        # the extra is absent, or this grammar is not in the installed pack
            _parsers[name] = None
    return _parsers[name], name


def available(lang="java"):
    return _parser(lang)[0] is not None


def describe():
    if not enabled():
        return "regular expressions (tree-sitter turned off)"
    if not available():
        return "regular expressions (install the `parse` extra for tree-sitter)"
    return "tree-sitter: " + ", ".join(sorted(n for n in NODES if _parser(n)[0] is not None))


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
    return "tree-sitter" if available() else "regex"


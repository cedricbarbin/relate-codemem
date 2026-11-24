"""Read-only MCP server over stdio (newline-delimited JSON-RPC 2.0).

Besides raw Cypher it offers the standard questions as tools, so that a model can ask
"what does this class call" or "what is the impact of changing this method" without
writing a query, and as prompts, which Claude Code lists as slash commands.

Nothing here writes to the store: the named tools run fixed read queries, and
`read_cypher` opens the store with the engine's own read-only mode."""
import contextlib
import io
import json
import sys

from . import __version__, queries, recall, store

PROTOCOL = "2025-03-26"


def _captured(function, *args, **kwargs):
    """The recall functions print; a tool returns text."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        function(*args, **kwargs)
    return buffer.getvalue().strip() or "(nothing found)"


def _read_cypher(statement, params=None):
    rows = store.run(statement, params or None, read_only=True, strict=True, timeout=60)[-1]
    if len(rows) > 200:
        rows = rows[:200] + [{"truncated": "more than 200 rows: add a LIMIT or narrow the query"}]
    return json.dumps(rows, ensure_ascii=False, default=str, indent=1)


def _name(description):
    return {"type": "object", "required": ["name"], "properties": {"name": {"type": "string", "description": description}}}


SYMBOL = ("A symbol name: a simple name (`total`, `Invoice`) or a qualified one (`Invoice.total`). "
          "A simple name that several symbols share returns all of them.")

# name -> (description, input schema, handler)
TOOLS = {
    "search": (
        "Search the project memory: code symbols, documentation, decisions, meeting notes and past sessions, "
        "by exact words and by meaning. Start here when you do not know where something is. "
        "Example: {\"query\": \"how are tokens refreshed\", \"kind\": \"code\"}.",
        {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"},
            "kind": {"type": "string", "enum": sorted(recall.KINDS), "description": "Restrict to one kind of entry."},
            "k": {"type": "integer", "default": 10, "description": "Number of results."}}},
        lambda a: _captured(recall.search, a["query"], k=int(a.get("k", 10)), kind=a.get("kind"))),
    "recall": (
        "Open decisions, notes, meetings and recent sessions of the project. Use it when starting work on an area, "
        "or for \"what did we decide\", \"what happened last time\".",
        {"type": "object", "properties": {}},
        lambda a: _captured(recall.recall)),
    "file_info": (
        "Everything recorded about one file: revisions, symbols, the files it depends on and that depend on it, "
        "its imports with their scope (project, standard library, external), decisions and sessions that touched it. "
        "Example: {\"path\": \"src/api/client.py\"}.",
        {"type": "object", "required": ["path"], "properties": {"path": {"type": "string", "description": "Project-relative path."}}},
        lambda a: _captured(recall.show_file, a["path"])),
    "symbol_info": (
        "Where a symbol is defined, its supertypes and subtypes, what it redefines, what it calls and what calls it "
        "directly. Example: {\"name\": \"Invoice.total\"}.",
        _name(SYMBOL), lambda a: _captured(recall.show_symbol, a["name"])),
    "class_calls": (
        "Answers \"which methods does class X call?\": every method or type that the class reaches through any of "
        "its methods, inside the project (with how sure each link is) and outside it (libraries). Also works for "
        "one method. Example: {\"name\": \"OrderService\"}.",
        _name(SYMBOL), lambda a: queries.calls_of(a["name"])),
    "implementations": (
        "Answers \"which implementations of interface Y exist?\": the types that extend or implement a type, at any "
        "depth; for a method, the methods that redefine it. Example: {\"name\": \"Repository\"} or "
        "{\"name\": \"Repository.save\"}.",
        _name(SYMBOL), lambda a: queries.implementations(a["name"])),
    "impact": (
        "Answers \"what would be the impact of changing method M of class C?\": the calling methods, their classes "
        "and files, directly and through other methods up to `depth` calls away. Callers that reach the method "
        "through a supertype or an interface are included; for a class, its subtypes too. "
        "Example: {\"name\": \"Customer.save\", \"depth\": 3}.",
        {"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string", "description": SYMBOL},
            "depth": {"type": "integer", "default": 3, "minimum": 1, "maximum": 6,
                      "description": "How many calls away to follow callers."}}},
        lambda a: queries.impact(a["name"], a.get("depth", 3))),
    "get_schema": (
        "The graph model: labels, key properties and relationships. Read it before writing Cypher for read_cypher.",
        {"type": "object", "properties": {}},
        lambda a: recall.MODEL_DOC),
    "read_cypher": (
        "Run one read-only Cypher statement on the project memory graph, for questions the other tools do not "
        "cover. Always add a LIMIT. Values go in `params` and are read as $p.<key>. Example: {\"statement\": "
        "\"MATCH (s:Symbol)-[:CALLS]->(e:External {scope: 'external'}) RETURN e.root AS library, count(*) AS calls "
        "ORDER BY calls DESC LIMIT 20\"}.",
        {"type": "object", "required": ["statement"], "properties": {
            "statement": {"type": "string"},
            "params": {"type": "object", "description": "Values, read in the statement as $p.<key>."}}},
        lambda a: _read_cypher(a["statement"], a.get("params"))),
}

# name -> (description, arguments, text of the user message)
PROMPTS = {
    "ask": ("Answer a question from the project memory before reading files",
            [("question", "The question", True)],
            "Answer this question about the project using the relate-codemem MCP tools first (search, recall, "
            "file_info, symbol_info, class_calls, implementations, impact, read_cypher). Check what they return "
            "against the current files before relying on it, and say when the memory and the code disagree.\n\n"
            "Question: {question}"),
    "class-calls": ("Which methods does a class call?", [("name", "Class or method name", True)],
                    "Use the relate-codemem `class_calls` tool with name `{name}`, then summarise what it calls "
                    "inside the project and which libraries it reaches."),
    "implementations": ("Which implementations of an interface exist?", [("name", "Interface, class or method name", True)],
                        "Use the relate-codemem `implementations` tool with name `{name}` and list what it returns, "
                        "grouped by level."),
    "impact": ("What is the impact of changing a method or a class?", [("name", "Symbol name, e.g. Customer.save", True)],
               "Use the relate-codemem `impact` tool with name `{name}`. Report the calling methods, the classes and "
               "the files affected, starting with direct callers, and point out which links are uncertain."),
}


def _result(text, error=False):
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(message):
    """-> the response to one JSON-RPC message, or None for a notification."""
    method, params, ident = message.get("method"), message.get("params") or {}, message.get("id")
    if ident is None:
        return None
    try:
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                      "capabilities": {"tools": {}, "prompts": {}},
                      "serverInfo": {"name": "relate-codemem", "version": __version__},
                      "instructions": "Read-only access to the project memory graph. Prefer the named tools; "
                                      "use read_cypher (after get_schema) for anything else."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "description": description, "inputSchema": schema,
                                 "annotations": {"readOnlyHint": True}}
                                for name, (description, schema, _) in TOOLS.items()]}
        elif method == "tools/call":
            tool = TOOLS.get(params.get("name"))
            if tool is None:
                raise KeyError(f"unknown tool: {params.get('name')}")
            try:
                result = _result(tool[2](params.get("arguments") or {}))
            except KeyError as exc:
                result = _result(f"invalid params: missing {exc}", error=True)
            except Exception as exc:        # a failed query is an answer for the model, not a protocol error
                result = _result(f"{type(exc).__name__}: {exc}", error=True)
        elif method == "prompts/list":
            result = {"prompts": [{"name": name, "description": description,
                                   "arguments": [{"name": a, "description": d, "required": r} for a, d, r in arguments]}
                                  for name, (description, arguments, _) in PROMPTS.items()]}
        elif method == "prompts/get":
            prompt = PROMPTS.get(params.get("name"))
            if prompt is None:
                raise KeyError(f"unknown prompt: {params.get('name')}")
            values = {a: (params.get("arguments") or {}).get(a, "") for a, _, _ in prompt[1]}
            result = {"description": prompt[0],
                      "messages": [{"role": "user", "content": {"type": "text", "text": prompt[2].format(**values)}}]}
        else:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"method not found: {method}"}}
    except KeyError as exc:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": str(exc).strip("'\"")}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def serve(stdin=None, stdout=None):
    store.ensure_store()
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(message) if isinstance(message, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()
    return 0

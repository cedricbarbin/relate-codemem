"""Command line: `relate-codemem <command>` (alias `rcm`)."""
import argparse
import json
import subprocess
import sys
import webbrowser

from . import __version__, config, embeddings, events, indexer, mcp, project, queries, recall, store


def _embedding_options(parser):
    parser.add_argument("--embeddings", choices=config.PROVIDERS, help="embedding provider")
    parser.add_argument("--model", help="embedding model name")
    parser.add_argument("--url", help="base URL (ollama, openai-compatible)")
    parser.add_argument("--api-key-env", help="name of the environment variable holding the API key")
    parser.add_argument("--dimensions", type=int, help="vector size, for models that accept it")


def _overrides(args):
    return project.embedding_overrides(args.embeddings, args.model, args.url, args.api_key_env, args.dimensions)


def playground(bolt, http, open_browser):
    """Serve the project store read-only on the loopback interface until interrupted.

    relate has no local playground of its own (the one on its site runs an in-browser
    database); what it offers for a store on disk is the Bolt and HTTP surfaces, which
    Neo4j Browser can connect to."""
    store.ensure_store()
    link = f"https://browser.neo4j.io/?connectURL=bolt://relate@127.0.0.1:{bolt}"
    print(f"""Serving the project memory read-only (it follows new commits). Stop with Ctrl-C.

  Bolt      bolt://127.0.0.1:{bolt}
  HTTP API  http://127.0.0.1:{http}
  user      relate
  password  the token in {store.db()}.auth-token (created on the first run)

Explore it in Neo4j Browser, which runs in your web browser and talks to this machine only:
  {link}
Try:  MATCH (a:Symbol)-[r:CALLS]->(b:Symbol) RETURN a, r, b LIMIT 100
""", flush=True)
    if open_browser:
        webbrowser.open(link)
    try:
        return subprocess.call([store.relate_bin(), "serve", "--database", str(store.db()), "--read-only=live",
                                "--bolt", str(bolt), "--http", str(http)],
                               stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, **config.NO_WINDOW)
    except KeyboardInterrupt:
        return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="relate-codemem", description=__doc__.splitlines()[0] + "\n\n"
                                 "Project memory graph for Claude Code on the relate embedded database.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"relate-codemem {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    a = sub.add_parser("setup", help="machine setup: default embedding provider, global Claude Code skill")
    _embedding_options(a)
    a.add_argument("--no-skill", action="store_true")
    sub.add_parser("doctor", help="check the installation")
    a = sub.add_parser("init", help="set up a project (hooks, settings, CLAUDE.md, docs dirs) and index it")
    a.add_argument("path", nargs="?", default=".")
    _embedding_options(a)
    a.add_argument("--no-index", action="store_true", help="skip the initial analysis")
    a = sub.add_parser("index", help="analyse the project: hashes, revisions, symbols, docs, embeddings")
    a.add_argument("paths", nargs="*")
    a.add_argument("--full", action="store_true", help="re-extract every file (no new revision)")
    a.add_argument("--rebuild", action="store_true",
                   help="delete symbols, chunks, dependencies, calls and their embeddings, then analyse "
                        "everything again; revisions, sessions, decisions and notes are kept")
    a.add_argument("--no-embed", action="store_true")
    a = sub.add_parser("embed", help="compute missing embeddings")
    a.add_argument("--reset", action="store_true", help="rebuild all vectors")
    a.add_argument("--quiet", action="store_true")
    a = sub.add_parser("search", help="hybrid search (full-text + semantic)")
    a.add_argument("query", nargs="+")
    a.add_argument("-k", type=int, default=10)
    a.add_argument("--kind", choices=sorted(recall.KINDS))
    a.add_argument("--all", action="store_true", help="include superseded entries and deleted files")
    a.add_argument("--no-semantic", action="store_true")
    sub.add_parser("recall", help="open decisions, notes, meetings, recent sessions")
    a = sub.add_parser("file", help="revisions, symbols, dependencies, decisions and sessions of a file")
    a.add_argument("path")
    a = sub.add_parser("symbol", help="where a symbol is defined, what it calls, what calls it")
    a.add_argument("name")
    a = sub.add_parser("calls", help="what a class (through all its methods) or a method calls")
    a.add_argument("name")
    a = sub.add_parser("implementations", help="subtypes of a type, or redefinitions of a method")
    a.add_argument("name")
    a = sub.add_parser("impact", help="callers of a method or class, their classes and files")
    a.add_argument("name")
    a.add_argument("--depth", type=int, default=3, help="how many calls away to follow callers (default 3)")
    a = sub.add_parser("path", help="the shortest chain of calls from one symbol to another")
    a.add_argument("source")
    a.add_argument("target")
    a = sub.add_parser("uses", help="the libraries the project uses, or where one of them is used")
    a.add_argument("library", nargs="?")
    a = sub.add_parser("why", help="decisions, notes, documents and sessions that explain a file")
    a.add_argument("path")
    a = sub.add_parser("tests", help="the tests that reach a method or a class")
    a.add_argument("name")
    a.add_argument("--depth", type=int, default=4, help="how many calls away to look (default 4)")
    a = sub.add_parser("diff-impact", help="what the uncommitted changes, or the changes since a revision, may affect")
    a.add_argument("ref", nargs="?", default="HEAD", help="git revision to compare the working tree with (default HEAD)")
    a.add_argument("--depth", type=int, default=3)
    a = sub.add_parser("playground", help="serve the store read-only on this machine, to explore it in Neo4j Browser")
    a.add_argument("--bolt", type=int, default=7688, help="Bolt port (default 7688)")
    a.add_argument("--http", type=int, default=7475, help="HTTP query API port (default 7475)")
    a.add_argument("--open", action="store_true", help="open Neo4j Browser in the default web browser")
    a = sub.add_parser("query", help="read-only Cypher")
    a.add_argument("cypher")
    sub.add_parser("schema", help="the graph model")
    a = sub.add_parser("decide", help="record a decision")
    a.add_argument("--title", required=True)
    a.add_argument("--text", required=True)
    a.add_argument("--rationale")
    a.add_argument("--slot")
    a.add_argument("--files")
    a.add_argument("--supersedes")
    a = sub.add_parser("note", help="record a note")
    a.add_argument("text", nargs="+")
    a.add_argument("--title")
    a.add_argument("--slot")
    a.add_argument("--files")
    a.add_argument("--supersedes")
    sub.add_parser("status", help="node counts, spool, embedding coverage")
    sub.add_parser("config", help="effective configuration and where each value comes from")
    sub.add_parser("flush", help="write spooled hook events now")
    sub.add_parser("hook", help="Claude Code hook entry point (payload on stdin)")
    sub.add_parser("mcp", help="read-only MCP server on the project store (stdio)")
    return ap


def show_config():
    ecfg = config.embedding_config()
    print(f"project root:    {config.project_root()}")
    print(f"store:           {store.home()}")
    print(f"project options: {config.project_config_path()}"
          + ("" if config.project_config_path().exists() else "  (absent, defaults apply)"))
    print(f"user config:     {config.global_config_path()}"
          + ("" if config.global_config_path().exists() else "  (absent, defaults apply)"))
    print(f"embeddings:      {ecfg['provider']}  (from {ecfg['source']})")
    for key in ("model", "url", "api_key_env", "dimensions", "min_similarity"):
        if ecfg.get(key) is not None:
            print(f"  {key + ':':<15}{ecfg[key]}")
    marker = embeddings.read_marker()
    if marker:
        print(f"store vectors:   {marker['provider']}:{marker['model']} ({marker['dimensions']} dimensions)")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)

    if args.cmd == "hook":
        try:
            events.hook()
        except Exception as exc:                 # a hook must never break the session
            store.log(f"hook error: {type(exc).__name__}: {exc}")
        return 0
    try:
        if args.cmd == "setup":
            return project.setup(_overrides(args), skill=not args.no_skill)
        if args.cmd == "doctor":
            return project.doctor()
        if args.cmd == "init":
            project.init_project(args.path, _overrides(args))
            if not args.no_index:
                print("\ninitial analysis:")
                indexer.index()
                embeddings.embed_pass()
            print("\nrestart Claude Code in this project to load the hooks and the MCP server.")
        elif args.cmd == "index":
            indexer.index(args.paths or None, full=args.full, rebuild=args.rebuild)
            if not args.no_embed:
                embeddings.embed_pass()
        elif args.cmd == "embed":
            embeddings.embed_pass(quiet=args.quiet, reset=args.reset)
        elif args.cmd == "search":
            recall.search(" ".join(args.query), k=args.k, kind=args.kind, include_closed=args.all,
                          semantic=not args.no_semantic)
        elif args.cmd == "recall":
            recall.recall()
        elif args.cmd == "file":
            recall.show_file(args.path)
        elif args.cmd == "symbol":
            recall.show_symbol(args.name)
        elif args.cmd == "query":
            for row in store.read(args.cypher):
                print(json.dumps(row, ensure_ascii=False))
        elif args.cmd == "schema":
            print(recall.MODEL_DOC)
        elif args.cmd == "decide":
            recall.remember("Decision", args.title, args.text, args.rationale, args.slot, args.files,
                            args.supersedes)
        elif args.cmd == "note":
            text = " ".join(args.text)
            recall.remember("Note", args.title or recall.one_line(text, 100), text, None, args.slot,
                            args.files, args.supersedes)
        elif args.cmd == "status":
            recall.status()
        elif args.cmd == "config":
            show_config()
        elif args.cmd == "flush":
            print(f"flushed {events.flush()} event(s)")
        elif args.cmd == "mcp":
            return mcp.serve()
        elif args.cmd == "calls":
            print(queries.calls_of(args.name))
        elif args.cmd == "implementations":
            print(queries.implementations(args.name))
        elif args.cmd == "impact":
            print(queries.impact(args.name, args.depth))
        elif args.cmd == "path":
            print(queries.call_path(args.source, args.target))
        elif args.cmd == "uses":
            print(queries.library_usage(args.library))
        elif args.cmd == "why":
            print(queries.why(args.path))
        elif args.cmd == "tests":
            print(queries.tests_for(args.name, args.depth))
        elif args.cmd == "diff-impact":
            print(queries.diff_impact(args.ref, args.depth))
        elif args.cmd == "playground":
            return playground(args.bolt, args.http, args.open)
    except (store.RelateError, config.ConfigError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

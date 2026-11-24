# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

## 0.2.0 - 2026-11-24

The code analysis was rebuilt in this version. After upgrading, run
`relate-codemem setup` (new skills) and restart Claude Code (new MCP tools);
the next `index` analyses every file again without creating revisions. Add
the `parse` extra to get the call graph.

### Added

- Optional `parse` extra (Python 3.11 or later): code is parsed with
  tree-sitter. Thirteen languages (Java, Kotlin, Scala, C#, Go, Rust,
  JavaScript, TypeScript, PHP, Ruby, C, C++, Swift) get exact extents and
  nested names (`Invoice.total`); other languages known to
  `tree-sitter-language-pack` and without patterns (Zig, Haskell, OCaml...)
  are now indexed through its generic extraction. Comments and strings are
  excluded exactly from dependency analysis. Grammars are downloaded on first
  use into the user cache. Regular expressions remain the fallback;
  `RELATE_CODEMEM_PARSER=regex` forces them. The installers add the extra,
  and `doctor` reports the active parser.

- Call graph (with the `parse` extra): `(Symbol)-[:CALLS]->(Symbol)`,
  resolved through what the calling file can see, the receiver's written
  type and the argument count. Beyond direct dependencies only the type
  hierarchy is followed, to any depth, stopping at the first declaration
  found. Each link carries `via`, `distance`, `candidates` and `inherited`.
  `(Symbol)-[:EXTENDS]->(Symbol)` and `(Symbol)-[:OVERRIDES]->(Symbol)`
  describe the hierarchy, so the implementations a call on an interface may
  run can be queried. `relate-codemem symbol NAME` lists calls and
  callers. A full re-analysis clears the calls it no longer finds.

- `index --rebuild` deletes everything derived from file contents (symbols,
  chunks, modules, dependencies, calls, embeddings) and analyses the project
  again; revisions, sessions, decisions and notes are kept.

- Internal and external relationships are told apart: `IMPORTS` carries a
  `scope` (`internal`, `stdlib`, `external`, `unresolved`) and `Module` a
  `root` library; calls and supertypes proven external by an import are
  linked to `External` nodes. `file` and `symbol` show them.

- Standard questions as commands and MCP tools: `calls` (what a class or a
  method calls), `implementations` (subtypes, redefinitions) and `impact`
  (callers, classes and files affected by a change), `path` (chain of calls
  between two symbols), `uses` (libraries, or where one is used), `why`
  (decisions, documents and sessions behind a file), `tests` (tests that
  reach a symbol) and `diff-impact` (what the changes since a git revision
  affect).
- The MCP server is now part of this package: it adds those tools, `search`,
  `recall`, `file_info` and `symbol_info` to `read_cypher` and `get_schema`,
  and publishes the questions as MCP prompts. It stays read-only.
- `playground` serves the store read-only on this machine for Neo4j Browser,
  with an optional chosen password (`--password` or
  `RELATE_CODEMEM_PLAYGROUND_PASSWORD`).
- `sharedb` lets git track the store, after a warning about personal data
  and a confirmation; `sharedb --off` undoes it.
- Claude Code skills `/relate-codemem-ask`, `/relate-codemem-impact`,
  `/relate-codemem-playground` and `/relate-codemem-sharedb`, installed by
  `setup`.
- Project links (repository, documentation, issues, changelog) in the package
  metadata, and absolute links in the README, for the PyPI page.

- Terraform: blocks are symbols (`aws_s3_bucket.logs`, `var.region`,
  `module.vpc`...), files of a directory depend on each other through the
  names they use, module sources and providers are resolved, and references
  between blocks feed `impact`.
- Ansible: YAML files recognised as playbooks, roles or variable files are
  indexed as code, with plays, tasks, handlers and variables as symbols,
  include, role, template and file dependencies, `notify` links to handlers
  and task modules as external targets.

### Changed

- Python: `from package import submodule` now creates a dependency on the
  submodule's file.
- File dependencies are resolved from declared symbols instead of guessed
  paths: same-package references without an import (Java, Kotlin, Scala, C#,
  PHP, Go), wildcard imports, fully qualified names, types in any source root,
  Go package imports and Rust `use crate::...`. `DEPENDS_ON` carries a `via`
  property (`import`, `wildcard`, `qualified`, `package`).
- Dependencies of unchanged files follow the rest of the project: they are
  resolved again from stored facts whenever a file is added, changed or
  removed. Existing stores are analysed again in full on the first run.

## 0.1.1 - 2026-11-24

### Fixed

- On Windows, console windows no longer flash on every hook and at start-up:
  relate, git and the background embedding pass are started without a window.

## 0.1.0 - 2026-11-24

First public version. Early development: interfaces may change before 1.0.

### Added

- Project memory graph on a relate directory store in `.claude/relate/`.
- Incremental code index with sha256 hashes, file revisions and per-symbol
  change tracking; Python through `ast`, other languages through patterns.
- Documentation, ADR and meeting transcript indexing from dedicated
  directories.
- Claude Code hooks recording sessions, prompts, replies, edits, reads and
  shell commands.
- Decisions and notes with slot-based supersession.
- Hybrid search: full-text and vector lists fused by reciprocal rank.
- Embedding providers: `local` (fastembed), `ollama`, `openai`-compatible,
  `none`; machine default chosen at install, per-project override in
  `.claude/settings.local.json`.
- `setup`, `init`, `doctor` and `config` commands; installers for macOS,
  Linux and Windows; global Claude Code skill `/relate-codemem-init`.
- Read-only MCP server on the project store.

### Fixed

- Files with CRLF line endings are normalised before hashing and parsing.
- A read that hits an abnormal exit of the relate binary (seen on Windows) is retried.
- On Windows, reads open the store normally under the writer lock instead of
  read-only, which crashed or hung intermittently with relate 1.2.0.

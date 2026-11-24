# Architecture

relate-codemem is a thin layer between Claude Code and a relate store:

```
Claude Code ── hooks ──▶ relate-codemem hook ──▶ spool ──▶ ┐
            ── Bash ───▶ relate-codemem search / recall ◀──┤  relate store
            ── MCP ────▶ relate serve --read-only=live ◀──┘  .claude/relate/db
terminal    ───────────▶ relate-codemem index / embed ────▶
```

The Python package has no required dependency and never links the database:
it drives the `relate` binary, one short-lived process per operation, passing
parameters through a JSON file. See
[decisions/0001](decisions/0001-drive-relate-through-its-cli.md).

## Modules

| Module | Role |
| --- | --- |
| `config` | paths, project root detection, layered settings |
| `store` | the `relate` runner, the write lock, the schema |
| `extract` | pure functions: file classification, symbols, imports, chunks, ADR and meeting metadata |
| `indexer` | incremental indexing with hashes and revisions |
| `embeddings` | providers, the vector index, the embedding pass |
| `events` | hook entry point, spool, flush |
| `recall` | search, recall, file and symbol views, decisions and notes |
| `project` | `setup`, `init`, `doctor` |
| `cli` | argument parsing |

## Graph model

`relate-codemem schema` prints this from the command line.

| Label | Key | Main properties |
| --- | --- | --- |
| `File` | `path` | `kind` (code, doc, decision, meeting, config), `lang`, `hash`, `revision`, `lines`, `deleted` |
| `FileRevision` | `id` = `path@rev` | `rev`, `hash`, `at`, `gitCommit`, `gitBranch`, `session`, `deleted` |
| `Symbol` | `id` = `path::qualname` | `name`, `kind`, `signature`, `startLine`, `endLine`, `hash`, `sinceRev`, `changedRev` |
| `Chunk` | `id` = `path#ord` | `title` (heading trail), `text`, `startLine`, `kind` |
| `Module` | `name` | an import that is not a project file |
| `Decision` | `id` | `title`, `text`, `status`, `date`, `slot`, `source` (file or session), `recordedAt`, `closedAt` |
| `Note` | `id` | `title`, `text`, `slot`, `date`, `recordedAt`, `closedAt` |
| `Meeting` | `id` = path | `title`, `date` |
| `Person` | `name` | |
| `Session` | `id` | `startedAt`, `lastAt`, `endedAt`, `branch`, `gitCommit`, `source` |
| `Prompt`, `Reply` | `id` | `title`, `text`, `at`, `session` |
| `ToolCall` | `id` | `tool`, `summary`, `path`, `at` |

```
(FileRevision)-[:REVISION_OF]->(File)        (FileRevision)-[:PREVIOUS]->(FileRevision)
(Symbol)-[:DEFINED_IN]->(File)               (Chunk)-[:PART_OF]->(File)
(File)-[:DEPENDS_ON {via}]->(File)           (File)-[:IMPORTS]->(Module)
(Decision)-[:DOCUMENTED_IN]->(File)          (Decision|Note)-[:AFFECTS]->(File)
(Decision|Note)-[:SUPERSEDES]->(same label)  (Decision|Note)-[:MADE_IN]->(Session)
(Meeting)-[:RECORDED_IN]->(File)             (Person)-[:ATTENDED]->(Meeting)
(Prompt|Reply|ToolCall)-[:IN_SESSION]->(Session)   (Reply)-[:ANSWERS]->(Prompt)
(ToolCall)-[:MODIFIED]->(File)               (Session)-[:READ {count}]->(File)
(Session)-[:PRODUCED]->(FileRevision)
```

Searchable nodes (`File`, `Symbol`, `Chunk`, `Decision`, `Note`, `Meeting`,
`Prompt`, `Reply`) carry a second label, `Searchable`, with `title`, `text`
and `terms`. One full-text index (`memory_text`) and one vector index
(`memory_vec`) cover that label. An entry is current while `closedAt` is null.

## Hashes and revisions

On each indexing pass:

1. A local cache (`state.json`: mtime, size, hash) skips files that did not
   change, so an unchanged file costs one `stat()`.
2. When a file's sha256 differs from the one in the graph, `File.revision` is
   incremented and a `FileRevision` is created and chained to the previous one
   with `PREVIOUS`. It records the time, the git commit and branch, and the
   session that produced it.
3. Symbols are extracted again. Each keeps `sinceRev`, the revision it first
   appeared in, and `changedRev`, the last revision whose body hash differs.
   Symbols that disappeared are removed.
4. A deleted file gets a final revision flagged `deleted` and stays in the
   graph; if it comes back, numbering continues.

`index --full` re-extracts every file without creating revisions, for use
after an upgrade that changes extraction.

### How code is parsed

Python is parsed with the standard `ast` module. For the other languages
there are three sources, in this order:

1. **tree-sitter with a table**, when the `parse` extra is installed (the
   installers add it; Python 3.11 or later). Java, Kotlin, Scala, C#, Go,
   Rust, JavaScript, TypeScript and TSX, PHP, Ruby, C, C++ and Swift are
   parsed into syntax trees and read through tables that say what a
   declaration looks like in each grammar. A symbol has its exact first and
   last line, a member is named after its container (`Invoice.total`,
   `DB.Close`), and local and anonymous declarations are left out.
2. **regular expressions**, for the languages that have patterns (Elixir,
   Lua, SQL, shell, Dart...) and for every language when the extra is
   absent: one pattern per declaration line, a symbol ending where the next
   one starts, no nesting.
3. **tree-sitter without a table**, for any other language the pack knows
   (Zig, Haskell, OCaml, Julia...): the pack's own structure extraction. It
   is coarser (some declarations are missed, depending on the language) but
   it makes those files and their main symbols searchable. Files of such
   languages are only indexed when the extra is installed.

With the extra, comments and string literals are excluded exactly, in every
language, when names are collected for dependencies.

`relate-codemem doctor` shows which path is active. The pack knows several
hundred languages and downloads a grammar the first time one is met, into
`~/.cache/relate-codemem/tree-sitter` (`%LOCALAPPDATA%\relate-codemem\cache`
on Windows): about 26 MB once, then a few MB per language. This is native
code fetched from the pack's release server and checked against its manifest.
A hook can trigger it when a file of a new language is first edited; without
network the file falls back to patterns. `RELATE_CODEMEM_PARSER=regex` turns
tree-sitter off. Switching path changes symbol names and extents, so every
file is analysed again on the next run: no file revision is created, but
renamed symbols restart their own history and their embeddings are computed
again.

### Dependencies between files

A `DEPENDS_ON` relationship is resolved from declared symbols, not only from
import lines. Each code file is reduced to a few facts: its package or
namespace, the names it declares, its imports and the names its code uses
(comments and string literals excluded). Its `via` property says where the
link comes from:

| `via` | Meaning | Languages |
| --- | --- | --- |
| `import` | an import that names the file, or a type or function it declares | all |
| `wildcard` | `import a.b.*`, `using A.B;`: a declared name of that package is used | Java, Kotlin, Scala, C# |
| `qualified` | a fully qualified name written in the code | Java, Kotlin, Scala, C#, PHP |
| `package` | a name of the same package, used without any import | Java, Kotlin, Scala, C#, PHP, Go |

Types are found by their qualified name wherever the file lives, so
multi-module layouts need no configuration. Other languages (Python,
JavaScript and TypeScript, Rust, C, Ruby...) keep resolving imports to paths.

The facts are stored in `.claude/relate/deps.json` and edges are resolved over
all of them on every run. An unchanged file is not read again and gets no new
revision, yet it gains or loses a dependency when another file starts or stops
declaring a name it uses. A store that has no facts yet is analysed again in
full on the next run.

The analysis is lexical. A name used in the code links to the file that
declares it in a visible package; there is no type inference, so `x.close()`
says nothing about the class of `x`.

Decisions and notes follow the same no-delete rule on the knowledge side: an
entry written with the `--slot` of an existing current entry, or with
`--supersedes <id>`, closes the old one (`closedAt`, status `superseded`) and
links to it with `SUPERSEDES`. An ADR file whose status starts with
`superseded`, `deprecated` or `rejected` is closed the same way.

## Hooks

| Hook | Effect |
| --- | --- |
| `SessionStart` | creates the session, catches up with changes made outside Claude Code, prints one status line |
| `UserPromptSubmit` | stores the prompt |
| `PostToolUse` on `Edit`, `Write`, `MultiEdit`, `NotebookEdit` | re-indexes the file and links the new revision to the session |
| `PostToolUse` on `Read`, `Bash` | spools the file read or the command; written at the next flush |
| `Stop` | stores the final reply, re-indexes whatever shell commands changed, starts the embedding pass in the background |
| `PreCompact`, `SessionEnd` | flushes, re-indexes, closes the session |

A hook never fails the session: any error is written to
`.claude/relate/memory.log` and the hook exits 0.

## Concurrency

relate accepts one writer process per store. Hooks can fire in parallel, so:

- every hook appends its event to a spool file under a short lock and returns;
- writes to the store are serialised by a file lock (`write.lock`), with a
  retry when relate reports the store busy;
- every node is written with `MERGE` on its key, so replaying a spool batch
  changes nothing;
- readers (search, the MCP server) open the store read-only and run beside the
  writer.

The embedding pass runs in a detached process guarded by its own lock, so
hooks stay fast and passes never pile up.

## Search

`search` runs the full-text query and, when vectors exist for the configured
model, a vector query; each list is filtered (kind, superseded entries,
deleted files) and the two are fused by reciprocal rank, `1 / (60 + rank)`
summed over the lists. Identifiers are split into words at indexing time and
at query time (`parseConfig` becomes `parse`, `config`, `parseconfig`), and
common English and French stop words are dropped from the full-text query.

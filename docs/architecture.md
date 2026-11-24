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
(File)-[:DEPENDS_ON]->(File)                 (File)-[:IMPORTS]->(Module)
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

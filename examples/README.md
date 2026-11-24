# Example: a small project, start to finish

[demo-project/](demo-project) is a toy repository with Python, TypeScript and
Go sources, an architecture document, one ADR and one meeting note. The output
below is real, produced with local embeddings.

Work on a copy so that the example directory stays clean:

```bash
cp -R examples/demo-project /tmp/demo-project && cd /tmp/demo-project
```

## 1. Initialise and analyse

```console
$ relate-codemem init
relate-codemem initialised in /tmp/demo-project
  hooks, permissions   .claude/settings.json
  MCP server           .mcp.json
  indexing options     .claude/relate-codemem.json
  instructions         CLAUDE.md
  embeddings           local:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  (from default)

initial analysis:
indexed 11 changed file(s), 0 deleted, 14 symbols, 11 chunks
  embedded 26 item(s)
embeddings up to date (26 computed, local:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)

restart Claude Code in this project to load the hooks and the MCP server.
```

Eleven files: the nine of the demo plus the `CLAUDE.md` and `.mcp.json` that
`init` just wrote.

## 2. Search

The question shares no word with the ADR; the semantic list finds it
(`[vec]`):

```console
$ relate-codemem search "which database do we use" --kind decision -k 2
[vec] Chunk  docs/decisions/0001-postgres.md:7
       Use PostgreSQL as primary store > Context
       We need transactions.
[vec] Chunk  docs/decisions/0001-postgres.md:9
       Use PostgreSQL as primary store > Decision
       PostgreSQL 17, no ORM.
```

Code symbols, found by both lists (`[text+vec]`):

```console
$ relate-codemem search "load settings from yaml" --kind code -k 2
[text+vec] Symbol  src/app/config.py:7
       class Settings
       class Settings Runtime settings.
[text+vec] Symbol  src/app/config.py:17
       function parse_config
       def parse_config(path: str, strict: bool=False) Load the YAML configuration file.
```

## 3. Change a file and look at its history

Edit the body of `parse_config` in `src/app/config.py`, then:

```console
$ relate-codemem index
indexed 1 changed file(s), 0 deleted, 4 symbols, 0 chunks
  embedded 1 item(s)
embeddings up to date (1 computed, local:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)

$ relate-codemem file src/app/config.py
src/app/config.py  kind=code lang=python revision=2 lines=23 hash=fe65877c85dc indexed=2026-11-24T10:53

Revisions:
  r2 2026-11-24T10:53 fe65877c85dc
  r1 2026-11-24T10:53 ba92b11ecc74

Symbols:
  7-14 class Settings (since r1, changed r1)
  10-11 method Settings.__init__ (since r1, changed r1)
  13-14 method Settings.reload (since r1, changed r1)
  17-22 function parse_config (since r1, changed r2)

Depends on: src/app/util.py

Imports: .util, os
```

The file moved to revision 2, and only `parse_config` is marked as changed in
it. Inside Claude Code you never run `index` yourself: the hooks do it after
every edit, and the revision is linked to the session that produced it.

## 4. Record a decision and recall

```console
$ relate-codemem decide --title "Strict config parsing is opt-in" \
    --text "parse_config only raises when strict=True." \
    --rationale "Existing deployments rely on empty files." \
    --slot config/strictness --files src/app/config.py
stored decision:20261124T104500-strict-config-parsing-is-opt-in

$ relate-codemem recall
# Open decisions (2)
- 2026-11-24 [accepted] Strict config parsing is opt-in  <decision:20261124T104500-strict-config-parsing-is-opt-in> slot=config/strictness
- 2026-10-02 [accepted] Use PostgreSQL as primary store  <docs/decisions/0001-postgres.md>

# Notes (0)

# Meetings (1)
- 2026-11-20 Kickoff meeting  <docs/meetings/2026-11-20-kickoff.md> with Alice Martin, Bob Chen
...
```

A later `decide` with the same `--slot config/strictness` supersedes this
entry: it stays in the graph, marked `superseded`, and stops appearing here.

## 5. Query the graph directly

```bash
relate-codemem query "MATCH (f:File)-[:DEPENDS_ON]->(t:File) RETURN f.path, t.path LIMIT 10"
relate-codemem schema
```

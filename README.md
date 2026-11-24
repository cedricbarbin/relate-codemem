# relate-codemem

Long-term project memory for [Claude Code](https://claude.com/claude-code),
stored in a [relate](https://relatedb.ai) embedded graph database: one local
store per project, no server.

Claude Code forgets everything between sessions. relate-codemem gives each
project a graph that remembers the code (with content hashes and revisions),
the documentation, the decisions, the meeting notes and what happened in
earlier sessions, and lets Claude search it.

- **Hooks write.** Every prompt, reply, edit and file change is recorded by
  Claude Code hooks, whether or not the model thinks about it.
- **CLAUDE.md reads.** A section of `CLAUDE.md` tells Claude what the memory
  holds and when it is worth querying; Claude decides.
- **Real parsing where it is installed.** With the optional `parse` extra,
  tree-sitter gives exact symbol extents and nested names in thirteen
  languages, and a coarser structure for the other languages it knows;
  regular expressions cover the rest.
- **A call graph that knows about homonyms.** Calls between methods are
  resolved through the file dependencies, the receiver's written type and
  the argument count, and each link says how sure it is.
- **Hybrid search.** relate's full-text index and its vector index, fused by
  reciprocal rank. Embeddings are computed locally by default.
- **Everything stays in the project.** The store is a directory under
  `.claude/relate/`. Nothing leaves the machine unless you pick a remote
  embedding API.

Works on macOS, Linux and Windows.

> **Status: 0.1.x, early development.** The graph model, the command names and
> the configuration keys may still change before 1.0. Developed and tested on
> macOS; the Linux and Windows paths are covered by CI only.

## Install

relate-codemem is on [PyPI](https://pypi.org/project/relate-codemem/). With
[uv](https://docs.astral.sh/uv) and [relate](https://relatedb.ai) already on
the machine, on any platform:

```bash
uv tool install --python 3.12 "relate-codemem[local,parse]"
```

```bash
relate-codemem setup --embeddings local
```

`setup` records the default embedding provider for this machine and adds the
`/relate-codemem-init` skill to Claude Code. Both extras are optional: `local`
adds local embeddings, `parse` adds syntax-tree parsing of code with
[tree-sitter](https://tree-sitter.github.io/) (Python 3.11 or later). Grammars
are downloaded on first use, about 26 MB once plus a few MB per language.
Without `parse`, symbols are extracted with regular expressions.

To have the prerequisites installed for you, run the installer from a
checkout. On macOS and Linux:

```bash
git clone https://github.com/cedricbarbin/relate-codemem && cd relate-codemem && ./install.sh
```

On Windows (PowerShell):

```powershell
git clone https://github.com/cedricbarbin/relate-codemem; cd relate-codemem; .\install.ps1
```

The installer checks for uv and relate and offers to install them, asks which
embedding provider should be the default, then installs the command (alias
`rcm`) from the checkout and runs `setup`. Details:
[docs/install.md](docs/install.md).

Check the result at any time:

```bash
relate-codemem doctor
```

## Set up a project

In Claude Code, inside the project, type `/relate-codemem-init`. Or from a
terminal at the project root:

```bash
relate-codemem init
```

This merges the hooks into `.claude/settings.json`, registers a read-only MCP
server in `.mcp.json`, adds the memory section to `CLAUDE.md`, creates
`docs/decisions/` and `docs/meetings/`, ignores the store in git, then runs
the initial analysis of the code base. Restart Claude Code afterwards so the
hooks load.

The initial analysis can be run on its own, and rerun at any time; it only
processes what changed:

```bash
relate-codemem index
```

## What is stored

| Source | In the graph | Written by |
| --- | --- | --- |
| Code (30+ file types) | `File`, `FileRevision`, `Symbol`, `Module`, file dependencies (imports, same-package and qualified references) | hooks, `index` |
| Documentation (`*.md`, `*.rst`, `*.txt`, ...) | `File`, `Chunk` per section | hooks, `index` |
| Decisions in `docs/decisions/` (ADRs) | `Decision`, `Chunk` | hooks, `index` |
| Decisions made in a session | `Decision`, `Note` | Claude, with `decide` and `note` |
| Meetings in `docs/meetings/` (`.md`, `.txt`, `.vtt`, `.srt`) | `Meeting`, `Person`, `Chunk` | hooks, `index` |
| Sessions | `Session`, `Prompt`, `Reply`, `ToolCall`, files read | hooks |

Every file carries a sha256 `hash` and a `revision` number. When the hash
changes, the revision is incremented and a `FileRevision` node records the
hash, time, git commit and session, linked to the previous one. Symbols keep
the revision they appeared in and the last revision that changed their body.
Decisions and notes are never deleted: a newer entry in the same slot
supersedes the old one. See [docs/architecture.md](docs/architecture.md).

## Use it

Claude runs these itself when it judges the memory relevant; they work the
same in a terminal.

```bash
relate-codemem recall                              # open decisions, notes, meetings, recent sessions
relate-codemem search "how are tokens refreshed"   # hybrid search
relate-codemem search retry --kind decision        # code | doc | decision | meeting | session | note
relate-codemem file src/app/config.py              # revisions, symbols, dependencies, decisions, sessions
relate-codemem symbol parse_config
relate-codemem query "MATCH (f:File) RETURN f.kind, count(*) LIMIT 10"    # read-only Cypher
relate-codemem decide --title "..." --text "..." --rationale "..." --slot area/topic --files a.py,b.py
relate-codemem note "Integration tests need Docker running" --slot tests/prerequisites
relate-codemem status
```

A worked example with real output is in [examples/](examples/README.md).

## Embeddings

| Provider | Runs | Default model | Needs |
| --- | --- | --- | --- |
| `local` (default) | on this machine, via fastembed | `paraphrase-multilingual-MiniLM-L12-v2` | a one-time model download (about 220 MB) |
| `ollama` | your Ollama server | `bge-m3` | `ollama pull bge-m3` |
| `openai` | any OpenAI-compatible `/embeddings` API | `text-embedding-3-small` | an API key in an environment variable |
| `none` | - | - | nothing; search is full-text only |

The default is chosen at install time (`relate-codemem setup --embeddings ...`)
and can be overridden per project in `.claude/settings.local.json`:

```json
{
  "env": {
    "RELATE_CODEMEM_EMBEDDINGS": "ollama",
    "RELATE_CODEMEM_EMBEDDING_MODEL": "bge-m3"
  }
}
```

`relate-codemem init --embeddings ollama` writes that block for you, and
`relate-codemem config` shows the effective value and where it comes from. When
nothing is configured, embeddings are local. Changing provider or model
rebuilds the project's vectors on the next `relate-codemem embed`. All settings:
[docs/configuration.md](docs/configuration.md).

## Privacy

- The store (`.claude/relate/`) holds prompts, replies and file contents in
  clear text. `init` adds it to `.gitignore`; do not share it unreviewed.
- With `local`, `ollama` on localhost or `none`, nothing leaves the machine.
  With `openai`, the text of indexed files, prompts and replies is sent to the
  configured API.
- API keys are read from an environment variable and never written to disk.
- The `relate` binary collects anonymous usage metrics by default;
  `relate telemetry off` disables them.

## Limitations

- Symbol extraction is exact for Python (`ast`) and pattern-based for other
  languages: end lines are approximate and some methods are missed.
- The default local model is small. It handles paraphrases and cross-language
  queries on sentences well, raw code and long passages less so.
- relate's vector index is an exact scan, so its cost grows linearly with the
  number of vectors.
- Full-text search matches whole words without stemming; identifiers are split
  (`config` finds `parseConfig` and `parse_config`).
- Timestamps are shown in UTC.
- Windows: relate 1.2.0 is unstable there when a read-only open follows a write,
  so reads are serialised with writes. The read-only MCP server is covered by a CI
  test on Windows.

## Uninstall

```bash
uv tool uninstall relate-codemem
```

Then delete `~/.claude/skills/relate-codemem-init`, the cache
(`~/.cache/relate-codemem`, models and grammars) and the user configuration
(`~/.config/relate-codemem`, or `%APPDATA%\relate-codemem` on Windows). In a
project, remove the `relate-codemem hook` entries from `.claude/settings.json`,
the server from `.mcp.json`, the marked section of `CLAUDE.md` and the
`.claude/relate/` directory.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports:
[SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE). relate-codemem is an independent project; it drives the
`relate` binary, which is distributed separately under its own license.

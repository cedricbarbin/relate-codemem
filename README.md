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
  resolved through the file dependencies, the receiver's written type, the
  type hierarchy and the argument count; each link says how sure it is, and
  the implementations behind an interface call can be queried.
- **Inside or outside the project.** Imports are classified (project,
  standard library, external library), and calls or supertypes that an
  import proves external are kept apart from the internal graph.
- **Infrastructure too.** Terraform blocks and Ansible plays, tasks and
  handlers are symbols, with their references, module and role dependencies.
- **Hybrid search.** relate's full-text index and its vector index, fused by
  reciprocal rank. Embeddings are computed locally by default.
- **Everything stays in the project.** The store is a directory under
  `.claude/relate/`. Nothing leaves the machine unless you pick a remote
  embedding API.

Works on macOS, Linux and Windows.

> **Status: 0.1.x, early development.** The graph model, the command names and
> the configuration keys may still change before 1.0. Developed and tested on
> macOS; the Linux and Windows paths are covered by CI only.

Source, documentation and issues: <https://github.com/cedricbarbin/relate-codemem>.

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
[docs/install.md](https://github.com/cedricbarbin/relate-codemem/blob/main/docs/install.md).

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
supersedes the old one. See [docs/architecture.md](https://github.com/cedricbarbin/relate-codemem/blob/main/docs/architecture.md).

## In Claude Code

`setup` installs five skills, each usable as a slash command:

| Command | What it does |
| --- | --- |
| `/relate-codemem-init` | set up the memory in the current project and analyse it |
| `/relate-codemem-ask <question>` | answer from the memory graph first, then check against the code |
| `/relate-codemem-impact <symbol>` | callers, classes and files affected by changing a method or a class |
| `/relate-codemem-playground [password]` | serve the graph locally for visual exploration, with an optional password for the user `relate` |
| `/relate-codemem-sharedb [off]` | let git track the store, after a warning and an explicit confirmation |

The project's `.mcp.json` registers a read-only MCP server whose tools cover
the standard questions, so Claude can answer them without writing a query:

| Tool | Question it answers |
| --- | --- |
| `search`, `recall` | where is this, what did we decide, what happened last time |
| `file_info`, `symbol_info` | everything recorded about one file or one symbol |
| `class_calls` | which methods does class X call, inside and outside the project? |
| `implementations` | which implementations of interface Y exist? |
| `impact` | what would changing method M of class C affect: calling methods, classes, files? |
| `call_path` | how does A end up calling B? |
| `library_usage` | which libraries do we use, and where is library L used? |
| `why` | why is this file the way it is: decisions, documents, sessions? |
| `tests_for` | which tests cover this method or class? |
| `diff_impact` | what do my current changes affect, and which tests reach them? |
| `get_schema`, `read_cypher` | anything else, in read-only Cypher |

The same questions are offered as MCP prompts (`ask`, `class-calls`,
`implementations`, `impact`, `call-path`, `library`, `why`, `tests`,
`diff-impact`), which Claude Code lists among its slash commands under the
server's name. The questions that rest on the call graph (`class_calls`,
`implementations`, `impact`, `call_path`, `tests_for`, `diff_impact`) need
the `parse` extra.

## Sharing the store

The store is local by default: `init` adds `.claude/relate/` to `.gitignore`,
and each teammate builds their own. `relate-codemem sharedb` lets git track
the database instead, so that the memory travels with the repository. It
shows a warning and asks for a confirmation first, because the store holds
session prompts, replies and shell commands, which may contain personal
data or secrets pasted by mistake, and everything pushed stays in the git
history. **Do not do this on a public repository.** The database is binary
and changes at every session, so the repository grows and two versions
cannot be merged. The playground token, logs and local state stay ignored.
`relate-codemem sharedb --off` goes back to a local store.

## Use it

Claude runs these itself when it judges the memory relevant; they work the
same in a terminal.

```bash
relate-codemem recall                              # open decisions, notes, meetings, recent sessions
relate-codemem search "how are tokens refreshed"   # hybrid search
relate-codemem search retry --kind decision        # code | doc | decision | meeting | session | note
relate-codemem file src/app/config.py              # revisions, symbols, dependencies, decisions, sessions
relate-codemem symbol parse_config                 # definition, hierarchy, calls and callers
relate-codemem calls OrderService                  # what a class (all its methods) or a method calls
relate-codemem implementations Repository          # subtypes of a type, redefinitions of a method
relate-codemem impact Customer.save --depth 3      # callers, classes and files affected by a change
relate-codemem path OrderController.create Mailer.send   # shortest chain of calls between two symbols
relate-codemem uses                                # libraries the project uses; `uses numpy`: where and how
relate-codemem why src/billing/invoice.py          # decisions, documents and sessions that explain a file
relate-codemem tests Invoice.total                 # tests that reach a method or a class
relate-codemem diff-impact                         # what the uncommitted changes affect (or `diff-impact origin/main`)
relate-codemem playground                          # explore the graph in Neo4j Browser (read-only, local)
relate-codemem query "MATCH (f:File) RETURN f.kind, count(*) LIMIT 10"    # read-only Cypher
relate-codemem decide --title "..." --text "..." --rationale "..." --slot area/topic --files a.py,b.py
relate-codemem note "Integration tests need Docker running" --slot tests/prerequisites
relate-codemem status
```

A worked example with real output is in [examples/](https://github.com/cedricbarbin/relate-codemem/blob/main/examples/README.md).

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
[docs/configuration.md](https://github.com/cedricbarbin/relate-codemem/blob/main/docs/configuration.md).

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

Then delete the `~/.claude/skills/relate-codemem-*` directories, the cache
(`~/.cache/relate-codemem`, models and grammars) and the user configuration
(`~/.config/relate-codemem`, or `%APPDATA%\relate-codemem` on Windows). In a
project, remove the `relate-codemem hook` entries from `.claude/settings.json`,
the server from `.mcp.json`, the marked section of `CLAUDE.md` and the
`.claude/relate/` directory.

## Contributing

See [CONTRIBUTING.md](https://github.com/cedricbarbin/relate-codemem/blob/main/CONTRIBUTING.md). Security reports:
[SECURITY.md](https://github.com/cedricbarbin/relate-codemem/blob/main/SECURITY.md).

## License

[MIT](https://github.com/cedricbarbin/relate-codemem/blob/main/LICENSE). relate-codemem is an independent project; it drives the
`relate` binary, which is distributed separately under its own license.

# Installation

relate-codemem is a Python command-line tool with no required dependencies. It
needs two programs on the machine:

| Program | Why | Install |
| --- | --- | --- |
| [relate](https://relatedb.ai) 1.2 or later | the graph database | `curl -sSfL https://relatedb.ai/install.sh \| bash`, or on Windows `powershell -ExecutionPolicy ByPass -c "irm https://relatedb.ai/install.ps1 \| iex"` |
| [uv](https://docs.astral.sh/uv) | installs the tool in an isolated environment with its own Python | see the uv documentation |

`git` is optional: with it, file discovery honours `.gitignore`.

## From PyPI

With relate and uv installed:

```bash
uv tool install --python 3.12 "relate-codemem[local,parse]"
relate-codemem setup --embeddings local
```

The `parse` extra adds `tree-sitter-language-pack`, which parses code into
syntax trees; without it, or on Python 3.10, symbols are extracted with
regular expressions (see [architecture](architecture.md)). The pack downloads
a grammar the first time a language is met: the first `index` of a project
needs network access. The `local` extra adds `fastembed` for local embeddings; install plain
`relate-codemem` if the provider will be `ollama`, `openai` or `none`, and
pass that provider to `setup`. `setup` accepts the same `--model`, `--url` and
`--api-key-env` options as the installer below.

`pipx` works too: `pipx install "relate-codemem[local,parse]"`.

To install the development version instead of the latest release:

```bash
uv tool install --python 3.12 --with "fastembed>=0.4" --with "tree-sitter-language-pack>=1.25,<2" git+https://github.com/cedricbarbin/relate-codemem
```

## With the installer

From a checkout, `./install.sh` (macOS, Linux) or `.\install.ps1` (Windows).
Both accept the same options:

| `install.sh` | `install.ps1` | Meaning |
| --- | --- | --- |
| `--embeddings NAME` | `-Embeddings NAME` | default provider: `local`, `ollama`, `openai`, `none`. Asked interactively when omitted; `local` when there is no terminal |
| `--model NAME` | `-Model NAME` | embedding model |
| `--url URL` | `-Url URL` | base URL for `ollama` or `openai` |
| `--api-key-env VAR` | `-ApiKeyEnv VAR` | environment variable that holds the API key |
| `-y`, `--yes` | `-Yes` | install missing prerequisites without asking, no questions |

Unattended example:

```bash
./install.sh --yes --embeddings ollama --model bge-m3
```

The installer:

1. installs uv and relate if they are missing (after asking);
2. runs `uv tool install` on the checkout (the code you cloned, not the PyPI
   release), with `tree-sitter-language-pack`, and with `fastembed` when the
   provider is `local`;
3. runs `relate-codemem setup`, which records the default provider in the user
   configuration, writes the Claude Code skill to
   `~/.claude/skills/relate-codemem-*/` (init, ask, impact, playground, sharedb) and
   prints the `doctor` report.

## PATH

Claude Code hooks call `relate-codemem hook`, so the command must be on the
`PATH` that Claude Code sees. `uv tool update-shell` adds uv's tool directory
to your shell profile; restart the terminal (and Claude Code) afterwards.
`relate-codemem doctor` reports whether the command is found.

## Upgrade

```bash
uv tool upgrade relate-codemem
relate-codemem setup            # refreshes the skill
relate-codemem init             # in each project: refreshes hooks and the CLAUDE.md section
relate-codemem index --rebuild  # only if release notes say the analysis changed: starts it again from scratch
```

If you installed from a checkout, pull the repository and rerun the installer
instead of `uv tool upgrade`.

## Teams

Commit `.claude/settings.json`, `.claude/relate-codemem.json`, `.mcp.json` and
`CLAUDE.md`. Each teammate installs relate-codemem once; their store is built
locally by the hooks and by `relate-codemem index`. Sharing the store itself
through git is possible with `relate-codemem sharedb`, on a private
repository only: read its warning first. Someone without the tool
sees a failing-hook notice in Claude Code but can keep working.

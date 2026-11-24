---
name: relate-codemem-init
description: "Set up relate-codemem in a project: the project memory graph on the relate embedded database (hooks, settings, CLAUDE.md section, docs/decisions and docs/meetings, read-only MCP server), then run the initial analysis of the code base (files, hashes, revisions, symbols, docs, embeddings). Use when the user wants to initialise, install, repair or upgrade the relate memory in a project, to (re)run the initial indexing, or to change the embedding provider of a project."
---

# relate-codemem: initialise a project

`relate-codemem` is a command installed on this machine (alias `rcm`). Run
every step from the project root.

## 1. Check the installation

```bash
relate-codemem doctor
```

It reports the `relate` binary, the command on `PATH`, the embedding provider
and the state of the current project, with the fix for each missing item.

- If `relate-codemem` itself is not found, the tool is not installed on this
  machine: point the user to the project's install script and stop.
- If `relate` is missing, give the user the install command that `doctor`
  prints and stop; do not install it without their agreement.
- A failing embedding provider does not block the rest: full-text search still
  works. Say so and continue, or offer another provider (step 3).

## 2. Find the project root

`git rev-parse --show-toplevel`, else the current working directory. Confirm
with the user when it is the home directory or looks like a parent of several
projects.

## 3. Initialise and analyse

```bash
relate-codemem init
```

One command, safe to rerun. It:

- merges the hooks (`SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`,
  `PreCompact`, `SessionEnd`) and the command permission into
  `.claude/settings.json`, keeping what is already there;
- registers the read-only MCP server `relate-codemem` in `.mcp.json`;
- writes the memory section of `CLAUDE.md` between
  `<!-- relate-codemem:begin -->` and `<!-- relate-codemem:end -->`;
- writes the indexing options to `.claude/relate-codemem.json` (kept if present);
- creates `docs/decisions/` and `docs/meetings/` with a README template each;
- adds `.claude/relate/` (the store) to `.gitignore`;
- runs the initial analysis: sha256 hash and revision 1 for every file,
  symbols and imports for code, chunks for documentation, decisions and
  meetings, then the embeddings.

Options:

- `--no-index` sets the project up without analysing it; `relate-codemem index`
  does it later. On a large repository run the analysis in the background and
  report when it ends.
- `--embeddings local|ollama|openai|none` (with `--model`, `--url`,
  `--api-key-env`) overrides the machine default for this project only. The
  choice is written to the `env` block of `.claude/settings.local.json` as
  `RELATE_CODEMEM_EMBEDDINGS` and related variables. Ask before choosing
  `openai`: file contents are sent to that service.

If the project keeps its ADRs or meeting notes elsewhere, set `decisions_dirs`
/ `meetings_dirs` in `.claude/relate-codemem.json`, then run
`relate-codemem index`.

With local embeddings, the first run on a machine downloads the model (about
220 MB, once).

## 4. Verify and report

```bash
relate-codemem status
relate-codemem recall
```

Report the counts (files, symbols, chunks, decisions, meetings, embedding
coverage) and anything that failed (`.claude/relate/memory.log`). Then tell
the user:

- hooks and the MCP server load at session start: restart Claude Code in this
  project, and approve the `relate-codemem` MCP server when asked;
- existing ADRs and meeting notes belong in the configured directories;
- `.claude/relate/` holds prompts and replies in clear text and is not
  committed; `.claude/settings.json`, `.claude/relate-codemem.json`,
  `.mcp.json` and `CLAUDE.md` are meant to be committed so the team shares the
  setup. Teammates need `relate-codemem` installed too.

Do not commit anything yourself unless asked.

# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

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

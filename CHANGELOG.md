# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

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

# Contributing

Bug reports, fixes and new language extractors are welcome.

## Development setup

You need [uv](https://docs.astral.sh/uv) and [relate](https://relatedb.ai).

```bash
git clone https://github.com/cedricbarbin/relate-codemem && cd relate-codemem
uv tool install --python 3.12 --force --editable ".[local]"   # the command, pointing at your checkout
uv run --no-project --python 3.12 --with pytest --with-editable . pytest   # the tests
```

Integration tests run against a real relate store in a temporary directory
and are skipped when the `relate` binary is absent. They use a deterministic
`fake` embedding provider, so no model is downloaded.

## Guidelines

- The package has no required runtime dependency; keep it that way. Optional
  features go behind an extra, as `fastembed` does.
- Everything must work on macOS, Linux and Windows: use `pathlib`, store
  project paths in POSIX form, pass `encoding="utf-8"` when reading or writing
  text, and go through `store.file_lock` for locking.
- Hooks must stay fast and must never fail a session. Slow work belongs in the
  background embedding pass or in `index`.
- All Cypher values go through the `$p` parameter map, never string
  formatting. Labels may be formatted in only from fixed lists in the code.
- Graph writes are idempotent: `MERGE` on the node key.
- A change to the graph model updates `docs/architecture.md` and
  `recall.MODEL_DOC`; a change to configuration updates
  `docs/configuration.md`.
- Add a test with each fix. Extraction tests live in `tests/test_extract.py`
  and need no database.

## Adding a language

Symbols and imports for languages other than Python are extracted with
regular expressions, in `PATTERNS` and `IMPORT_RX` of
`src/relate_codemem/extract.py`. Add the file extension to `CODE_EXT`, the
patterns for the language, and a test with a short sample.

## Pull requests

Keep each pull request to one subject, describe what changed and why, and add
a line to `CHANGELOG.md` under "Unreleased".

## Releasing

Maintainers only.

1. Set the version in `pyproject.toml` and `src/relate_codemem/__init__.py`,
   and move the "Unreleased" entries of `CHANGELOG.md` under the new version.
2. Merge, then publish a GitHub release whose tag is `v<version>`.
3. The `Publish` workflow builds the package and waits for approval on the
   `pypi` environment; once approved it uploads to PyPI through trusted
   publishing. No token is stored anywhere.

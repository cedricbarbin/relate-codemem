# Configuration

`relate-codemem config` prints the effective configuration of the current
project and the source of each value.

## Layers

Settings are read from four places, most specific first:

| Layer | Where | Typical use |
| --- | --- | --- |
| Process environment | `RELATE_CODEMEM_*` variables | one-off runs, CI |
| Project, personal | `env` block of `.claude/settings.local.json` | your own override for this project (not committed) |
| Project, shared | `env` block of `.claude/settings.json` | a team-wide choice for this project |
| Machine | `~/.config/relate-codemem/config.json` (`%APPDATA%\relate-codemem\config.json` on Windows) | the default chosen at install time |

When no layer says anything, embeddings are `local`.

relate-codemem reads the two settings files itself, so a command typed in a
terminal and a hook started by Claude Code resolve the same configuration.

## Embedding variables

| Variable | Meaning | Default |
| --- | --- | --- |
| `RELATE_CODEMEM_EMBEDDINGS` | `local`, `ollama`, `openai` or `none` | `local` |
| `RELATE_CODEMEM_EMBEDDING_MODEL` | model name | per provider, see below |
| `RELATE_CODEMEM_EMBEDDING_URL` | base URL (`ollama`, `openai`) | `http://localhost:11434`, `https://api.openai.com/v1` |
| `RELATE_CODEMEM_EMBEDDING_API_KEY_ENV` | name of the variable that holds the API key (`openai`) | `OPENAI_API_KEY` |
| `RELATE_CODEMEM_EMBEDDING_DIMENSIONS` | requested vector size, for APIs that accept it | the model's own |
| `RELATE_CODEMEM_EMBEDDING_MIN_SIMILARITY` | score below which a semantic hit is dropped (0.5 is unrelated, 1 identical) | `0.62` local, `0.60` others |

### Providers

**local**: [fastembed](https://github.com/qdrant/fastembed) runs
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384
dimensions, about fifty languages) on the CPU. The model is downloaded once
into `~/.cache/relate-codemem` (`%LOCALAPPDATA%\relate-codemem\cache` on
Windows). Requires the `local` extra, which the installer adds. Any model
fastembed supports can be named in `RELATE_CODEMEM_EMBEDDING_MODEL`.

**ollama**: calls `POST /api/embed` on an Ollama server. Default model
`bge-m3` (multilingual); pull it first with `ollama pull bge-m3`.

**openai**: calls `POST {url}/embeddings` with the OpenAI request shape. This
covers OpenAI, Mistral, Voyage, and self-hosted servers such as vLLM, LM
Studio or text-embeddings-inference. The key is read from the environment
variable named by `RELATE_CODEMEM_EMBEDDING_API_KEY_ENV` and is never stored; a
`localhost` URL may run without a key. File contents, prompts and replies are
sent to that service.

```json
{
  "env": {
    "RELATE_CODEMEM_EMBEDDINGS": "openai",
    "RELATE_CODEMEM_EMBEDDING_URL": "https://api.mistral.ai/v1",
    "RELATE_CODEMEM_EMBEDDING_MODEL": "mistral-embed",
    "RELATE_CODEMEM_EMBEDDING_API_KEY_ENV": "MISTRAL_API_KEY"
  }
}
```

**none**: no vectors; `search` is full-text only.

### Changing provider or model

Vectors from different models are not comparable. Each store records what its
vectors were built with (`.claude/relate/embeddings.json`). When the effective
provider or model differs, `search` falls back to full-text and says so, and
the next `relate-codemem embed` (or the next hook) checks that the new provider
answers, then drops the old vectors and rebuilds them. `relate-codemem embed
--reset` forces a rebuild.

## Commands that write configuration

| Command | Writes |
| --- | --- |
| `relate-codemem setup --embeddings X [--model M] [--url U] [--api-key-env V]` | the machine default |
| `relate-codemem init --embeddings X [...]` | the project override, in `.claude/settings.local.json` |

## Project options: `.claude/relate-codemem.json`

Created by `init` with the defaults; commit it so the team indexes the same
way.

| Key | Meaning | Default |
| --- | --- | --- |
| `decisions_dirs` | directories whose files are decisions (ADRs) | `docs/decisions`, `docs/adr`, `adr` |
| `meetings_dirs` | directories whose files are meeting notes or transcripts | `docs/meetings`, `meetings` |
| `exclude_dirs` | directory names skipped anywhere in the tree | `.git`, `.claude`, `node_modules`, `.venv`, `dist`, `build`, ... |
| `exclude_files` | file names skipped | lock files |
| `max_file_bytes` | larger files are skipped | `500000` |
| `chunk_chars` | target size of a documentation chunk | `1800` |
| `max_prompt_chars`, `max_reply_chars` | stored length of prompts and replies | `6000`, `8000` |

The first entry of `decisions_dirs` and `meetings_dirs` is where `init`
creates the README templates. After changing these, run `relate-codemem index`.

## Other variables

| Variable | Meaning |
| --- | --- |
| `RELATE_CODEMEM_HOME` | store directory, instead of `<project>/.claude/relate` |
| `RELATE_CODEMEM_PARSER` | `auto` (default): tree-sitter when the `parse` extra is installed (grammars are downloaded on first use); `regex`: always regular expressions, no download |
| `RELATE_CODEMEM_PROJECT` | project root, instead of auto-detection |
| `RELATE_CODEMEM_PLAYGROUND_PASSWORD` | password of the user `relate` for `playground`, instead of a generated token; put it in `.claude/settings.local.json`, not in the shared settings |

The project root is, in order: `RELATE_CODEMEM_PROJECT`, Claude Code's
`CLAUDE_PROJECT_DIR`, the nearest parent directory that is already
initialised, the git root, the current directory.

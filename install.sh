#!/bin/sh
# relate-codemem installer for macOS and Linux.
#
#   ./install.sh                         from a checkout
#   curl -sSfL <raw url>/install.sh | sh from the published repository
#
# Options:
#   --embeddings local|ollama|openai|none   default embedding provider (default: local)
#   --model NAME  --url URL  --api-key-env VAR   provider settings
#   -y, --yes                                install missing prerequisites without asking
set -eu

REPO_URL="${RELATE_CODEMEM_REPO:-https://github.com/cedricbarbin/relate-codemem}"
EMBEDDINGS="" MODEL="" URL="" KEY_ENV="" YES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --embeddings) EMBEDDINGS="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --url) URL="$2"; shift 2 ;;
    --api-key-env) KEY_ENV="$2"; shift 2 ;;
    -y|--yes) YES=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
ask() {  # ask "question" -> 0 for yes
  [ "$YES" = 1 ] && return 0
  [ -r /dev/tty ] || return 1
  printf '%s [Y/n] ' "$1" > /dev/tty
  read -r answer < /dev/tty || return 1
  case "$answer" in ""|y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# 1. uv: installs the tool in its own environment, with a Python it manages
if ! command -v uv >/dev/null 2>&1; then
  if ask "uv (Python tool installer, https://docs.astral.sh/uv) is missing. Install it?"; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  else
    say "uv is required: https://docs.astral.sh/uv/getting-started/installation/"; exit 1
  fi
fi

# 2. relate: the embedded graph database
if ! command -v relate >/dev/null 2>&1; then
  if ask "relate (embedded graph database, https://relatedb.ai) is missing. Install it?"; then
    curl -sSfL https://relatedb.ai/install.sh | bash
  else
    say "relate is required: curl -sSfL https://relatedb.ai/install.sh | bash"; exit 1
  fi
fi

# 3. embedding provider
if [ -z "$EMBEDDINGS" ]; then
  EMBEDDINGS=local
  if [ "$YES" = 0 ] && [ -r /dev/tty ]; then
    {
      say ""
      say "Semantic search needs embeddings. Default provider for this machine:"
      say "  1) local   on this machine, no service, ~220 MB model downloaded once (default)"
      say "  2) ollama  an Ollama server you run"
      say "  3) openai  an OpenAI-compatible API (file contents are sent to it)"
      say "  4) none    full-text search only"
      printf 'Choice [1]: '
    } > /dev/tty
    read -r choice < /dev/tty || choice=1
    case "$choice" in 2|ollama) EMBEDDINGS=ollama ;; 3|openai) EMBEDDINGS=openai ;; 4|none) EMBEDDINGS=none ;; esac
  fi
fi
case "$EMBEDDINGS" in local|ollama|openai|none) ;; *) say "unknown provider: $EMBEDDINGS" >&2; exit 2 ;; esac

# 4. the tool itself: from this checkout when run inside one, else from the repository
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
  SOURCE="$SCRIPT_DIR"
else
  SOURCE="git+$REPO_URL"
fi
say ""
say "Installing relate-codemem from $SOURCE ..."
PARSE="tree-sitter-language-pack>=0.13,<1"   # syntax-tree parsing of code
if [ "$EMBEDDINGS" = local ]; then
  uv tool install --python 3.12 --force --with "fastembed>=0.4" --with "$PARSE" "$SOURCE"
else
  uv tool install --python 3.12 --force --with "$PARSE" "$SOURCE"
fi
uv tool update-shell >/dev/null 2>&1 || true
BIN="$(uv tool dir --bin)/relate-codemem"

# 5. machine setup: default provider, global Claude Code skill, checks
set -- setup --embeddings "$EMBEDDINGS"
[ -n "$MODEL" ] && set -- "$@" --model "$MODEL"
[ -n "$URL" ] && set -- "$@" --url "$URL"
[ -n "$KEY_ENV" ] && set -- "$@" --api-key-env "$KEY_ENV"
say ""
"$BIN" "$@" || true

say ""
say "Done. In a project:  relate-codemem init     (or /relate-codemem-init in Claude Code)"
command -v relate-codemem >/dev/null 2>&1 || say "Open a new terminal first so that relate-codemem is on your PATH."

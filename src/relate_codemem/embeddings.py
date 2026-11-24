"""Embeddings computed upstream of relate, then stored as typed vectors and searched
through relate's vector index.

Providers:
  local   fastembed, on this machine (extra `relate-codemem[local]`); nothing leaves it
  ollama  an Ollama server (default http://localhost:11434)
  openai  any OpenAI-compatible /embeddings endpoint (OpenAI, Mistral, Voyage, vLLM, ...)
  none    full-text search only
"""
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

from . import config, store

EMBED_LABELS = ("Decision", "Note", "Chunk", "Symbol", "Prompt", "Reply")
VECTOR_INDEX = "memory_vec"
MAX_CHARS = 2000


class EmbeddingError(store.RelateError):
    pass


# ---------------------------------------------------------------- providers

def _post(url, body, headers=None, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise EmbeddingError(f"{url} answered HTTP {exc.code}: {detail}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise EmbeddingError(f"cannot reach {url}: {exc}")


def _local(texts, cfg):
    try:
        from fastembed import TextEmbedding
    except ImportError:
        raise EmbeddingError("local embeddings need fastembed: reinstall with the `local` extra "
                             "(uv tool install 'relate-codemem[local]') or pick another provider")
    cache = config.user_cache_dir() / "fastembed"
    cache.mkdir(parents=True, exist_ok=True)
    model = TextEmbedding(model_name=cfg["model"], cache_dir=str(cache))
    return [[float(x) for x in v] for v in model.embed(texts, batch_size=32)]


def _ollama(texts, cfg):
    out = []
    for start in range(0, len(texts), 64):
        data = _post(cfg["url"].rstrip("/") + "/api/embed",
                     {"model": cfg["model"], "input": texts[start:start + 64]})
        if "embeddings" not in data:
            raise EmbeddingError(f"Ollama returned no embeddings: {str(data)[:200]}")
        out += data["embeddings"]
    return out


def _openai(texts, cfg):
    key_env = cfg.get("api_key_env") or ""
    key = os.environ.get(key_env, "") if key_env else ""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if key_env and not key and "localhost" not in cfg["url"] and "127.0.0.1" not in cfg["url"]:
        raise EmbeddingError(f"environment variable {key_env} is not set (API key for {cfg['url']})")
    out = []
    for start in range(0, len(texts), 96):
        body = {"model": cfg["model"], "input": texts[start:start + 96]}
        if cfg.get("dimensions"):
            body["dimensions"] = int(cfg["dimensions"])
        data = _post(cfg["url"].rstrip("/") + "/embeddings", body, headers)
        rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
        if len(rows) != len(body["input"]):
            raise EmbeddingError(f"embedding API returned {len(rows)} vectors for {len(body['input'])} texts")
        out += [r["embedding"] for r in rows]
    return out


def _fake(texts, cfg):
    """Deterministic bag-of-words vectors: no model, for the test suite."""
    out = []
    for text in texts:
        v = [0.0] * 16
        for word in re.findall(r"\w+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 16] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


_PROVIDERS = {"local": _local, "ollama": _ollama, "openai": _openai, "fake": _fake}


def embed_texts(texts, cfg=None):
    cfg = cfg or config.embedding_config()
    if cfg["provider"] == "none":
        raise EmbeddingError("embeddings are disabled")
    vectors = _PROVIDERS[cfg["provider"]](texts, cfg)
    if len(vectors) != len(texts):
        raise EmbeddingError(f"provider returned {len(vectors)} vectors for {len(texts)} texts")
    return [[round(float(x), 5) for x in v] for v in vectors]


# ---------------------------------------------------------------- store side

def marker_path():
    return store.home() / "embeddings.json"


def read_marker():
    """What the vectors in this store were built with: {provider, model, dimensions}."""
    try:
        return json.loads(marker_path().read_text("utf-8"))
    except (OSError, ValueError):
        return None


def marker_matches(cfg, marker=None):
    marker = marker if marker is not None else read_marker()
    return bool(marker) and (marker["provider"], marker["model"]) == (cfg["provider"], cfg["model"])


def _drop_vectors():
    with store.write_lock():
        store.run(f"DROP INDEX {VECTOR_INDEX} IF EXISTS")
        store.run("MATCH (n:Searchable) WHERE n.embedding IS NOT NULL SET n.embedding = null")
        try:
            marker_path().unlink()
        except OSError:
            pass


def _create_index(cfg, dim):
    with store.write_lock():
        store.run(f"CREATE VECTOR INDEX {VECTOR_INDEX} IF NOT EXISTS FOR (n:Searchable) ON (n.embedding) "
                  f"OPTIONS {{indexConfig: {{`vector.dimensions`: {int(dim)}, "
                  f"`vector.similarity_function`: 'cosine'}}}}")
        marker = {"provider": cfg["provider"], "model": cfg["model"], "dimensions": int(dim),
                  "at": store.now_iso()}
        marker_path().write_text(json.dumps(marker), "utf-8")
    return marker


def embed_pass(quiet=False, reset=False):
    """Embed every searchable node whose text has no vector yet (new or changed text).
    A store built with another provider or model is re-embedded from scratch."""
    cfg = config.embedding_config()
    say = (lambda *a: None) if quiet else (lambda msg: print(msg, flush=True))
    if cfg["provider"] == "none":
        say("embeddings are off (provider: none); search is full-text only")
        return 0
    store.ensure_store()
    try:
        guard = store.file_lock("embed.lock", timeout=0).__enter__()
    except store.Busy:
        say("an embedding pass is already running")
        return 0
    try:
        marker = read_marker()
        if marker and (reset or not marker_matches(cfg, marker)):
            embed_texts(["probe"], cfg)          # fail here, before dropping the vectors we have
            say(f"embedding model changed ({marker['provider']}:{marker['model']} -> "
                f"{cfg['provider']}:{cfg['model']}): rebuilding all vectors")
            _drop_vectors()
            marker = None
        total = 0
        for _ in range(1000):
            pending = []
            for label in EMBED_LABELS:
                rows = store.read(f"MATCH (n:{label}) WHERE n.embedding IS NULL AND n.text IS NOT NULL "
                                  f"RETURN n.id AS id, n.title AS title, n.text AS text LIMIT 1500")
                pending += [(label, r) for r in rows]
                if len(pending) >= 1500:
                    break
            if not pending:
                break
            vectors = embed_texts([f"{r['title'] or ''}\n{r['text']}"[:MAX_CHARS] for _, r in pending], cfg)
            dim = len(vectors[0])
            if marker is None:
                marker = _create_index(cfg, dim)
            elif dim != marker["dimensions"]:
                raise EmbeddingError(f"model returned {dim} dimensions, the store holds "
                                     f"{marker['dimensions']}: run `relate-codemem embed --reset`")
            for start in range(0, len(pending), 250):
                by_label = {}
                for (label, r), v in zip(pending[start:start + 250], vectors[start:start + 250]):
                    by_label.setdefault(label, []).append({"id": r["id"], "v": v})
                with store.write_lock():
                    for label, rows in by_label.items():
                        store.write(f"UNWIND $p.rows AS r MATCH (n:{label} {{id: r.id}}) "
                                    f"SET n.embedding = vector(r.v, {dim}, FLOAT32)", {"rows": rows})
            total += len(pending)
            say(f"  embedded {total} item(s)")
        if total:
            store.log(f"embedded {total} item(s) with {cfg['provider']}:{cfg['model']}")
        say(f"embeddings up to date ({total} computed, {cfg['provider']}:{cfg['model']})")
        return total
    finally:
        guard.__exit__(None, None, None)


def embed_in_background():
    """Hooks must return quickly: the embedding pass runs in a detached process."""
    try:
        if config.embedding_config()["provider"] == "none":
            return
        env = dict(os.environ, **{config.ENV + "PROJECT": str(config.project_root())})
        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP. Not DETACHED_PROCESS: a process
        # without any console makes every relate call it starts open a window.
        kwargs = {"creationflags": 0x08000000 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
        with open(store.home() / "memory.log", "a") as out:
            subprocess.Popen([sys.executable, "-m", "relate_codemem", "embed", "--quiet"],
                             stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env, **kwargs)
    except (OSError, ValueError) as exc:
        store.log(f"background embed not started: {exc}")

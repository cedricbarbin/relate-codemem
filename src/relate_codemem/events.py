"""Claude Code hooks: every event is appended to a spool, then flushed into the graph."""
import json
import sys
import time

from . import embeddings, indexer, store
from .extract import cfg, sha
from .indexer import git_info, rel_path


def spool_dir():
    return store.home() / "spool"


EVENTS_SCRIPT = """
UNWIND $p.sessions AS e
MERGE (s:Session {id: e.id}) ON CREATE SET s.startedAt = datetime(e.at)
SET s.lastAt = datetime(e.last), s.cwd = coalesce(e.cwd, s.cwd),
    s.source = coalesce(e.source, s.source), s.branch = coalesce(e.branch, s.branch),
    s.gitCommit = coalesce(e.commit, s.gitCommit), s.endedAt = CASE WHEN e.ended IS NULL THEN s.endedAt ELSE datetime(e.ended) END,
    s.endReason = coalesce(e.reason, s.endReason);

UNWIND $p.prompts AS e
MATCH (s:Session {id: e.session})
MERGE (x:Prompt {id: e.id})
  ON CREATE SET x:Searchable, x.title = e.title, x.text = e.text, x.at = datetime(e.at), x.session = e.session
MERGE (x)-[:IN_SESSION]->(s);

UNWIND $p.replies AS e
MATCH (s:Session {id: e.session})
MERGE (x:Reply {id: e.id})
  ON CREATE SET x:Searchable, x.title = e.title, x.text = e.text, x.at = datetime(e.at), x.session = e.session
MERGE (x)-[:IN_SESSION]->(s)
WITH x, s
MATCH (q:Prompt)-[:IN_SESSION]->(s) WHERE q.at <= x.at
WITH x, q ORDER BY q.at DESC
WITH x, head(collect(q)) AS last
MERGE (x)-[:ANSWERS]->(last);

UNWIND $p.tools AS e
MATCH (s:Session {id: e.session})
MERGE (x:ToolCall {id: e.id})
  ON CREATE SET x.tool = e.tool, x.at = datetime(e.at), x.summary = e.summary, x.path = e.path, x.session = e.session
MERGE (x)-[:IN_SESSION]->(s);

UNWIND $p.tools AS e
MATCH (x:ToolCall {id: e.id}) WHERE e.path IS NOT NULL
MERGE (f:File {path: e.path})
MERGE (x)-[:MODIFIED]->(f);

UNWIND $p.reads AS e
MATCH (s:Session {id: e.session})
MATCH (f:File {path: e.path})
MERGE (s)-[r:READ]->(f)
SET r.count = coalesce(r.count, 0) + e.count, r.lastAt = datetime(e.at)
"""


def spool(event):
    spool_dir().mkdir(parents=True, exist_ok=True)
    with store.file_lock("spool.lock", 15):
        with open(spool_dir() / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def flush():
    """Move spooled hook events into the graph. Idempotent: every node is MERGEd on its id."""
    store.ensure_store()
    with store.write_lock():
        current = spool_dir() / "events.jsonl"
        with store.file_lock("spool.lock", 15):
            if current.exists():
                current.replace(spool_dir() / f"batch-{time.time_ns()}.jsonl")
        total = 0
        for batch in sorted(spool_dir().glob("batch-*.jsonl")):
            events = []
            for line in batch.read_text("utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
            sessions, reads = {}, {}
            p = {"prompts": [], "replies": [], "tools": []}
            for e in events:
                s = sessions.setdefault(e["session"], {"id": e["session"], "at": e["at"], "last": e["at"],
                                                       "cwd": None, "source": None, "branch": None,
                                                       "commit": None, "ended": None, "reason": None})
                s["last"] = max(s["last"], e["at"])
                s["at"] = min(s["at"], e["at"])
                kind = e["type"]
                if kind == "start":
                    s.update({k: e.get(k) or s[k] for k in ("cwd", "source", "branch", "commit")})
                elif kind == "end":
                    s.update(ended=e["at"], reason=e.get("reason"))
                elif kind == "prompt":
                    p["prompts"].append(e)
                elif kind == "reply":
                    p["replies"].append(e)
                elif kind == "tool":
                    p["tools"].append(e)
                elif kind == "read":
                    r = reads.setdefault((e["session"], e["path"]), dict(e, count=0))
                    r["count"] += 1
                    r["at"] = max(r["at"], e["at"])
            p["sessions"] = list(sessions.values())
            p["reads"] = list(reads.values())
            if events:
                store.write(EVENTS_SCRIPT, p)
            batch.unlink()
            total += len(events)
    return total


def last_assistant_text(transcript_path):
    """Text of the assistant's last turn, read from the tail of the session transcript."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 3_000_000))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    texts = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = entry.get("message") or {}
        content = msg.get("content")
        if entry.get("type") == "assistant":
            if isinstance(content, list):
                part = "\n".join(b.get("text", "") for b in content
                                 if isinstance(b, dict) and b.get("type") == "text")
                if part.strip():
                    texts.insert(0, part.strip())
        elif entry.get("type") == "user":
            is_prompt = isinstance(content, str) or (isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "text" for b in content))
            if is_prompt and not entry.get("isMeta"):
                break
    return "\n\n".join(texts)


def hook():
    """Entry point of every Claude Code hook. Never fails the session: errors go to the log."""
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except ValueError:
        payload = {}
    event = payload.get("hook_event_name") or ""
    session = payload.get("session_id") or "unknown"
    at = store.now_iso()
    uid = f"{session}:{time.time_ns() // 1000}"
    store.home().mkdir(parents=True, exist_ok=True)
    try:
        (store.home() / "current_session").write_text(session)
    except OSError:
        pass
    base = {"session": session, "at": at}
    tool = payload.get("tool_name") or ""
    tin = payload.get("tool_input") or {}

    if event == "SessionStart":
        spool(dict(base, type="start", cwd=payload.get("cwd"), source=payload.get("source"), **git_info()))
        flush()
        indexer.index(session=None, quiet=True)          # catch up with changes made outside sessions
        embeddings.embed_in_background()
        rows = store.read("MATCH (f:File) WHERE f.deleted = false WITH count(f) AS files "
                    "RETURN files, COUNT { (s:Symbol) } AS symbols, "
                    "COUNT { (d:Decision) WHERE d.closedAt IS NULL } AS decisions, "
                    "COUNT { (m:Meeting) } AS meetings, COUNT { (s:Session) } AS sessions")
        c = rows[0] if rows else {}
        print(f"[relate-codemem] project memory graph ready: {c.get('files', 0)} files, "
              f"{c.get('symbols', 0)} symbols, {c.get('decisions', 0)} open decisions, "
              f"{c.get('meetings', 0)} meetings, {c.get('sessions', 0)} sessions. "
              f"Query it with `relate-codemem` (see CLAUDE.md).")
    elif event == "UserPromptSubmit":
        text = (payload.get("prompt") or "").strip()
        if text:
            spool(dict(base, type="prompt", id=uid, title=" ".join(text.split())[:120],
                       text=text[:cfg()["max_prompt_chars"]]))
            flush()
    elif event == "PostToolUse":
        path = rel_path(tin.get("file_path") or tin.get("notebook_path") or "") \
            if (tin.get("file_path") or tin.get("notebook_path")) else None
        if tool == "Read":
            if path:
                spool(dict(base, type="read", path=path))          # cheap: flushed later
        elif tool == "Bash":
            spool(dict(base, type="tool", id=uid, tool=tool, path=None,
                       summary=(tin.get("command") or "")[:500]))
        else:                                                       # Edit / Write / MultiEdit / NotebookEdit
            spool(dict(base, type="tool", id=uid, tool=tool, path=path, summary=path or ""))
            flush()
            if path:
                indexer.index([path], session=session, quiet=True)
    elif event in ("Stop", "SubagentStop", "PreCompact", "SessionEnd"):
        if event == "Stop":
            text = (payload.get("last_assistant_message") or "").strip() \
                or last_assistant_text(payload.get("transcript_path") or "")
            if text:
                spool(dict(base, type="reply", id=f"{session}:r:{sha(text)[:12]}",
                           title=" ".join(text.split())[:120], text=text[:cfg()["max_reply_chars"]]))
        if event == "SessionEnd":
            spool(dict(base, type="end", reason=payload.get("reason")))
        flush()
        indexer.index(session=session, quiet=True)       # Bash, git, generators: anything that moved files
        if event != "SubagentStop":
            embeddings.embed_in_background()

"""End-to-end against a real relate store (skipped when the binary is absent)."""
import io
import json
import sys

from conftest import needs_relate
from relate_codemem import cli, config, embeddings, events, indexer, project as setup, store

pytestmark = needs_relate

CONFIG_PY = '''from .util import read_yaml


def parse_config(path):
    """Load the YAML configuration file."""
    return read_yaml(path)
'''


def seed(root):
    (root / "src" / "app").mkdir(parents=True)
    (root / "src" / "app" / "config.py").write_text(CONFIG_PY)
    (root / "src" / "app" / "util.py").write_text("def read_yaml(path):\n    return {}\n")
    (root / "docs" / "decisions").mkdir(parents=True)
    (root / "docs" / "decisions" / "0001-postgres.md").write_text(
        "---\nstatus: accepted\ndate: 2026-10-02\n---\n# Use PostgreSQL as primary store\n\nWe need transactions.\n")
    (root / "docs" / "meetings").mkdir()
    (root / "docs" / "meetings" / "2026-11-20-kickoff.md").write_text(
        "# Kickoff\n\nParticipants: Alice Martin, Bob Chen\n\nWe ship the billing module first.\n")


def q(cypher):
    return store.read(cypher)


def hook(monkeypatch, **payload):
    data = json.dumps(payload).encode()
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(data)})())
    monkeypatch.setattr(embeddings, "embed_in_background", lambda: None)
    events.hook()


def test_init_writes_project_files_idempotently(project, monkeypatch, capsys):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"allow": ["Bash(ls:*)"]}, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}))
    assert cli.main(["init", str(project), "--no-index"]) == 0
    assert cli.main(["init", str(project), "--no-index"]) == 0
    settings = json.loads((project / ".claude" / "settings.json").read_text())
    stop = [h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]]
    assert stop == ["echo hi", "relate-codemem hook"]
    assert set(setup.HOOKS) <= set(settings["hooks"])
    assert "Bash(ls:*)" in settings["permissions"]["allow"] and "Bash(relate-codemem:*)" in settings["permissions"]["allow"]
    assert (project / "CLAUDE.md").read_text().count(setup.MARK_BEGIN) == 1
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"]["relate-codemem"]["args"] == ["mcp"]
    assert (project / "docs" / "decisions" / "README.md").exists()
    assert ".claude/relate/" in (project / ".gitignore").read_text()


def test_init_records_a_project_embedding_override(project):
    assert cli.main(["init", str(project), "--no-index", "--embeddings", "ollama", "--model", "bge-m3"]) == 0
    env = json.loads((project / ".claude" / "settings.local.json").read_text())["env"]
    assert env == {"RELATE_CODEMEM_EMBEDDINGS": "ollama", "RELATE_CODEMEM_EMBEDDING_MODEL": "bge-m3"}
    assert config.embedding_config()["source"] == ".claude/settings.local.json"


def test_hash_and_revision_lifecycle(project, monkeypatch):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    seed(project)
    assert indexer.index(quiet=True) == (4, 0)
    assert indexer.index(quiet=True) == (0, 0)                       # nothing changed: no new revision

    path = project / "src" / "app" / "config.py"
    path.write_text(CONFIG_PY.replace("return read_yaml(path)", "return read_yaml(path) or {}"))
    assert indexer.index(quiet=True) == (1, 0)
    f = q("MATCH (f:File {path: 'src/app/config.py'}) RETURN f.revision AS rev, f.hash AS hash")[0]
    assert f["rev"] == 2 and len(f["hash"]) == 64
    chain = q("MATCH (:FileRevision {id: 'src/app/config.py@2'})-[:PREVIOUS]->(o) RETURN o.id AS id")
    assert chain == [{"id": "src/app/config.py@1"}]
    sym = q("MATCH (s:Symbol {name: 'parse_config'}) RETURN s.sinceRev AS since, s.changedRev AS changed")[0]
    assert sym == {"since": 1, "changed": 2}
    deps = q("MATCH (:File {path: 'src/app/config.py'})-[:DEPENDS_ON]->(t) RETURN t.path AS p")
    assert deps == [{"p": "src/app/util.py"}]

    path.unlink()
    assert indexer.index(quiet=True) == (0, 1)
    gone = q("MATCH (f:File {path: 'src/app/config.py'}) RETURN f.deleted AS deleted, f.revision AS rev, "
             "COUNT { (f)<-[:DEFINED_IN]-() } AS symbols")[0]
    assert gone == {"deleted": True, "rev": 3, "symbols": 0}

    path.write_text(CONFIG_PY)
    indexer.index(quiet=True)
    assert q("MATCH (f:File {path: 'src/app/config.py'}) RETURN f.revision AS rev, f.deleted AS d")[0] == {"rev": 4, "d": False}


def test_docs_decisions_meetings_and_search(project, monkeypatch, capsys):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    seed(project)
    indexer.index(quiet=True)
    assert q("MATCH (d:Decision) RETURN d.title AS t, d.status AS s, d.date AS date")[0] == {
        "t": "Use PostgreSQL as primary store", "s": "accepted", "date": "2026-10-02"}
    people = q("MATCH (p:Person)-[:ATTENDED]->(m:Meeting) RETURN p.name AS n, m.date AS d ORDER BY n")
    assert people == [{"n": "Alice Martin", "d": "2026-11-20"}, {"n": "Bob Chen", "d": "2026-11-20"}]
    capsys.readouterr()
    assert cli.main(["search", "postgresql", "--kind", "decision"]) == 0
    assert "docs/decisions/0001-postgres.md" in capsys.readouterr().out
    assert cli.main(["search", "config", "--kind", "code"]) == 0
    assert "parse_config" in capsys.readouterr().out


def test_decisions_supersede_by_slot(project, monkeypatch, capsys):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    assert cli.main(["decide", "--title", "Strict by default", "--text", "Reject unknown keys.", "--slot", "config/strict"]) == 0
    assert cli.main(["decide", "--title", "Strict is opt-in", "--text", "Default is lenient.", "--slot", "config/strict"]) == 0
    rows = q("MATCH (d:Decision) RETURN d.title AS t, d.status AS s, d.closedAt IS NULL AS open ORDER BY t")
    assert rows == [{"t": "Strict by default", "s": "superseded", "open": False},
                    {"t": "Strict is opt-in", "s": "accepted", "open": True}]
    assert q("MATCH (:Decision {title: 'Strict is opt-in'})-[:SUPERSEDES]->(o) RETURN o.title AS t") == [{"t": "Strict by default"}]


def test_hooks_record_a_session(project, monkeypatch, capsys):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    seed(project)
    hook(monkeypatch, hook_event_name="SessionStart", session_id="s1", cwd=str(project), source="startup")
    assert "project memory graph ready: 4 files" in capsys.readouterr().out
    hook(monkeypatch, hook_event_name="UserPromptSubmit", session_id="s1", prompt="Make parse_config strict")
    target = project / "src" / "app" / "config.py"
    hook(monkeypatch, hook_event_name="PostToolUse", session_id="s1", tool_name="Read", tool_input={"file_path": str(target)})
    target.write_text(CONFIG_PY + "\n# strict\n")
    hook(monkeypatch, hook_event_name="PostToolUse", session_id="s1", tool_name="Edit", tool_input={"file_path": str(target)})
    hook(monkeypatch, hook_event_name="PostToolUse", session_id="s1", tool_name="Bash", tool_input={"command": "pytest -q"})
    hook(monkeypatch, hook_event_name="Stop", session_id="s1", last_assistant_message="parse_config is strict now.")
    hook(monkeypatch, hook_event_name="SessionEnd", session_id="s1", reason="exit")

    assert q("MATCH (p:Prompt)-[:IN_SESSION]->(s:Session {id: 's1'}) RETURN p.text AS t, s.endReason AS r") == [
        {"t": "Make parse_config strict", "r": "exit"}]
    assert q("MATCH (r:Reply)-[:ANSWERS]->(p:Prompt) RETURN r.text AS t") == [{"t": "parse_config is strict now."}]
    assert q("MATCH (:Session {id: 's1'})-[:PRODUCED]->(v) RETURN v.id AS id") == [{"id": "src/app/config.py@2"}]
    assert q("MATCH (t:ToolCall)-[:MODIFIED]->(f) RETURN t.tool AS tool, f.path AS p") == [
        {"tool": "Edit", "p": "src/app/config.py"}]
    assert q("MATCH (:Session)-[r:READ]->(f) RETURN r.count AS n, f.path AS p") == [{"n": 1, "p": "src/app/config.py"}]
    assert q("MATCH (t:ToolCall {tool: 'Bash'}) RETURN t.summary AS s") == [{"s": "pytest -q"}]


def test_embeddings_follow_text_and_model_changes(project, monkeypatch, capsys):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "fake")
    seed(project)
    indexer.index(quiet=True)
    total = embeddings.embed_pass(quiet=True)
    assert total > 0 and embeddings.embed_pass(quiet=True) == 0
    assert embeddings.read_marker()["dimensions"] == 16

    doc = project / "docs" / "decisions" / "0001-postgres.md"
    doc.write_text(doc.read_text().replace("We need transactions.", "We need transactions and JSON columns."))
    indexer.index(quiet=True)
    assert embeddings.embed_pass(quiet=True) == 2                    # the Decision node and its changed chunk

    capsys.readouterr()
    assert cli.main(["search", "transactions JSON columns", "-k", "3"]) == 0
    assert "[text+vec]" in capsys.readouterr().out

    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDING_MODEL", "hash-16-b")   # another model: vectors are rebuilt
    assert embeddings.embed_pass(quiet=True) == total
    assert embeddings.read_marker()["model"] == "hash-16-b"


def test_crlf_files_index_like_lf_files(project, monkeypatch):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    seed(project)
    notes = project / "docs" / "meetings" / "2026-11-20-kickoff.md"
    notes.write_bytes(notes.read_text().replace("\n", "\r\n").encode())
    indexer.index(quiet=True)
    assert q("MATCH (p:Person) RETURN p.name AS n ORDER BY n") == [{"n": "Alice Martin"}, {"n": "Bob Chen"}]

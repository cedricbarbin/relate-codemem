from relate_codemem import extract

PY = '''"""Module."""
import os
from .util import read_yaml


class Settings:
    """Runtime settings."""

    def reload(self):
        return parse_config(self.path)


async def parse_config(path: str, strict: bool = False) -> dict:
    """Load the YAML configuration file."""
    return read_yaml(path)
'''

TS = '''import { retry } from './retry';
// Fetches a user by id.
export async function getUserById(id: string) { return retry(() => fetch(id)); }
export class HttpClient {
  get(url: string) { return fetch(url); }
}
export const buildUrl = (base: string, p: string) => base + p;
export interface UserDto { id: string }
'''


def names(symbols):
    return {(s["kind"], s["qualname"]) for s in symbols}


def test_python_symbols(project):
    symbols = extract.extract_symbols("src/app/config.py", PY, "python", 1)
    assert names(symbols) == {("class", "Settings"), ("method", "Settings.reload"),
                              ("function", "parse_config")}
    fn = next(s for s in symbols if s["name"] == "parse_config")
    assert fn["id"] == "src/app/config.py::parse_config"
    assert fn["signature"].startswith("async def parse_config(path: str")
    assert "Load the YAML" in fn["text"]
    assert (fn["startLine"], fn["endLine"]) == (13, 15)


def test_symbol_hash_tracks_the_body(project):
    before = extract.extract_symbols("a.py", PY, "python", 1)
    after = extract.extract_symbols("a.py", PY.replace("return read_yaml(path)", "return {}"), "python", 2)
    changed = {a["qualname"] for a, b in zip(after, before) if a["hash"] != b["hash"]}
    assert changed == {"parse_config"}


def test_typescript_symbols(project):
    symbols = extract.extract_symbols("web/client.ts", TS, "typescript", 1)
    assert names(symbols) == {("function", "getUserById"), ("class", "HttpClient"), ("method", "get"),
                              ("function", "buildUrl"), ("interface", "UserDto")}
    assert "Fetches a user by id." in next(s for s in symbols if s["name"] == "getUserById")["text"]


def test_broken_python_falls_back_to_patterns(project):
    symbols = extract.extract_symbols("a.py", "def ok(:\n    pass\nclass K:\n    pass\n", "python", 1)
    assert ("class", "K") in names(symbols)


def test_imports_resolve_to_project_paths(project):
    py = dict(extract.extract_imports("src/app/config.py", PY, "python"))
    assert "src/app/util.py" in py[".util"]
    ts = dict(extract.extract_imports("web/client.ts", TS, "typescript"))
    assert "web/retry.ts" in ts["./retry"]


def test_split_terms():
    terms = extract.split_terms("parseConfigFile", "user_repository").split()
    assert {"parse", "config", "file", "user", "repository", "parseconfigfile"} <= set(terms)


def test_classify(project):
    assert extract.classify("src/a.py") == ("code", "python")
    assert extract.classify("docs/guide.md") == ("doc", "markdown")
    assert extract.classify("docs/decisions/0001-x.md") == ("decision", "markdown")
    assert extract.classify("docs/decisions/README.md") is None
    assert extract.classify("docs/meetings/2026-01-05-sync.vtt") == ("meeting", "text")
    assert extract.classify("logo.png") is None
    assert extract.excluded("node_modules/x/index.js") and not extract.excluded("src/index.js")


def test_chunks_follow_headings(project):
    text = "---\ntitle: T\n---\n# Guide\n\nIntro.\n\n## Storage\n\nPostgres.\n\n## Auth\n\nTokens.\n"
    meta, chunks = extract.chunk_text("docs/guide.md", text, "doc")
    assert meta == {"title": "T"}
    assert [c["title"] for c in chunks] == ["Guide", "Guide > Storage", "Guide > Auth"]
    assert chunks[1]["startLine"] == 8 and chunks[1]["text"] == "Postgres."


def test_decision_and_meeting_metadata(project):
    adr = "---\nstatus: superseded by 0002\ndate: 2026-10-02\n---\n# Use PostgreSQL\n"
    meta, _ = extract.chunk_text("docs/decisions/0001-pg.md", adr, "decision")
    d = extract.parse_decision("docs/decisions/0001-pg.md", adr, meta)
    assert (d["title"], d["date"], d["closed"]) == ("Use PostgreSQL", "2026-10-02", True)
    notes = "# Kickoff\n\nAlice Martin: we ship billing first.\nBob Chen: agreed.\nAlice Martin: good.\nBob Chen: ok.\n"
    m = extract.parse_meeting("docs/meetings/2026-11-20-kickoff.md", notes, {})
    assert (m["title"], m["date"]) == ("Kickoff", "2026-11-20")
    assert sorted(m["participants"]) == ["Alice Martin", "Bob Chen"]


def test_transcript_cues_are_dropped():
    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\n<v Alice>Hello there</v>\n"
    assert extract.clean_transcript(vtt).strip() == "Alice: Hello there"

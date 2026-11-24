"""The read-only MCP server answers over stdio, beside a writer."""
import json
import os
import queue
import subprocess
import sys
import threading
import time

from conftest import needs_relate
from relate_codemem import cli, config, mcp, parse, queries, store

pytestmark = needs_relate

TIMEOUT = 40


class Client:
    def __init__(self, root):
        env = dict(os.environ, **{config.ENV + "PROJECT": str(root)})
        self.proc = subprocess.Popen([sys.executable, "-m", "relate_codemem", "mcp"], cwd=root, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     **config.NO_WINDOW)
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0

    def _pump(self):
        for raw in self.proc.stdout:
            self.lines.put(raw)
        self.lines.put(None)

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self.next_id += 1
            message["id"] = self.next_id
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        self.proc.stdin.flush()
        if notify:
            return None
        while True:
            try:
                raw = self.lines.get(timeout=TIMEOUT)
            except queue.Empty:
                raise AssertionError(f"no answer to {method} within {TIMEOUT}s") from None
            assert raw is not None, f"server exited during {method}: {self.proc.stderr.read().decode()[-800:]}"
            answer = json.loads(raw)
            if answer.get("id") == message["id"]:
                assert "error" not in answer, answer
                return answer["result"]

    def close(self):
        self.proc.kill()
        self.proc.wait(10)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            stream.close()


def test_mcp_server_answers_and_sees_later_writes(project, capsys):
    (project / "a.py").write_text("def f():\n    return 1\n")
    assert cli.main(["index", "--no-embed"]) == 0
    client = Client(project)
    try:
        hello = client.send("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                           "clientInfo": {"name": "test", "version": "0"}})
        assert "serverInfo" in hello
        client.send("notifications/initialized", notify=True)
        tools = {tool["name"]: tool for tool in client.send("tools/list")["tools"]}
        assert "read_cypher" in tools and not any("write" in name for name in tools)

        # a writer works while the server is open, and the server sees the commit
        assert cli.main(["note", "written", "beside", "the", "server", "--slot", "mcp/test"]) == 0
        # (the live server follows commits with a short delay)
        deadline = time.monotonic() + 15
        while True:
            found = json.dumps(client.send("tools/call", {"name": "read_cypher", "arguments": {
                "statement": "MATCH (n:Note) RETURN n.text AS text LIMIT 5"}}))
            if "written beside the server" in found or time.monotonic() > deadline:
                break
            time.sleep(0.3)
        assert "written beside the server" in found
    finally:
        client.close()


def call(tool, **arguments):
    answer = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": tool, "arguments": arguments}})["result"]
    return answer["content"][0]["text"], answer["isError"]


def test_standard_questions_are_tools_and_prompts(project, monkeypatch):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    (project / "app").mkdir()
    (project / "app" / "repo.py").write_text(
        "import json\n\n\nclass Repo:\n    def save(self, x):\n        raise NotImplementedError\n\n\n"
        "class FileRepo(Repo):\n    def save(self, x):\n        return json.dumps(x)\n")
    (project / "app" / "service.py").write_text(
        "from .repo import Repo\n\n\nclass Service:\n    def run(self, repo: Repo, x):\n        return repo.save(x)\n\n\n"
        "def main():\n    return Service().run(None, 1)\n")
    assert cli.main(["index", "--no-embed"]) == 0

    listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in listed} == {"search", "recall", "file_info", "symbol_info", "class_calls",
                                           "implementations", "impact", "get_schema", "read_cypher"}
    assert all(t["annotations"]["readOnlyHint"] and t["inputSchema"]["type"] == "object" for t in listed)

    text, error = call("symbol_info", name="Repo")
    assert not error and "class Repo" in text
    text, error = call("file_info", path="app/repo.py")
    assert not error and "json (stdlib)" in text
    text, error = call("get_schema")
    assert "CALLS" in text and not error
    unknown = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope"}})
    assert unknown["error"]["code"] == -32602
    text, error = call("impact")
    assert error and "missing" in text

    if parse.available():               # the call graph needs the parse extra
        text, _ = call("class_calls", name="Service")
        assert "Repo.save" in text and "from Service.run" in text
        text, _ = call("class_calls", name="FileRepo")
        assert "json.dumps  (stdlib)" in text
        text, _ = call("implementations", name="Repo")
        assert "class FileRepo" in text
        text, _ = call("implementations", name="Repo.save")
        assert "FileRepo.save" in text
        # FileRepo.save is reached through the calls made on Repo.save, two calls away from main
        text, _ = call("impact", name="FileRepo.save", depth=2)
        assert "also reached through calls on: Repo.save" in text and "Service.run" in text
        assert "callers 2 calls away" in text and "function main" in text and "app/service.py" in text
        capsys_text = queries.impact("Repo")
        assert "subtypes (1): FileRepo" in capsys_text

    prompts = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "prompts/list"})["result"]["prompts"]
    assert {p["name"] for p in prompts} == {"ask", "class-calls", "implementations", "impact"}
    got = mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "prompts/get",
                      "params": {"name": "impact", "arguments": {"name": "Customer.save"}}})["result"]
    assert "Customer.save" in got["messages"][0]["content"]["text"]
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert mcp.handle({"jsonrpc": "2.0", "id": 5, "method": "nope"})["error"]["code"] == -32601


def test_read_cypher_cannot_write(project):
    (project / "a.py").write_text("def f():\n    return 1\n")
    assert cli.main(["index", "--no-embed"]) == 0
    text, error = call("read_cypher", statement="CREATE (n:Note {id: 'x', text: 'written through MCP'})")
    assert error
    assert store.read("MATCH (n:Note {id: 'x'}) RETURN count(n) AS n") == [{"n": 0}]
    text, error = call("read_cypher", statement="MATCH (f:File {path: $p.path}) RETURN f.path AS path", params={"path": "a.py"})
    assert not error and json.loads(text) == [{"path": "a.py"}]


def test_playground_serves_the_store_read_only(project):
    import socket
    import urllib.request
    (project / "a.py").write_text("def f():\n    return 1\n")
    assert cli.main(["index", "--no-embed"]) == 0
    ports = []
    for _ in range(2):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            ports.append(s.getsockname()[1])
    env = dict(os.environ, **{config.ENV + "PROJECT": str(project)})
    proc = subprocess.Popen([sys.executable, "-m", "relate_codemem", "playground", "--bolt", str(ports[0]),
                             "--http", str(ports[1])], cwd=project, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **config.NO_WINDOW)
    try:
        deadline, document = time.monotonic() + 30, None
        while document is None and time.monotonic() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{ports[1]}/", timeout=2) as response:
                    document = json.loads(response.read())
            except OSError:
                time.sleep(0.3)
        assert document is not None, "the HTTP surface did not answer"
        assert document["bolt_direct"].endswith(str(ports[0]))
    finally:
        proc.kill()
        proc.wait(10)
        proc.stdout.close()

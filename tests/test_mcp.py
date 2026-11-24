"""The read-only MCP server answers over stdio, beside a writer."""
import json
import os
import queue
import subprocess
import sys
import threading
import time

from conftest import needs_relate
from relate_codemem import cli, config

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

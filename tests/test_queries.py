"""The standard questions, on a small Python project with tests and a git history."""
import shutil
import subprocess

import pytest

from conftest import needs_relate
from relate_codemem import cli, config, mcp, parse, queries

pytestmark = needs_relate
needs_calls = pytest.mark.skipif(not parse.available(), reason="the call graph needs the parse extra")

FILES = {
    "shop/__init__.py": "",
    "shop/mail.py": "import smtplib\n\n\nclass Mailer:\n    def send(self, to):\n        return smtplib.SMTP(to)\n",
    "shop/orders.py": "import requests\n\nfrom shop import mail\n\n\nclass Orders:\n    def create(self, x):\n"
                      "        requests.post(x)\n        return self.notify(x)\n\n    def notify(self, x):\n"
                      "        return mail.Mailer().send(x)\n\n\ndef unused():\n    return 1\n",
    "tests/test_orders.py": "from shop.orders import Orders\n\n\ndef test_create():\n    assert Orders().create(1)\n",
}


@pytest.fixture
def shop(project, monkeypatch):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    for path, text in FILES.items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(text)
    assert cli.main(["index", "--no-embed"]) == 0
    return project


def test_test_files_are_recognised_by_name_or_directory():
    yes = ["tests/test_a.py", "pkg/a_test.go", "src/test/java/p/FooTest.java", "web/a.test.ts", "web/a.spec.tsx",
           "spec/models/user_spec.rb", "app/__tests__/a.js", "Billing.Tests/InvoiceTests.cs"]
    no = ["src/app/contest.py", "src/latest.py", "src/main/java/p/Attestation.java", "web/testimony.ts"]
    assert [p for p in yes if not queries.is_test(p)] == [] and [p for p in no if queries.is_test(p)] == []


def test_a_submodule_imported_by_name_is_a_dependency(shop):
    text = queries.library_usage()
    assert "External libraries (1):" in text and "requests  imported by 1 file(s)" in text and "smtplib" in text
    from relate_codemem import store
    assert store.read("MATCH (:File {path: 'shop/orders.py'})-[:DEPENDS_ON]->(t) RETURN t.path AS p ORDER BY p") == [
        {"p": "shop/__init__.py"}, {"p": "shop/mail.py"}]
    # the guess `shop.mail` did not become an import of its own
    assert store.read("MATCH (:File {path: 'shop/orders.py'})-[:IMPORTS]->(m) RETURN m.name AS m ORDER BY m") == [
        {"m": "requests"}, {"m": "shop"}]


def test_why_gathers_decisions_documents_and_sessions(shop, capsys):
    (shop / "docs").mkdir()
    (shop / "docs" / "design.md").write_text("# Design\n\nOrders are created in shop/orders.py, one at a time.\n")
    assert cli.main(["decide", "--title", "Orders notify by mail", "--text", "Every order sends one mail.",
                     "--rationale", "Customers asked for it.", "--files", "shop/orders.py"]) == 0
    assert cli.main(["index", "--no-embed"]) == 0
    capsys.readouterr()
    assert cli.main(["why", "shop/orders.py"]) == 0
    text = capsys.readouterr().out
    assert "Orders notify by mail" in text and "Customers asked for it." in text and "docs/design.md" in text
    assert text.count("Customers asked for it.") == 1
    assert "No decision, note, document or recorded session" in queries.why("shop/mail.py")
    assert "not in the memory graph" in queries.why("nope.py")


@needs_calls
def test_call_path_library_usage_and_tests(shop, capsys):
    assert cli.main(["path", "test_create", "Mailer.send"]) == 0
    text = capsys.readouterr().out
    assert "3 step(s) from test_create to Mailer.send" in text
    assert [line.split("  <")[0].strip() for line in text.splitlines()[1:]] == [
        "function test_create", "calls method Orders.create", "calls method Orders.notify", "calls method Mailer.send"]
    assert "No chain of recorded calls" in queries.call_path("Mailer.send", "test_create")

    text = queries.library_usage("requests")
    assert "imported by 1 file(s)" in text and "requests.post   from Orders.create <shop/orders.py>" in text
    assert "Nothing recorded" in queries.library_usage("numpy")

    text = queries.tests_for("Mailer.send")
    assert "3  function test_create  <tests/test_orders.py:4>" in text and "test files (1): tests/test_orders.py" in text
    assert "no test reaches it" in queries.tests_for("unused")
    answer = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "tests_for", "arguments": {"name": "Orders"}}})["result"]
    assert not answer["isError"] and "test_create" in answer["content"][0]["text"]


@needs_calls
@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_diff_impact_follows_the_changed_lines(shop, capsys):
    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                        *args], cwd=shop, check=True, capture_output=True, **config.NO_WINDOW)

    (shop / ".gitignore").write_text(".claude/\n")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    assert "No difference" in queries.diff_impact()

    path = shop / "shop" / "mail.py"
    path.write_text(path.read_text().replace("return smtplib.SMTP(to)", "return smtplib.SMTP(to, 25)"))
    (shop / "shop" / "new.py").write_text("def fresh():\n    return 2\n")
    assert cli.main(["index", "--no-embed"]) == 0
    capsys.readouterr()
    assert cli.main(["diff-impact"]) == 0
    text = capsys.readouterr().out
    assert "2 file(s), 2 symbol(s) touched" in text and "method Mailer.send" in text and "function fresh" in text
    assert "class Mailer " not in text                      # the innermost symbol only
    assert "1  method Orders.notify" in text and "2  method Orders.create" in text
    assert "tests that reach the changes (1)" in text and "function test_create" in text
    assert "other files affected (1): shop/orders.py" in text
    assert "not a git revision" in queries.diff_impact("--output=x")

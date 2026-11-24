"""Call graph: homonyms told apart by what a file can see (needs the `parse` extra)."""
import pytest

from conftest import needs_relate
from relate_codemem import cli, deps, extract, indexer, parse, store

pytestmark = pytest.mark.skipif(not parse.available(), reason="tree-sitter-language-pack not installed")

JAVA = {
    "src/com/acme/billing/Invoice.java": """package com.acme.billing;

import com.acme.model.Customer;
import com.acme.util.Dates;

public class Invoice extends Base {
    private OrderRepo repo;

    public int total(int x) {
        Customer c = new Customer();
        c.save();
        repo.save(x, this);
        audit();
        helper(1);
        unknown.save();
        return Dates.parse("x");
    }

    private void helper(int n) {
    }
}
""",
    "src/com/acme/billing/Base.java": "package com.acme.billing;\n\nclass Base {\n    void audit() {\n    }\n}\n",
    "src/com/acme/billing/OrderRepo.java": "package com.acme.billing;\n\nclass OrderRepo {\n    void save(int x, Object o) {\n    }\n\n    void save(int x) {\n    }\n}\n",
    "src/com/acme/model/Customer.java": "package com.acme.model;\n\npublic class Customer {\n    public void save() {\n    }\n}\n",
    "src/com/acme/util/Dates.java": "package com.acme.util;\n\npublic class Dates {\n    public static int parse(String s) {\n        return 1;\n    }\n}\n",
    # same method name, in a file Invoice.java cannot see
    "src/com/acme/other/Cache.java": "package com.acme.other;\n\npublic class Cache {\n    public void save() {\n    }\n}\n",
}


def analyse(sources, lang="java"):
    files = {}
    for path, text in sources.items():
        symbols = extract.extract_symbols(path, text, lang, 1)
        files[path] = deps.facts(path, text, lang, symbols, extract.extract_imports(path, text, lang))
        files[path].update(deps.call_facts(path, text, lang, symbols, parse.calls(path, text, lang)))
    live = set(sources)
    return deps.resolve_calls(files, deps.resolve(files, live), live)


def short(rows):
    return {(a.split("::")[1], b.split("/")[-1].replace(".java::", ":")): (via, distance, candidates)
            for a, b, via, distance, candidates in rows}


def test_homonyms_are_told_apart_by_receiver_type_arity_and_visibility(project):
    calls = short(analyse(JAVA)["src/com/acme/billing/Invoice.java"])
    assert calls == {
        ("Invoice.total", "Customer:Customer"): ("new", 1, 1),
        ("Invoice.total", "Customer:Customer.save"): ("receiver", 1, 1),     # c is a Customer
        ("Invoice.total", "OrderRepo:OrderRepo.save"): ("receiver", 1, 1),   # repo is an OrderRepo, two arguments
        ("Invoice.total", "Base:Base.audit"): ("self", 0, 1),                # inherited
        ("Invoice.total", "Invoice:Invoice.helper"): ("self", 0, 1),
        ("Invoice.total", "Dates:Dates.parse"): ("receiver", 1, 1),
    }
    # `unknown.save()` has three visible homonyms with no argument... none: OrderRepo.save takes
    # arguments, so only Customer.save matches by name; Cache.save is never a candidate.
    assert not any("Cache" in callee for _, callee in calls)


def test_a_call_by_name_only_reaches_visible_files(project):
    sources = {
        "app/a.py": "from .b import load\n\ndef run(x):\n    return load(x) + x.close()\n",
        "app/b.py": "def load(x):\n    return 1\n\nclass B:\n    def close(self):\n        pass\n",
        "app/c.py": "class C:\n    def close(self):\n        pass\n",
    }
    calls = analyse(sources, "python")["app/a.py"]
    assert [(a.split("::")[1], b, via, d, n) for a, b, via, d, n in calls] == [
        ("run", "app/b.py::B.close", "name", 1, 1), ("run", "app/b.py::load", "scope", 1, 1)]


@pytest.mark.parametrize("signature, lang, kind, expected", [
    ("public Money total(int x, Map<String, Integer> m) {", "java", "method", 2),
    ("void audit() {", "java", "method", 0),
    ("func (d *DB) Close(force bool) error {", "go", "method", 1),
    ("def close(self):", "python", "method", 0),
    ("def run(x, y=1):", "python", "function", -1),
    ("fn f(&self, a: u8) {", "rust", "method", 1),
    ("public void log(String fmt, Object... args) {", "java", "method", -1),
    ("public void wrapped(int a,", "java", "method", -1),
])
def test_arity_read_from_a_signature(signature, lang, kind, expected):
    assert deps._arity(signature, lang, kind) == expected


@needs_relate
def test_calls_are_stored_and_follow_other_files(project, monkeypatch, capsys):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    for path, text in JAVA.items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(text)
    indexer.index(quiet=True)

    def callees():
        return [r["q"] for r in store.read(
            "MATCH (:Symbol {qualname: 'Invoice.total'})-[r:CALLS]->(t:Symbol) RETURN t.qualname AS q ORDER BY q")]

    assert callees() == ["Base.audit", "Customer", "Customer.save", "Dates.parse", "Invoice.helper", "OrderRepo.save"]
    capsys.readouterr()
    assert cli.main(["symbol", "Invoice.total"]) == 0
    out = capsys.readouterr().out
    assert "Customer.save" in out and "(receiver)" in out

    # the callee disappears from another file: the unchanged caller loses the call
    target = project / "src/com/acme/model/Customer.java"
    target.write_text(target.read_text().replace("save()", "store()"))
    assert indexer.index(quiet=True) == (1, 0)
    assert "Customer.save" not in callees() and "Customer" in callees()

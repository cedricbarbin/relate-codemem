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
            for a, b, via, distance, candidates, _ in rows}


def names(rows):
    return sorted(tuple(x.split("::")[1] if "::" in x else x for x in row) for row in rows)


def test_homonyms_are_told_apart_by_receiver_type_arity_and_visibility(project):
    calls = short(analyse(JAVA)["src/com/acme/billing/Invoice.java"]["calls"])
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
    calls = analyse(sources, "python")["app/a.py"]["calls"]
    assert [(a.split("::")[1], b, via, d, n) for a, b, via, d, n, _ in calls] == [
        ("run", "app/b.py::B.close", "name", 1, 1), ("run", "app/b.py::load", "scope", 1, 1)]


def java(**classes):
    return {f"src/p/{name}.java": "package p;\n\n" + body + "\n" for name, body in classes.items()}


HIERARCHY = java(
    Repo="interface Repo {\n    void save(int x);\n}",
    JpaRepo="class JpaRepo implements Repo {\n    public void save(int x) {\n    }\n\n    void flush() {\n    }\n}",
    CachedRepo="class CachedRepo extends JpaRepo {\n    public void save(int x) {\n        super.save(x);\n        flush();\n    }\n}",
    AuditRepo="class AuditRepo extends JpaRepo {\n}",
    L1="class L1 extends AuditRepo {\n}", L2="class L2 extends L1 {\n}", L3="class L3 extends L2 {\n}",
    Service="class Service {\n    void run(Repo repo, AuditRepo audit, CachedRepo cached) {\n        repo.save(1);\n"
            "        audit.save(2);\n        cached.save(4);\n    }\n\n    void far(L3 deep) {\n        deep.save(3);\n    }\n}",
)


def test_a_call_runs_the_first_declaration_found_up_the_hierarchy(project):
    links = analyse(HIERARCHY)
    calls = {(a.split("::")[1], b.split("::")[1], inherited)
             for a, b, via, _, _, inherited in links["src/p/Service.java"]["calls"] if via == "receiver"}
    assert calls == {
        ("Service.run", "Repo.save", 0),        # on the interface: its declaration; OVERRIDES leads to implementations
        ("Service.run", "JpaRepo.save", 1),     # AuditRepo does not redefine it: its parent's, not the interface's above
        ("Service.run", "CachedRepo.save", 0),  # redefined: the versions above are hidden
        ("Service.far", "JpaRepo.save", 4),     # L3 -> L2 -> L1 -> AuditRepo -> JpaRepo: no limit on depth
    }
    cached = {(b.split("::")[1], via, inherited) for a, b, via, _, _, inherited in links["src/p/CachedRepo.java"]["calls"]}
    assert cached == {("JpaRepo.save", "self", 1), ("JpaRepo.flush", "self", 1)}       # super.save(), inherited flush()


def test_extends_and_overrides_link_each_level_to_the_next(project):
    links = analyse(HIERARCHY)
    extends = names(row for f in links.values() for row in f["extends"])
    assert extends == [("AuditRepo", "JpaRepo", "extends"), ("CachedRepo", "JpaRepo", "extends"),
                       ("JpaRepo", "Repo", "implements"), ("L1", "AuditRepo", "extends"),
                       ("L2", "L1", "extends"), ("L3", "L2", "extends")]
    overrides = names(row for f in links.values() for row in f["overrides"])
    # each method points at the nearest one above only; AuditRepo, which redefines nothing, adds no link
    assert overrides == [("CachedRepo.save", "JpaRepo.save"), ("JpaRepo.save", "Repo.save")]


def test_superclass_wins_over_interfaces_and_arity_selects_the_overload(project):
    links = analyse(java(
        Base="class Base {\n    void m() {\n    }\n\n    void n(int a, int b) {\n    }\n}",
        I="interface I {\n    void m();\n}",
        X="class X extends Base implements I {\n    void n(int a) {\n    }\n\n    void go() {\n        m();\n        n(1, 2);\n        n(1);\n    }\n}",
    ))
    calls = {(b.split("::")[1], inherited) for _, b, _, _, _, inherited in links["src/p/X.java"]["calls"]}
    assert calls == {("Base.m", 1), ("Base.n", 1), ("X.n", 0)}


def test_a_cycle_in_the_hierarchy_ends(project):
    links = analyse(java(A="class A extends B {\n    void go() {\n        missing();\n    }\n}", B="class B extends A {\n}"))
    assert links["src/p/A.java"]["calls"] == []


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
    assert store.read("MATCH (a:Symbol)-[r:EXTENDS]->(b:Symbol) RETURN a.qualname AS a, b.qualname AS b, r.kind AS k") == [
        {"a": "Invoice", "b": "Base", "k": "extends"}]
    capsys.readouterr()
    assert cli.main(["symbol", "Invoice.total"]) == 0
    out = capsys.readouterr().out
    assert "Customer.save" in out and "(receiver)" in out and "Base.audit <src/com/acme/billing/Base.java> (self, inherited)" in out
    assert cli.main(["symbol", "Base"]) == 0
    assert "extended by: Invoice" in capsys.readouterr().out

    # the callee disappears from another file: the unchanged caller loses the call
    target = project / "src/com/acme/model/Customer.java"
    target.write_text(target.read_text().replace("save()", "store()"))
    assert indexer.index(quiet=True) == (1, 0)
    assert "Customer.save" not in callees() and "Customer" in callees()


    # a link left by an older resolution rule, from a method that makes no call: a full run clears it
    store.write("MATCH (a:Symbol {qualname: 'Base.audit'}), (b:Symbol {qualname: 'Dates.parse'}) "
                "MERGE (a)-[r:CALLS]->(b) SET r.via = 'name'")
    indexer.index(full=True, quiet=True)
    assert store.read("MATCH (:Symbol {qualname: 'Base.audit'})-[r:CALLS]->() RETURN count(r) AS n") == [{"n": 0}]
    assert "Customer" in callees()


@needs_relate
def test_implementations_of_an_interface_method_are_reachable(project, monkeypatch, capsys):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    for path, text in HIERARCHY.items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(text)
    indexer.index(quiet=True)
    # what Service.run's call on the interface may run
    may_run = store.read("MATCH (:Symbol {qualname: 'Service.run'})-[:CALLS]->(m:Symbol {qualname: 'Repo.save'}) "
                         "MATCH (impl:Symbol)-[:OVERRIDES*]->(m) RETURN impl.qualname AS q ORDER BY q")
    assert [r["q"] for r in may_run] == ["CachedRepo.save", "JpaRepo.save"]
    capsys.readouterr()
    assert cli.main(["symbol", "Repo.save"]) == 0
    out = capsys.readouterr().out
    assert "overridden by: CachedRepo.save" in out and "overridden by: JpaRepo.save" in out and "called by: Service.run" in out

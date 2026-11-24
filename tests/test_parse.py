"""Syntax-tree extraction (skipped without the `parse` extra)."""
import pytest

from relate_codemem import deps, extract, indexer, parse

pytestmark = pytest.mark.skipif(not parse.available(), reason="tree-sitter-language-pack not installed")

JAVA = """package com.acme;

/** An invoice. */
public class Invoice {
    static class Line {}

    Invoice() {}

    // Sum of the lines.
    public Money total(int x) {
        Runnable r = new Runnable() { public void run() {} };
        return null;
    }
}
"""


def listing(path, text, lang):
    return [(s["kind"], s["qualname"], s["startLine"], s["endLine"]) for s in extract.extract_symbols(path, text, lang, 1)]


def test_java_symbols_are_nested_with_exact_extents(project):
    assert listing("Invoice.java", JAVA, "java") == [
        ("class", "Invoice", 4, 14), ("class", "Invoice.Line", 5, 5), ("method", "Invoice.Invoice", 7, 7),
        ("method", "Invoice.total", 10, 13)]                      # the anonymous class is not indexed
    symbols = {s["qualname"]: s for s in extract.extract_symbols("Invoice.java", JAVA, "java", 1)}
    assert "An invoice." in symbols["Invoice"]["text"] and "Sum of the lines." in symbols["Invoice.total"]["text"]


@pytest.mark.parametrize("path, lang, text, expected", [
    ("a.go", "go", "package m\ntype DB struct{}\nfunc Open() *DB { return nil }\nfunc (d *DB) Close() {}\n",
     [("type", "DB"), ("function", "Open"), ("method", "DB.Close")]),
    ("a.kt", "kotlin", "class Store { fun save() {} }\nenum class E { A }\ninterface I\nfun top() = 1\n",
     [("class", "Store"), ("method", "Store.save"), ("enum", "E"), ("interface", "I"), ("function", "top")]),
    ("A.cs", "csharp", "namespace Acme.Billing {\n  public class Invoice {\n    public int Total() { return 1; }\n  }\n}\n",
     [("class", "Invoice"), ("method", "Invoice.Total")]),
    ("a.rs", "rust", "pub struct S;\nimpl T for S { fn f(&self) {} }\nfn top() {}\n",
     [("struct", "S"), ("method", "S.f"), ("function", "top")]),
    ("a.tsx", "typescript", "export const App = () => <div>hi</div>\nconst n = 3\nclass A { m() {} }\n",
     [("function", "App"), ("class", "A"), ("method", "A.m")]),
    ("a.php", "php", "<?php\nclass C { function m() {} }\nfunction top() {}\n",
     [("class", "C"), ("method", "C.m"), ("function", "top")]),
    ("a.rb", "ruby", "module M\n  class C\n    def m; end\n  end\nend\n",
     [("module", "M"), ("class", "M.C"), ("method", "M.C.m")]),
    ("a.cpp", "cpp", "namespace n { class C { void m(); }; }\nvoid C::m() {}\nstruct S x;\n",
     [("class", "C"), ("method", "C.m")]),
    ("a.swift", "swift", "struct S { func f() {} }\nprotocol P {}\n",
     [("struct", "S"), ("method", "S.f"), ("protocol", "P")]),
    ("a.scala", "scala", "object O { def f = 1 }\ntrait T\n", [("object", "O"), ("method", "O.f"), ("trait", "T")]),
])
def test_symbols_in_other_languages(project, path, lang, text, expected):
    assert [(kind, name) for kind, name, _, _ in listing(path, text, lang)] == expected


def test_comments_and_strings_are_not_code(project):
    blanked = parse.code_only("A.java", 'class A { String s = "Ghost"; /* Ledger */ B b; }', "java")
    assert "Ghost" not in blanked and "Ledger" not in blanked and "B b;" in blanked
    assert len(blanked) == len('class A { String s = "Ghost"; /* Ledger */ B b; }')


def test_member_functions_on_one_line_are_not_package_names(project):
    text = "package a\n\nclass Store { fun save() {} }\nfun open() = Store()\n"
    facts = deps.facts("a/Store.kt", text, "kotlin", extract.extract_symbols("a/Store.kt", text, "kotlin", 1), [])
    assert facts["exports"] == ["Store", "open"]


def test_turning_the_parser_off_falls_back_and_reanalyses(project, monkeypatch):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    (project / "A.java").write_text(JAVA)
    indexer.index(quiet=True)
    assert deps.load(indexer.deps_path()) is not None
    monkeypatch.setenv("RELATE_CODEMEM_PARSER", "regex")
    assert parse.mode() == "regex" and deps.load(indexer.deps_path()) is None
    assert ("method", "total", 10, 14) in listing("Invoice.java", JAVA, "java")
    assert indexer.index(quiet=True) == (1, 0)                       # same content, analysed again


def test_languages_without_table_or_patterns_use_the_generic_extraction(project):
    assert extract.classify("src/main.zig") == ("code", "zig")
    assert extract.classify("data/table.csv") is None                # a format the pack parses, not code
    found = [(kind, name) for kind, name, _, _ in listing("src/main.zig", "pub fn top() void {}\n", "zig")]
    assert ("function", "top") in found


def test_languages_with_patterns_keep_them(project):
    text = "defmodule A.B do\n  def f(x), do: x\nend\n"
    assert [(kind, name) for kind, name, _, _ in listing("a.ex", text, "elixir")] == [("defmodule", "A.B"), ("function", "f")]

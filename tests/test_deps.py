"""Dependency resolution from declared symbols (no store)."""
from relate_codemem import deps
from relate_codemem.extract import extract_imports, extract_symbols


def analyse(sources):
    """{path: (lang, text)} -> {path: {target: via}}"""
    files = {}
    for path, (lang, text) in sources.items():
        symbols = extract_symbols(path, text, lang, 1)
        files[path] = deps.facts(path, text, lang, symbols, extract_imports(path, text, lang))
    return deps.resolve(files, set(sources))


JAVA = {
    "billing/src/main/java/com/acme/billing/Invoice.java": ("java", """package com.acme.billing;

import com.acme.model.*;
import static com.acme.util.Dates.parse;

public class Invoice {
    private final Customer customer;
    private final LineItem item = new LineItem();

    public Money total() {
        return com.acme.money.Money.zero();
    }
    // Ledger is only mentioned in a comment
    String label = "Ghost";
}
"""),
    "billing/src/main/java/com/acme/billing/LineItem.java": ("java", "package com.acme.billing;\n\npublic class LineItem {\n}\n"),
    "billing/src/main/java/com/acme/billing/Ledger.java": ("java", "package com.acme.billing;\n\npublic class Ledger {\n}\n"),
    "billing/src/main/java/com/acme/billing/Ghost.java": ("java", "package com.acme.billing;\n\nclass Ghost {\n}\n"),
    "model/src/main/java/com/acme/model/Customer.java": ("java", "package com.acme.model;\n\npublic record Customer(String name) {\n}\n"),
    "model/src/main/java/com/acme/model/Unused.java": ("java", "package com.acme.model;\n\npublic class Unused {\n}\n"),
    "util/src/main/java/com/acme/util/Dates.java": ("java", "package com.acme.util;\n\npublic final class Dates {\n}\n"),
    "money/src/main/java/com/acme/money/Money.java": ("java", "package com.acme.money;\n\npublic class Money {\n}\n"),
}


def test_java_same_package_wildcard_static_and_qualified():
    found = analyse(JAVA)["billing/src/main/java/com/acme/billing/Invoice.java"]
    assert found == {
        "billing/src/main/java/com/acme/billing/LineItem.java": "package",   # no import at all
        "model/src/main/java/com/acme/model/Customer.java": "wildcard",      # import com.acme.model.*
        "util/src/main/java/com/acme/util/Dates.java": "import",             # import static ...Dates.parse
        "money/src/main/java/com/acme/money/Money.java": "qualified",        # written in full in the code
    }


def test_java_explicit_import_in_any_source_root():
    found = analyse({
        "services/orders/src/main/java/com/acme/orders/Order.java": ("java", "package com.acme.orders;\n\nimport com.acme.model.Customer;\n\nclass Order {\n    Customer c;\n}\n"),
        "libs/model/src/main/java/com/acme/model/Customer.java": JAVA["model/src/main/java/com/acme/model/Customer.java"],
    })
    assert found["services/orders/src/main/java/com/acme/orders/Order.java"] == {
        "libs/model/src/main/java/com/acme/model/Customer.java": "import"}


def test_kotlin_sees_java_types_and_top_level_functions_of_its_package():
    found = analyse({
        "src/main/kotlin/com/acme/app/Main.kt": ("kotlin", "package com.acme.app\n\nfun main() {\n    val repo = Repo()\n    println(describe(repo))\n    repo.save()\n}\n"),
        "src/main/java/com/acme/app/Repo.java": ("java", "package com.acme.app;\n\npublic class Repo {\n}\n"),
        "src/main/kotlin/com/acme/app/Text.kt": ("kotlin", "package com.acme.app\n\nfun describe(x: Any) = x.toString()\n"),
        "src/main/kotlin/com/acme/app/Store.kt": ("kotlin", "package com.acme.app\n\nclass Store {\n    fun save() {}\n}\n"),
    })
    assert found["src/main/kotlin/com/acme/app/Main.kt"] == {
        "src/main/java/com/acme/app/Repo.java": "package",
        "src/main/kotlin/com/acme/app/Text.kt": "package",   # Store.kt is not linked: save() is a member
    }


def test_csharp_using_and_parent_namespace():
    found = analyse({
        "App/Billing/Invoice.cs": ("csharp", "using Acme.Model;\n\nnamespace Acme.Billing\n{\n    public class Invoice\n    {\n        Customer customer;\n        Clock clock;\n    }\n}\n"),
        "App/Model/Customer.cs": ("csharp", "namespace Acme.Model;\n\npublic class Customer\n{\n}\n"),
        "App/Clock.cs": ("csharp", "namespace Acme\n{\n    public class Clock\n    {\n    }\n}\n"),
    })
    assert found["App/Billing/Invoice.cs"] == {"App/Model/Customer.cs": "wildcard", "App/Clock.cs": "package"}


def test_go_same_directory_and_imported_package():
    found = analyse({
        "cmd/app/main.go": ("go", 'package main\n\nimport (\n\t"fmt"\n\t"github.com/acme/app/internal/store"\n)\n\nfunc main() {\n\tdb := store.Open()\n\tfmt.Println(render(db))\n}\n'),
        "cmd/app/render.go": ("go", "package main\n\nfunc render(x any) string {\n\treturn \"\"\n}\n"),
        "internal/store/store.go": ("go", "package store\n\ntype DB struct{}\n\nfunc Open() *DB {\n\treturn nil\n}\n\nfunc (d *DB) Println() {}\n"),
        "internal/store/other.go": ("go", "package store\n\nfunc Unused() {}\n"),
    })
    assert found["cmd/app/main.go"] == {"cmd/app/render.go": "package", "internal/store/store.go": "import"}


def test_php_namespace_and_use():
    found = analyse({
        "src/Billing/Invoice.php": ("php", "<?php\nnamespace Acme\\Billing;\n\nuse Acme\\Model\\Customer;\n\nclass Invoice {\n    function total() { return new LineItem(); }\n}\n"),
        "src/Billing/LineItem.php": ("php", "<?php\nnamespace Acme\\Billing;\n\nclass LineItem {\n}\n"),
        "src/Model/Customer.php": ("php", "<?php\nnamespace Acme\\Model;\n\nclass Customer {\n}\n"),
    })
    assert found["src/Billing/Invoice.php"] == {"src/Billing/LineItem.php": "package", "src/Model/Customer.php": "import"}


def test_path_imports_still_resolve_and_follow_the_live_set():
    sources = {
        "src/app/config.py": ("python", "from .util import read\n"),
        "src/app/util.py": ("python", "def read():\n    return 1\n"),
        "web/main.ts": ("typescript", "import { api } from './api'\n"),
        "web/api.ts": ("typescript", "export const api = 1\n"),
        "core/src/lib.rs": ("rust", "use crate::net::http::Client;\n"),
        "core/src/net/http.rs": ("rust", "pub struct Client;\n"),
    }
    found = analyse(sources)
    assert found["src/app/config.py"] == {"src/app/util.py": "import"}
    assert found["web/main.ts"] == {"web/api.ts": "import"}
    assert found["core/src/lib.rs"] == {"core/src/net/http.rs": "import"}
    files = {p: deps.facts(p, t, l, extract_symbols(p, t, l, 1), extract_imports(p, t, l))
             for p, (l, t) in sources.items()}
    assert deps.resolve(files, set(sources) - {"web/api.ts"})["web/main.ts"] == {}

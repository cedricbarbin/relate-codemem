"""Infrastructure as code: Terraform and Ansible. No store access, no dependency.

Neither fits the "functions and classes" model of the other languages, so each has its
own reading here, producing the same things as the rest of the index: symbols, imports
with candidate files, and facts for the resolution of dependencies and of links between
symbols.

Terraform: one symbol per block, named the way Terraform refers to it (`var.region`,
`aws_s3_bucket.logs`, `module.vpc`, `local.name`, `data.aws_ami.base`, `output.arn`).
A directory is a module: a name used in a file links it to the file of the same
directory that declares it, and a `module` block links to the files of its source.

Ansible has no file extension of its own. A YAML file is taken for Ansible when its
directory or its content says so (see is_ansible). Plays, tasks, handlers and variables
of variable files become symbols; includes, roles, templates and copied files become
dependencies; `notify` links a task to its handler. Variable usage is not followed.
"""
import posixpath
import re

LANGS = {"terraform", "ansible"}

# ------------------------------------------------------------------ Terraform

_TF_COMMENT = re.compile(r'"(?:\\.|[^"\\\n])*"|/\*[\s\S]*?\*/|(?:#|//)[^\n]*')
_TF_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"')
_TF_HEADER = re.compile(r'^\s*([A-Za-z_]\w*)((?:\s+"[^"\n]*")*)\s*\{')
_TF_ATTRIBUTE = re.compile(r"^\s*([A-Za-z_][\w-]*)\s*=(?!=)")
_TF_SOURCE = re.compile(r'^\s*source\s*=\s*"([^"]+)"', re.M)
_TF_PREFIX = {"data": "data.", "module": "module.", "variable": "var.", "output": "output.", "provider": "provider."}
_TF_KIND = {"variable": "variable", "output": "output", "module": "module", "provider": "provider",
            "data": "data", "resource": "resource"}
_TF_REFS = (re.compile(r"\b(var\.[A-Za-z_][\w-]*)"), re.compile(r"\b(local\.[A-Za-z_][\w-]*)"),
            re.compile(r"\b(data\.[A-Za-z_]\w*\.[A-Za-z_][\w-]*)"), re.compile(r"\b(module\.[A-Za-z_][\w-]*)"),
            re.compile(r"(?<![\w.])([a-z][a-z0-9]*_[a-z0-9_]+\.[A-Za-z_][\w-]*)"))
_TF_MODULE_OUTPUT = re.compile(r"\bmodule\.([A-Za-z_][\w-]*)\.([A-Za-z_][\w-]*)")
_TF_META = {"source", "version", "count", "for_each", "providers", "depends_on"}


def _tf_clean(text):
    """Comments blanked out, strings and line numbers kept."""
    return _TF_COMMENT.sub(lambda m: m.group(0) if m.group(0).startswith('"') else
                           re.sub(r"[^\n]", " ", m.group(0)), text)


def _tf_blocks(text):
    """-> [(type, labels, first line, last line, [(attribute, line)] at the block's top level)]"""
    blocks, depth, current = [], 0, None
    for number, line in enumerate(_tf_clean(text).splitlines(), 1):
        bare = _TF_STRING.sub('""', line)
        if depth == 0:
            m = _TF_HEADER.match(line)
            if m:
                current = [m.group(1), re.findall(r'"([^"\n]*)"', m.group(2)), number, number, []]
        elif depth == 1 and current is not None:
            m = _TF_ATTRIBUTE.match(bare)
            if m:
                current[4].append((m.group(1), number))
        depth += bare.count("{") - bare.count("}")
        if depth <= 0:
            depth = 0
            if current is not None:
                current[3] = number
                blocks.append(tuple(current))
                current = None
    return blocks


def _doc_above(lines, start):
    doc, j = [], start - 2
    while j >= 0 and len(doc) < 8 and lines[j].lstrip()[:1] in ("#", "/", "*"):
        doc.insert(0, lines[j].strip().lstrip("#/*").rstrip("*/").strip())
        j -= 1
    return " ".join(d for d in doc if d)[:600]


def terraform_symbols(path, text):
    lines = text.splitlines()
    out = []

    def add(kind, qualname, start, end):
        out.append({"name": qualname.rsplit(".", 1)[-1], "qualname": qualname, "kind": kind, "startLine": start,
                    "endLine": max(end, start), "signature": lines[start - 1].strip()[:300] if start <= len(lines) else "",
                    "doc": _doc_above(lines, start), "body": "\n".join(lines[start - 1:end]),
                    "text": "\n".join(lines[start - 1:end])[:900]})

    if path.endswith(".tfvars"):
        return out
    for kind, labels, start, end, attributes in _tf_blocks(text):
        if kind == "locals":
            for index, (name, line) in enumerate(attributes):
                last = attributes[index + 1][1] - 1 if index + 1 < len(attributes) else end - 1
                add("local", f"local.{name}", line, last)
        elif kind in _TF_KIND and labels and len(labels) >= (2 if kind in ("resource", "data") else 1):
            add(_TF_KIND[kind], _TF_PREFIX.get(kind, "") + ".".join(labels[:2] if kind in ("resource", "data") else labels[:1]),
                start, end)
    return out


def terraform_imports(path, text):
    """Module and provider sources. A relative source is a directory, resolved with the facts."""
    return [(source, []) for source in dict.fromkeys(_TF_SOURCE.findall(_tf_clean(text)))]


def _tf_scope(directory):
    return "tf:" + directory


def _terraform_facts(path, text, symbols, imports):
    directory = posixpath.dirname(path)
    scope = _tf_scope(directory)
    clean = _tf_clean(text)
    lines = clean.splitlines()
    out = {"lang": "terraform", "imports": [[m, c] for m, c in imports], "names": {}, "package": scope,
           "exports": sorted(s["qualname"] for s in symbols),
           "symdefs": [[scope, s["qualname"], s["id"]] for s in symbols], "symrefs": {}, "extrefs": {}}
    used = set()
    modules = {}
    for kind, labels, start, end, attributes in _tf_blocks(text):
        if kind == "module" and labels:
            source = _TF_SOURCE.search("\n".join(lines[start - 1:end]))
            if source and source.group(1).startswith("."):
                modules[labels[0]] = _tf_scope(posixpath.normpath(posixpath.join(directory, source.group(1))))
    if path.endswith(".tfvars"):                        # a variable file sets the variables of its directory
        used.update(f"var.{m.group(1)}" for line in lines for m in [_TF_ATTRIBUTE.match(line)] if m and line[:1] not in " \t")
    for s in symbols:
        body = "\n".join(lines[s["startLine"] - 1:s["endLine"]])
        refs = []
        for rx in _TF_REFS:
            refs += [[[scope], name] for name in dict.fromkeys(rx.findall(body)) if name != s["qualname"]]
        for module, output in dict.fromkeys(_TF_MODULE_OUTPUT.findall(body)):
            # the module block may be in another file of the directory: resolved with all the facts
            refs.append([[f"@{scope}|{module}"], f"output.{output}"])
        if s["kind"] == "module" and s["name"] in modules:    # arguments of a module block set its variables
            for kind, labels, start, end, attributes in _tf_blocks(text):
                if kind == "module" and labels[:1] == [s["name"]]:
                    refs += [[[modules[s["name"]]], f"var.{name}"] for name, _ in attributes if name not in _TF_META]
        if refs:
            out["symrefs"][s["id"]] = refs[:300]
        used.update(name for scopes, name in refs if scopes == [scope])
    for rx in _TF_REFS:                                   # also outside blocks we kept (terraform {}, moved {}...)
        used.update(rx.findall(clean))
    out["uses"] = sorted(used - set(out["exports"]))[:3000]
    out["tfmodules"] = modules
    return out


# ------------------------------------------------------------------ Ansible

_YAML_EXT = (".yml", ".yaml")
_ROLE_PART = re.compile(r"(^|/)roles/([^/]+)/(tasks|handlers|defaults|vars|meta|templates|files)/")
_ANSIBLE_DIRS = re.compile(r"(^|/)(playbooks|group_vars|host_vars|tasks|handlers|vars)/")
_ITEM = re.compile(r"^(\s*)-\s+(.*)$")
_KEY = re.compile(r"^(\s*)(?:-\s+)?([A-Za-z_][\w.]*)\s*:\s*(.*)$")
_INCLUDES = {"import_tasks", "include_tasks", "import_playbook", "include_vars", "include", "include_role", "import_role"}
_KEYWORDS = _INCLUDES | {
    "name", "hosts", "when", "loop", "register", "become", "become_user", "become_method", "tags", "vars", "notify",
    "listen", "block", "rescue", "always", "delegate_to", "run_once", "ignore_errors", "changed_when", "failed_when",
    "environment", "args", "until", "retries", "delay", "no_log", "check_mode", "diff", "connection", "gather_facts",
    "roles", "tasks", "handlers", "pre_tasks", "post_tasks", "vars_files", "action", "local_action", "throttle",
    "timeout", "module_defaults", "collections", "strategy", "serial", "remote_user", "any_errors_fatal",
    "max_fail_percentage", "order", "port", "vars_prompt", "force_handlers", "loop_control", "delegate_facts",
    "debugger", "ignore_unreachable", "role", "dependencies", "async", "poll", "first_available_file"}
_VAR_FILE = re.compile(r"(^|/)(group_vars|host_vars)/|(^|/)roles/[^/]+/(defaults|vars)/|(^|/)vars/[^/]+$")
_CONTENT = re.compile(r"^-\s+(name|hosts|import_playbook|block|include_tasks|import_tasks)\s*:", re.M)
_PLAYISH = re.compile(r"^\s+(hosts|tasks|roles|become|register|notify|when|loop|import_playbook|ansible\.builtin\.\w+)\s*:"
                      r"|^-\s+(hosts|import_playbook)\s*:", re.M)


def is_ansible(path, text):
    """A YAML file that Ansible reads: by its place in a role or a conventional directory, or by
    a content that looks like plays or tasks."""
    if not path.lower().endswith(_YAML_EXT):
        return False
    if _ROLE_PART.search(path) or _ANSIBLE_DIRS.search(path):
        return True
    return bool(_CONTENT.search(text) and _PLAYISH.search(text))


def _unquote(value):
    value = re.sub(r"\s+#.*$", "", value.strip())
    return value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"" else value


def _scalars(value, nested):
    """The strings of `key: a`, `key: [a, b]` or a nested `- a` list."""
    value = value.strip()
    if value.startswith("["):
        return [_unquote(v) for v in value.strip("[]").split(",") if v.strip()]
    if value:
        return [_unquote(value)]
    out = []
    for line in nested:
        m = _ITEM.match(line)
        if m and not _KEY.match(line.replace("- ", "", 1) if ":" in m.group(2) and not m.group(2).startswith(("'", '"')) else ""):
            out.append(_unquote(m.group(2)))
    return out


def _items(text):
    """List items of a YAML file, read by indentation.
    -> [{"line", "end", "indent", "section", "scalar", "keys": {key: (value, line, [nested lines])}}]"""
    lines = text.splitlines()
    items, open_keys, stack = [], [], []      # stack: items still open
    for number, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        item = _ITEM.match(raw)
        while stack and indent <= stack[-1]["indent"]:      # back at, or left of, an item's dash: it is over
            stack.pop()
        if item:
            while open_keys and open_keys[-1][0] > indent:
                open_keys.pop()
            entry = {"line": number, "end": number, "indent": indent, "content": indent + 2, "keys": {},
                     "section": open_keys[-1][1] if open_keys else "", "scalar": "", "last": None}
            first = _KEY.match(raw)
            if first and not item.group(2).startswith(("'", '"', "{", "[")):
                entry["keys"][first.group(2)] = (first.group(3), number, [])
                entry["last"] = first.group(2)
                if not first.group(3).strip():
                    open_keys.append((indent + 2, first.group(2)))
            else:
                entry["scalar"] = _unquote(item.group(2))
            if stack and entry["scalar"] and indent >= stack[-1]["content"] and stack[-1]["last"]:
                stack[-1]["keys"][stack[-1]["last"]][2].append(raw)      # an element of a key's list
            items.append(entry)
            stack.append(entry)
        else:
            key = _KEY.match(raw)
            while open_keys and open_keys[-1][0] > indent:
                open_keys.pop()
            if key and not key.group(3).strip():
                while open_keys and open_keys[-1][0] == indent:
                    open_keys.pop()
                open_keys.append((indent, key.group(2)))
            if stack:
                top = stack[-1]
                if key and indent == top["content"]:
                    top["keys"][key.group(2)] = (key.group(3), number, [])
                    top["last"] = key.group(2)
                elif top["last"]:
                    top["keys"][top["last"]][2].append(raw)
        for open_item in stack:
            open_item["end"] = number
    return items


def _kind(item, path):
    if "hosts" in item["keys"] or "import_playbook" in item["keys"]:
        return "play"
    if item["section"] in ("roles", "dependencies", "vars_files", "notify", "listen", "collections"):
        return None
    if not item["keys"] or set(item["keys"]) <= {"role", "name", "vars", "tags", "when"} and item["section"] in ("roles", "dependencies"):
        return None
    if item["section"] == "handlers" or "/handlers/" in "/" + path:
        return "handler"
    return "task" if any(k not in ("vars", "tags") for k in item["keys"]) else None


def ansible_symbols(path, text):
    lines = text.splitlines()
    out = []

    def add(kind, name, start, end):
        out.append({"name": name, "qualname": name, "kind": kind, "startLine": start, "endLine": max(end, start),
                    "signature": lines[start - 1].strip()[:300], "doc": _doc_above(lines, start),
                    "body": "\n".join(lines[start - 1:end]), "text": "\n".join(lines[start - 1:end])[:900]})

    if _VAR_FILE.search(path):
        tops = [(m.group(2), n) for n, line in enumerate(lines, 1) for m in [_KEY.match(line)]
                if m and not line[:1].isspace() and not line.startswith("-")]
        for index, (name, line) in enumerate(tops):
            add("variable", name, line, tops[index + 1][1] - 1 if index + 1 < len(tops) else len(lines))
        return out
    for item in _items(text):
        kind = _kind(item, path)
        if not kind:
            continue
        name = _unquote(item["keys"].get("name", ("",))[0])
        if not name:
            if kind == "play" and "hosts" in item["keys"]:
                name = "play " + _unquote(item["keys"]["hosts"][0])
            else:
                key = next((k for k in item["keys"] if k not in _KEYWORDS or k in _INCLUDES), None)
                if not key:
                    continue
                name = f"{key} {_unquote(item['keys'][key][0])}".strip()
        add(kind, name[:200], item["line"], item["end"])
    return out


def _role_root(path):
    m = _ROLE_PART.search(path)
    return path[:m.start(3) - 1] if m else None          # .../roles/<name>


def _nested_value(nested, key):
    for line in nested:
        m = _KEY.match(line)
        if m and m.group(2) == key:
            return _unquote(m.group(3))
    return ""


def _references(path, text):
    """-> [(line, kind, value)] with kind in include, vars, playbook, role, template, file, notify, listen, module."""
    out = []
    for item in _items(text):
        keys = item["keys"]
        if item["section"] in ("roles", "dependencies"):
            role = item["scalar"] or _unquote(keys.get("role", keys.get("name", ("",)))[0])
            if role:
                out.append((item["line"], "role", role))
            continue
        if item["section"] == "vars_files" and item["scalar"]:
            out.append((item["line"], "vars", item["scalar"]))
            continue
        for key, (value, line, nested) in keys.items():
            short = key.rsplit(".", 1)[-1]
            if short in ("import_tasks", "include_tasks", "include"):
                out.append((line, "include", _unquote(value) or _nested_value(nested, "file")))
            elif short == "import_playbook":
                out.append((line, "playbook", _unquote(value)))
            elif short == "include_vars":
                out.append((line, "vars", _unquote(value) or _nested_value(nested, "file")))
            elif short in ("include_role", "import_role"):
                out.append((line, "role", _nested_value(nested, "name") or _unquote(re.sub(r"^name=", "", value))))
            elif short in ("template", "copy", "script", "unarchive"):
                source = _nested_value(nested, "src") or "".join(re.findall(r"\bsrc=(\S+)", value)[:1])
                if short == "script" and not source:
                    source = _unquote(value).split(" ")[0]
                if source:
                    out.append((line, "template" if short == "template" else "file", _unquote(source)))
            elif key in ("notify", "listen"):
                out += [(line, key, v) for v in _scalars(value, nested)]
            if key not in _KEYWORDS and short not in _INCLUDES and _kind(item, path) in ("task", "handler"):
                out.append((line, "module", key))
    return [(line, kind, value) for line, kind, value in out if value and "{{" not in value]


def ansible_imports(path, text):
    """-> [(module, candidates)]. Entries starting with "?" are further files of a role: they
    become dependencies when they exist and are nothing otherwise."""
    directory = posixpath.dirname(path)
    role = _role_root(path)
    roles_dirs = [posixpath.join(directory, "roles"), "roles"]
    if role:
        roles_dirs.insert(0, posixpath.dirname(role))
    out = []
    for _, kind, value in _references(path, text):
        if kind in ("include", "playbook"):
            bases = [directory] + ([posixpath.join(role, "tasks")] if role else []) + [""]
            out.append((value, [posixpath.normpath(posixpath.join(b, value)) for b in bases]))
        elif kind == "vars":
            bases = [directory] + ([posixpath.join(role, "vars")] if role else []) + ["vars", ""]
            out.append((value, [posixpath.normpath(posixpath.join(b, value)) for b in bases]))
        elif kind in ("template", "file"):
            folder = "templates" if kind == "template" else "files"
            bases = ([posixpath.join(role, folder)] if role else []) + [posixpath.join(directory, folder), directory]
            out.append((value, [posixpath.normpath(posixpath.join(b, value)) for b in bases]))
        elif kind == "role":
            name = value.rsplit(".", 1)[-1] if "/" not in value else value
            parts = [f"{part}/main{ext}" for part in ("tasks", "handlers", "defaults", "vars", "meta") for ext in _YAML_EXT]
            out.append((f"role:{value}", [posixpath.normpath(posixpath.join(d, name, "tasks", "main" + ext))
                                          for d in roles_dirs for ext in _YAML_EXT]))
            for part in parts[2:]:
                out.append((f"?role:{value}/{part}", [posixpath.normpath(posixpath.join(d, name, part)) for d in roles_dirs]))
    return list(dict.fromkeys((m, tuple(c)) for m, c in out))


def _ansible_facts(path, text, symbols, imports):
    directory = posixpath.dirname(path)
    role = _role_root(path)
    # handlers are looked up in the role, then in the directory, then anywhere: once a role is
    # applied, its handlers answer to the whole play
    scopes = (["ans:" + role] if role else []) + ["ans:" + directory, "ans:*"]
    out = {"lang": "ansible", "imports": [[m, list(c)] for m, c in imports], "names": {},
           "symdefs": [], "symrefs": {}, "extrefs": {}}
    handlers = [s for s in symbols if s["kind"] == "handler"]
    for s in handlers:
        out["symdefs"] += [[scopes[0], s["qualname"], s["id"]], ["ans:*", s["qualname"], s["id"]]]
    owners = [s for s in symbols if s["kind"] in ("task", "handler")]

    def owner(line):
        inside = [s for s in owners if s["startLine"] <= line <= s["endLine"]]
        return max(inside, key=lambda s: s["startLine"])["id"] if inside else None

    for line, kind, value in _references(path, text):
        source = owner(line)
        if kind == "listen" and source:
            out["symdefs"] += [[scopes[0], value, source], ["ans:*", value, source]]   # it also answers to this name
        elif kind == "notify" and source:
            out["symrefs"].setdefault(source, []).append([scopes, value])
        elif kind == "module" and source:
            parts = value.split(".")
            if len(parts) >= 3:
                root = ".".join(parts[:2])
                scope = "stdlib" if root == "ansible.builtin" else "external"
            else:                       # a short name cannot be attributed to a collection
                root, scope = "ansible", "external"
            out["extrefs"].setdefault(source, []).append([value, "module", value, root, scope])
    return out


# ------------------------------------------------------------------ shared

def symbols(path, text, lang):
    return terraform_symbols(path, text) if lang == "terraform" else ansible_symbols(path, text)


def imports(path, text, lang):
    return terraform_imports(path, text) if lang == "terraform" else ansible_imports(path, text)


def facts(path, text, lang, syms, imps):
    return (_terraform_facts if lang == "terraform" else _ansible_facts)(path, text, syms, imps)


def resolve(files, live):
    """Links between symbols of infrastructure files.
    -> {path: {"calls": [[from, to, "reference", distance, candidates, 0]], "external": [...]}}

    A reference is looked up in the scopes its file can see, in order (for Terraform the
    directory, or the directory of a module's source; for Ansible the role, then the
    directory), and links to what declares that name there."""
    index = {}
    for path, f in files.items():
        if path in live:
            for scope, name, sid in f.get("symdefs", ()):
                index.setdefault(scope, {}).setdefault(name, []).append((sid, path))
    sources = {}                    # "@<directory scope>|<module name>" -> scope of the module's source
    for path, f in files.items():
        if path in live:
            for name, target in f.get("tfmodules", {}).items():
                sources[f"@{f['package']}|{name}"] = target
    result = {}
    for path, f in files.items():
        if path not in live or f.get("lang") not in LANGS:
            continue
        rows = {}
        for source, refs in f.get("symrefs", {}).items():
            for scopes, name in refs:
                for scope in scopes:
                    scope = sources.get(scope, scope)
                    found = index.get(scope, {}).get(name)
                    if found:
                        if len(found) <= 3:
                            for sid, where in found:
                                if sid != source:
                                    rows[(source, sid)] = ["reference", 0 if where == path else 1, len(found), 0]
                        break
        outside = [[source, "CALLS", *entry] for source, entries in f.get("extrefs", {}).items() for entry in entries]
        if rows or outside:
            result[path] = {"calls": sorted([a, b, *rest] for (a, b), rest in rows.items()), "extends": [],
                            "overrides": [], "external": sorted({tuple(r[:3]): r for r in outside}.values())}
    return result

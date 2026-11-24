"""Terraform and Ansible: symbols, dependencies and references (no parser extra needed)."""
from conftest import needs_relate
from relate_codemem import cli, deps, extract, iac, queries, store

TERRAFORM = {
    "infra/main.tf": '''terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

locals {
  name = "demo"
  tags = { env = var.env }   # not var.unused
}

# The main bucket.
resource "aws_s3_bucket" "logs" {
  bucket = "${local.name}-logs"
  tags   = local.tags
}

data "aws_caller_identity" "current" {}

module "vpc" {
  source = "./modules/vpc"
  region = var.region
  cidr   = "10.0.0.0/16"
}

module "remote" {
  source = "terraform-aws-modules/eks/aws"
}
''',
    "infra/variables.tf": 'variable "region" {\n  type = string\n}\n\nvariable "env" {\n  type = string\n}\n\nvariable "unused" {}\n',
    "infra/outputs.tf": 'output "bucket_arn" {\n  value = aws_s3_bucket.logs.arn\n}\n\noutput "vpc_id" {\n  value = module.vpc.id\n}\n\n'
                        'output "account" {\n  value = data.aws_caller_identity.current.account_id\n}\n',
    "infra/prod.tfvars": 'region = "eu-west-3"\nenv    = "prod"\n',
    "infra/modules/vpc/main.tf": 'variable "region" {}\n\nvariable "cidr" {}\n\nresource "aws_vpc" "this" {\n  cidr_block = var.cidr\n}\n\n'
                                 'output "id" {\n  value = aws_vpc.this.id\n}\n',
    "other/main.tf": 'variable "region" {}\n\nresource "aws_s3_bucket" "logs" {\n  bucket = var.region\n}\n',
}


def analyse(sources, lang):
    files = {}
    for path, text in sources.items():
        symbols = extract.extract_symbols(path, text, lang, 1)
        files[path] = deps.facts(path, text, lang, symbols, extract.extract_imports(path, text, lang))
    live, scopes = set(sources), {}
    edges = deps.resolve(files, live, scopes)
    return edges, scopes, deps.resolve_calls(files, edges, live, scopes)


def short(rows):
    return sorted((a.split("::")[1], b.split("::")[0].split("/")[-1] + ":" + b.split("::")[1]) for a, b, *_ in rows)


def test_terraform_symbols_are_named_as_terraform_refers_to_them(project):
    assert extract.classify("infra/main.tf") == ("code", "terraform") == extract.classify("infra/prod.tfvars")
    symbols = extract.extract_symbols("infra/main.tf", TERRAFORM["infra/main.tf"], "terraform", 1)
    assert [(s["kind"], s["qualname"], s["startLine"], s["endLine"]) for s in symbols] == [
        ("local", "local.name", 10, 10), ("local", "local.tags", 11, 11), ("resource", "aws_s3_bucket.logs", 15, 18),
        ("data", "data.aws_caller_identity.current", 20, 20), ("module", "module.vpc", 22, 26),
        ("module", "module.remote", 28, 30)]
    bucket = symbols[2]
    assert bucket["name"] == "logs" and "The main bucket." in bucket["text"] + extract.extract_symbols(
        "infra/main.tf", TERRAFORM["infra/main.tf"], "terraform", 1)[2]["title"] or "local.name" in bucket["text"]
    assert extract.extract_symbols("infra/prod.tfvars", TERRAFORM["infra/prod.tfvars"], "terraform", 1) == []


def test_terraform_dependencies_follow_names_within_a_directory_and_module_sources(project):
    edges, scopes, links = analyse(TERRAFORM, "terraform")
    assert edges["infra/main.tf"] == {"infra/variables.tf": "package", "infra/modules/vpc/main.tf": "import"}
    assert edges["infra/outputs.tf"] == {"infra/main.tf": "package"}
    assert edges["infra/prod.tfvars"] == {"infra/variables.tf": "package"}        # it sets those variables
    assert edges["other/main.tf"] == {}                                           # same names, another directory
    assert scopes["infra/main.tf"] == {"hashicorp/aws": "external", "./modules/vpc": "internal",
                                       "terraform-aws-modules/eks/aws": "external"}

    assert short(links["infra/main.tf"]["calls"]) == [
        ("aws_s3_bucket.logs", "main.tf:local.name"), ("aws_s3_bucket.logs", "main.tf:local.tags"),
        ("local.tags", "variables.tf:var.env"),
        ("module.vpc", "main.tf:var.cidr"), ("module.vpc", "main.tf:var.region"),      # arguments set the module's variables
        ("module.vpc", "variables.tf:var.region")]
    assert short(links["infra/outputs.tf"]["calls"]) == [
        ("output.account", "main.tf:data.aws_caller_identity.current"), ("output.bucket_arn", "main.tf:aws_s3_bucket.logs"),
        ("output.vpc_id", "main.tf:module.vpc"), ("output.vpc_id", "main.tf:output.id")]  # the module block, and its output
    assert all(via == "reference" for rows in links.values() for _, _, via, *_ in rows["calls"])
    # the comment that names var.unused creates nothing
    assert not any("var.unused" in b for rows in links.values() for _, b, *_ in rows["calls"])


@needs_relate
def test_terraform_in_the_store_answers_the_standard_questions(project, monkeypatch, capsys):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    for path, text in TERRAFORM.items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(text)
    assert cli.main(["index", "--no-embed"]) == 0
    text = queries.impact("var.region")
    assert "resource" not in text.split("direct callers")[0] and "module module.vpc" in text
    text = queries.impact("aws_s3_bucket.logs")           # two buckets share the name: one block per directory
    first, second = text.split("resource aws_s3_bucket.logs  <other/main.tf")
    assert "output output.bucket_arn" in first and "files affected (1): infra/outputs.tf" in first
    assert "no caller recorded" in second
    assert "terraform-aws-modules/eks" in queries.library_usage() and "hashicorp/aws" in queries.library_usage()
    capsys.readouterr()
    assert cli.main(["search", "main bucket", "--no-semantic"]) == 0
    assert "aws_s3_bucket.logs" in capsys.readouterr().out
    assert store.read("MATCH (f:File {path: 'infra/main.tf'})-[r:DEPENDS_ON]->(t) RETURN t.path AS p, r.via AS via ORDER BY p") == [
        {"p": "infra/modules/vpc/main.tf", "via": "import"}, {"p": "infra/variables.tf", "via": "package"}]


ANSIBLE = {
    "site.yml": """---
# Site playbook
- name: Configure web servers
  hosts: web
  become: true
  vars_files:
    - vars/common.yml
  roles:
    - common
    - role: web
      vars:
        port: 8080
  tasks:
    - name: Install nginx
      ansible.builtin.apt:
        name: nginx
        state: present
      notify: restart nginx

    - name: Extra tasks
      ansible.builtin.import_tasks: tasks/extra.yml
    - name: Deploy with docker
      community.docker.docker_container:
        name: app
      notify:
        - restart nginx
        - reload app
  handlers:
    - name: restart nginx
      ansible.builtin.service:
        name: nginx
        state: restarted

- import_playbook: db.yml
""",
    "db.yml": "- name: Databases\n  hosts: db\n  tasks:\n    - name: Ping\n      ping:\n",
    "tasks/extra.yml": "- name: Say hello\n  debug:\n    msg: hello\n",
    "vars/common.yml": "domain: example.org\nusers:\n  - alice\n  - bob\n",
    "roles/web/tasks/main.yml": """- name: Render config
  template:
    src: nginx.conf.j2
    dest: /etc/nginx/nginx.conf
  notify: reload web
- name: Copy script
  copy: src=run.sh dest=/usr/local/bin/run.sh
- include_tasks: "{{ ansible_os_family }}.yml"
- block:
    - name: Inside block
      debug:
        msg: hi
  when: x
""",
    "roles/web/handlers/main.yml": "- name: reload web\n  service: name=nginx state=reloaded\n  listen: reload app\n",
    "roles/web/defaults/main.yml": "port: 80\n",
    "roles/web/templates/nginx.conf.j2": "listen {{ port }};\n",
    "roles/web/files/run.sh": "#!/bin/sh\necho hi\n",
    "roles/common/tasks/main.yml": "- name: Base packages\n  package:\n    name: curl\n",
}
NOT_ANSIBLE = {
    "docker-compose.yml": "services:\n  web:\n    image: x\n",
    ".github/workflows/ci.yml": "name: CI\non:\n  push:\njobs:\n  test:\n    steps:\n      - name: Test\n        run: x\n",
    "k8s/deploy.yaml": "apiVersion: v1\nkind: Pod\nspec:\n  containers:\n    - name: a\n      image: b\n",
}


def test_ansible_files_are_recognised_by_place_or_content():
    yaml = [p for p in ANSIBLE if p.endswith((".yml", ".yaml"))]
    assert [p for p in yaml if not iac.is_ansible(p, ANSIBLE[p])] == []
    assert [p for p, text in NOT_ANSIBLE.items() if iac.is_ansible(p, text)] == []
    assert not iac.is_ansible("roles/web/files/run.sh", ANSIBLE["roles/web/files/run.sh"])


def test_ansible_symbols(project):
    def listing(path):
        return [(s["kind"], s["qualname"], s["startLine"], s["endLine"])
                for s in extract.extract_symbols(path, ANSIBLE[path], "ansible", 1)]

    assert listing("site.yml") == [
        ("play", "Configure web servers", 3, 32), ("task", "Install nginx", 14, 18), ("task", "Extra tasks", 20, 21),
        ("task", "Deploy with docker", 22, 27), ("handler", "restart nginx", 29, 32), ("play", "import_playbook db.yml", 34, 34)]
    assert listing("roles/web/tasks/main.yml") == [
        ("task", "Render config", 1, 5), ("task", "Copy script", 6, 7),
        ("task", "include_tasks {{ ansible_os_family }}.yml", 8, 8), ("task", "Inside block", 10, 12)]
    assert listing("roles/web/handlers/main.yml") == [("handler", "reload web", 1, 3)]
    assert listing("vars/common.yml") == [("variable", "domain", 1, 1), ("variable", "users", 2, 4)]
    task = extract.extract_symbols("site.yml", ANSIBLE["site.yml"], "ansible", 1)[1]
    assert "state: present" in task["text"]                 # the body of a task is searchable


def test_ansible_dependencies_notify_and_modules(project):
    sources = {p: t for p, t in ANSIBLE.items() if p.endswith((".yml", ".yaml"))}
    files = {}
    for path, text in sources.items():
        symbols = extract.extract_symbols(path, text, "ansible", 1)
        files[path] = deps.facts(path, text, "ansible", symbols, extract.extract_imports(path, text, "ansible"))
    live, scopes = set(ANSIBLE), {}
    edges = deps.resolve(files, live, scopes)
    links = deps.resolve_calls(files, edges, live, scopes)

    assert sorted(edges["site.yml"]) == [
        "db.yml", "roles/common/tasks/main.yml", "roles/web/defaults/main.yml", "roles/web/handlers/main.yml",
        "roles/web/tasks/main.yml", "tasks/extra.yml", "vars/common.yml"]
    assert sorted(edges["roles/web/tasks/main.yml"]) == ["roles/web/files/run.sh", "roles/web/templates/nginx.conf.j2"]
    assert scopes["site.yml"]["role:web"] == "internal" and "?role:web/handlers/main.yml" not in scopes["site.yml"]

    assert short(links["site.yml"]["calls"]) == [
        ("Deploy with docker", "main.yml:reload web"),       # through the name the role's handler listens to
        ("Deploy with docker", "site.yml:restart nginx"), ("Install nginx", "site.yml:restart nginx")]
    assert short(links["roles/web/tasks/main.yml"]["calls"]) == [("Render config", "main.yml:reload web")]
    outside = {(a.split("::")[1], name, root, scope) for a, _, name, _, _, root, scope in links["site.yml"]["external"]}
    assert outside == {("Install nginx", "ansible.builtin.apt", "ansible.builtin", "stdlib"),
                       ("Deploy with docker", "community.docker.docker_container", "community.docker", "external"),
                       ("restart nginx", "ansible.builtin.service", "ansible.builtin", "stdlib")}


@needs_relate
def test_ansible_in_the_store(project, monkeypatch, capsys):
    monkeypatch.setenv("RELATE_CODEMEM_EMBEDDINGS", "none")
    for path, text in {**ANSIBLE, **NOT_ANSIBLE}.items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(text)
    assert cli.main(["index", "--no-embed"]) == 0
    kinds = {r["p"]: (r["k"], r["l"]) for r in store.read("MATCH (f:File) RETURN f.path AS p, f.kind AS k, f.lang AS l LIMIT 100")}
    assert kinds["site.yml"] == ("code", "ansible") and kinds["roles/web/handlers/main.yml"] == ("code", "ansible")
    assert kinds["docker-compose.yml"] == ("config", "yml") and kinds["k8s/deploy.yaml"] == ("config", "yaml")

    capsys.readouterr()
    assert cli.main(["search", "Install nginx", "--no-semantic"]) == 0
    assert "task Install nginx" in capsys.readouterr().out
    text = queries.impact("reload web")                     # what triggers this handler
    assert "task Render config" in text and "task Deploy with docker" in text
    assert "community.docker" in queries.library_usage() and "docker_container" in queries.library_usage("community.docker")
    assert cli.main(["file", "site.yml"]) == 0
    out = capsys.readouterr().out
    assert "roles/web/tasks/main.yml" in out and "tasks/extra.yml" in out

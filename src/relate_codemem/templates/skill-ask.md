---
name: relate-codemem-ask
description: "Answer a question about this project from its relate-codemem memory graph (code structure, calls, dependencies, decisions, meetings, past sessions) before reading files. Use when the user invokes it, or asks to answer from the project memory, the graph or the MCP server."
---

# relate-codemem: answer from the project memory

Question: $ARGUMENTS

Answer it from the project memory graph first, then confirm against the code.

1. Query the memory. Prefer the `relate-codemem` MCP tools when the server is
   connected; the same questions exist as commands (`relate-codemem ...`):

   | Need | MCP tool | Command |
   | --- | --- | --- |
   | find something by words or meaning | `search` | `search "..." [--kind code\|doc\|decision\|meeting\|session\|note]` |
   | decisions, notes, meetings, recent sessions | `recall` | `recall` |
   | one file: revisions, dependencies, imports | `file_info` | `file PATH` |
   | one symbol: definition, calls, callers, hierarchy | `symbol_info` | `symbol NAME` |
   | what a class or method calls | `class_calls` | `calls NAME` |
   | implementations of an interface, redefinitions | `implementations` | `implementations NAME` |
   | callers and affected classes and files | `impact` | `impact NAME [--depth N]` |
| tests that reach a method or class | `tests_for` | `tests NAME` |
| what the current changes affect | `diff_impact` | `diff-impact [REF]` |
| how A ends up calling B | `call_path` | `path A B` |
| libraries used, or where one is used | `library_usage` | `uses [LIBRARY]` |
| why a file is the way it is | `why` | `why PATH` |
   | anything else | `get_schema`, then `read_cypher` | `schema`, then `query "MATCH ... LIMIT 20"` |

2. Treat what comes back as evidence with a date. Open the files it points
   to and check the parts your answer relies on.
3. Answer the question. Say what came from the memory and what you verified
   in the code, and say so plainly when they disagree or when the memory has
   nothing on the subject.

If the store is missing or empty, say so and suggest `/relate-codemem-init`.

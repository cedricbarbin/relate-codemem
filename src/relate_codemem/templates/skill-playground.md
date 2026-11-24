---
name: relate-codemem-playground
description: "Open the relate-codemem project memory graph for visual exploration: serves the store read-only on this machine so that Neo4j Browser can connect to it. Optional argument: the password to use for the user relate, instead of a generated token. Use when the user wants to browse, visualise or explore the memory graph, or asks for the playground."
argument-hint: "[password]"
---

# relate-codemem: explore the graph

Argument (optional password): $ARGUMENTS

`relate-codemem playground` serves the project store read-only on the loopback
interface and prints how to connect Neo4j Browser to it. It runs until
stopped, so start it in a terminal the user can see, or in the background.

- With a password given as argument, pass it through the environment, so
  that it does not appear in the process list:

  ```bash
  RELATE_CODEMEM_PLAYGROUND_PASSWORD='<the argument>' relate-codemem playground
  ```

  On Windows PowerShell: `$env:RELATE_CODEMEM_PLAYGROUND_PASSWORD='<the argument>'; relate-codemem playground`.
  The password needs at least 4 characters and no `:`.
- Without argument, run `relate-codemem playground`: relate then uses a
  generated token, stored in `.claude/relate/db.auth-token`.

A password can also be set once for the project, in the `env` block of
`.claude/settings.local.json` (`RELATE_CODEMEM_PLAYGROUND_PASSWORD`), which
git ignores; suggest it if the user wants the same password every time.

Options: `--bolt PORT` (default 7688), `--http PORT` (default 7475),
`--open` to open Neo4j Browser in the default web browser.

Then tell the user:

- the address to connect to (`bolt://127.0.0.1:7688`) and the user (`relate`);
- the password: the one they gave, or the token in the file above. Do not
  write a generated token in your reply;
- a first query to try, for example
  `MATCH (a:Symbol)-[r:CALLS]->(b:Symbol) RETURN a, r, b LIMIT 100` or
  `MATCH (a:Symbol)-[r:EXTENDS]->(b) RETURN a, r, b LIMIT 100`;
- that the server is read-only, listens on this machine only, and stops with
  Ctrl-C (or when you stop the command).

Stop the server when the user is done.

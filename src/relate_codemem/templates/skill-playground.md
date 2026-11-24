---
name: relate-codemem-playground
description: "Open the relate-codemem project memory graph for visual exploration: serves the store read-only on this machine so that Neo4j Browser can connect to it. Use when the user wants to browse, visualise or explore the memory graph, or asks for the playground."
---

# relate-codemem: explore the graph

`relate-codemem playground` serves the project store read-only on the loopback
interface and prints how to connect Neo4j Browser to it. It runs until
stopped, so start it in the background (or ask the user to run it in a
terminal of their own):

```bash
relate-codemem playground
```

Options: `--bolt PORT` (default 7688), `--http PORT` (default 7475),
`--open` to open Neo4j Browser in the default web browser.

Then tell the user:

- the address to connect to (`bolt://127.0.0.1:7688`), the user (`relate`)
  and that the password is the token stored in the file the command names
  (`.claude/relate/db.auth-token`). Do not repeat the token in your reply;
- a first query to try, for example
  `MATCH (a:Symbol)-[r:CALLS]->(b:Symbol) RETURN a, r, b LIMIT 100` or
  `MATCH (a:Symbol)-[r:EXTENDS]->(b) RETURN a, r, b LIMIT 100`;
- that the server is read-only and stops with Ctrl-C (or when you stop the
  background command).

Stop the server when the user is done.

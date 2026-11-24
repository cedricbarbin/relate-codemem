# Decisions (ADR)

One file per decision, named `NNNN-short-title.md`. Every file in this
directory except this README is indexed into the project memory graph as a
`Decision` (and as searchable chunks) each time it changes.

A status starting with `superseded`, `deprecated` or `rejected` closes the
decision: it stays in history but is no longer returned as current.

Template:

```markdown
---
title: Use relate for project memory
status: accepted          # proposed | accepted | superseded by 0007 | deprecated | rejected
date: 2026-01-15
---

# Use relate for project memory

## Context

What forces the decision.

## Decision

What we do.

## Consequences

What becomes easier, what becomes harder.
```

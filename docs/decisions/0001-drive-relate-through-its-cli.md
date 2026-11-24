---
title: Drive relate through its CLI
status: accepted
date: 2026-11-24
---

# Drive relate through its CLI

## Context

relate-codemem must install with one command on macOS, Linux and Windows and
run inside Claude Code hooks, where a slow or failing start costs every tool
call. relate offers a Python client, but it is not published on PyPI yet and
ships native code.

## Decision

The package has no required dependency. It runs the `relate` binary as a
subprocess for every operation: one script on stdin, parameters in a JSON file
bound as `$p`, results read from the `envelope` output mode. Writes are
wrapped in `.begin` / `.commit` and serialised by a file lock, because relate
accepts one writer process per store.

## Consequences

- Installation is a pure-Python wheel plus the relate installer.
- Each operation pays a process start, about 0.1 s; batching keeps a full
  index to a handful of calls.
- There is no long-lived connection, so hooks cannot hold the store open and
  block each other.
- Moving to the Python client later only touches `store.py`.

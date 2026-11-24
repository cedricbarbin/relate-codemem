# Security

## Reporting a vulnerability

Please report security issues privately through GitHub's "Report a
vulnerability" button on the repository's Security tab, not in a public
issue. Include the version (`relate-codemem --version`), the operating system
and the steps to reproduce.

## What to keep in mind

- The store in `.claude/relate/` contains prompts, replies and file contents
  in clear text. It is ignored by git by default; treat it like source code
  with session history attached.
- With the `openai` embedding provider, indexed text is sent to the configured
  API. The `local` and `none` providers, and `ollama` on localhost, keep
  everything on the machine.
- API keys are read from an environment variable at call time and are never
  written to configuration files or logs.
- The MCP server opens the store read-only on stdio; relate-codemem starts no
  network listener.
- Hook payloads and indexed file contents are data: they are stored and
  searched, never executed.

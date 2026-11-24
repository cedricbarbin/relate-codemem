---
name: relate-codemem-sharedb
description: "Let git track the relate-codemem store so that the project memory (code graph, decisions, sessions) is shared through the repository, or undo it with the argument `off`. Requires an explicit confirmation from the user, because the store holds session prompts and replies that may contain personal data. Use only when the user asks to share, commit or version the memory database."
argument-hint: "[off]"
disable-model-invocation: true
---

# relate-codemem: share the store through git

Argument: $ARGUMENTS

If the argument is `off`, run `relate-codemem sharedb --off`, relay what it
prints (the store may still be in the repository and its history) and stop.

Otherwise, do not change anything before the user has confirmed.

1. Check where the repository goes: `git remote -v`, and when the `gh`
   command is available, `gh repo view --json visibility,nameWithOwner`.
   If you cannot tell whether the repository is public, say so.
2. Show the user this warning, in their language, without shortening it:

   > Sharing the store sends it to everyone who can read the repository. It
   > contains what was typed in Claude Code sessions (prompts), Claude's
   > replies and the shell commands that were run, which can include personal
   > data, names, internal URLs or secrets pasted by mistake, together with
   > file paths, excerpts of documents and meeting notes with their
   > participants. Once pushed, it stays in the git history even if the store
   > is later removed. **This is not recommended on a public repository.**
   > The store is binary data that changes at every session: the repository
   > grows, and two people cannot merge their versions.

   If the repository is public, say it explicitly and advise against going on.
3. Ask for an explicit confirmation (a question with a clear yes and no). An
   earlier general approval does not count. If the answer is not a clear
   yes, stop and change nothing.
4. On a yes, run:

   ```bash
   relate-codemem sharedb --yes
   ```

   It changes `.gitignore` so that git tracks `.claude/relate/db` and its
   description files; the playground token, logs and local state stay
   ignored. It adds and commits nothing.
5. Tell the user what changed and what is left to them: reviewing
   `git status`, then `git add .gitignore .claude/relate` and a commit. Do
   not add, commit or push the store yourself unless the user then asks for
   it; if they do, remind them once more when the repository is public.

To undo later: `/relate-codemem-sharedb off`.

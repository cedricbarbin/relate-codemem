# Meetings

Drop meeting notes and transcripts here, one file per meeting, named
`YYYY-MM-DD-topic.md` (`.txt`, `.vtt` and `.srt` transcripts work too). Every
file except this README is indexed into the project memory graph as a
`Meeting`, with its participants as `Person` nodes and its content as
searchable chunks.

The date comes from the front matter, else from the file name. Participants
come from the front matter, else from a `Participants:` line, else from
speaker labels (`Alice: ...`) that appear at least twice.

Template:

```markdown
---
title: Sprint planning
date: 2026-01-15
participants: Alice Martin, Bob Chen
---

# Sprint planning

## Decisions

- ...

## Actions

- ...

## Transcript

Alice Martin: ...
Bob Chen: ...
```

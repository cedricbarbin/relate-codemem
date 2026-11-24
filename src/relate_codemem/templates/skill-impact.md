---
name: relate-codemem-impact
description: "Impact analysis from the relate-codemem call graph: which methods, classes and files call a method or a class, directly or through other methods, and which implementations or subtypes are involved. Use before changing or removing a method, a class or an interface, or when the user asks what depends on a symbol."
---

# relate-codemem: impact of a change

Symbol: $ARGUMENTS

1. Run the impact query: MCP tool `impact` with `{"name": "<symbol>"}`, or
   `relate-codemem impact <symbol> --depth 3`. Use a qualified name
   (`Customer.save`) when a simple one is ambiguous.
2. If the symbol is an interface, an abstract type or one of their methods,
   also run `implementations` (`relate-codemem implementations <symbol>`):
   a change to the declaration affects every implementation.
3. For the direct callers, open the call sites and check that each one really
   calls this symbol. Links marked `name` were matched by name only, and
   `1 of N` means the call had several possible targets.
4. Report, most affected first: direct callers, then indirect ones, the
   classes and files involved, the implementations or subtypes, and what
   reaches the symbol from outside your reading (tests, entry points).

State the limits in the report: calls whose receiver has no written type are
not in the graph, and reflection or dependency injection is invisible to it.
So the list is a lower bound; a text search for the name is a useful
cross-check.

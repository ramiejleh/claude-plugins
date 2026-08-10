---
description: Build a paginated HTML walkthrough of a feature — real code excerpts in execution order, one step per page, with explanations hidden behind a click so you read the code first. Works on any existing code, whoever wrote it and whenever.
argument-hint: <what to walk through, e.g. "the auth of this application">
allowed-tools: Read, Grep, Glob, Bash
---

# Build a walkthrough

`$1` (and everything after it) describes what the user wants to understand — a
feature, a flow, a subsystem. It is deliberately loose: "the auth of this
application", "how uploads get processed", "the billing webhooks".

Load the **`authoring-walkthroughs`** skill before starting. It carries the
tracing method, the ordering rule, and how to write titles and bubbles that
survive contact with a reader — none of which is obvious, and all of which
decides whether the output is useful or just a nicely-formatted summary.

The short version of the workflow:

1. **Resolve the target.** Glob/Grep for the entry points and the files
   involved. Cheap and broad before deep.
2. **Trace the flow** from the entry point through to where the work finishes.
3. **Propose the outline** — step titles only — if it is more than about five
   steps. Let the user correct scope before you do the expensive part. "The auth
   of this application" is ambiguous and your first reading of it may be wrong.
4. **Pin the excerpts**: real paths, real line numbers, verified by reading the
   files.
5. **Write the bubbles.**
6. **Render:**

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render.py" <path-to-walkthrough.json>
```

Write the JSON to a scratch path, not into the user's project. The renderer
writes the HTML to `.walkthroughs/<id>.html` inside the repo and gitignores that
directory on first use.

Tell the user the path when it is done and let them open it. Do not summarise
what the walkthrough says — that is the one thing that would defeat it.

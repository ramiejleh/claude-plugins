---
description: Rebuild an existing walkthrough from its saved source. Checks first whether the code it explains has changed — if it has, the walkthrough needs re-tracing rather than re-rendering, and this says so.
argument-hint: <walkthrough-id>
allowed-tools: Bash, Read, Grep, Glob
---

# Refresh a walkthrough

`$1` is the walkthrough id (the filename in `.walkthroughs/`, without `.html`).
Run `/walkthrough:list` first if the user is not sure.

**Check freshness before doing anything:**

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/catalog.py" check "$1" --root "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
```

## If it reports fresh

Nothing has moved. Re-render from the saved source to pick up any changes to the
walkthrough itself or to the renderer:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render.py" <root>/.walkthroughs/$1.json
```

## If it reports stale

**Do not just re-render.** The saved JSON pins line numbers, and the cited files
have moved on — re-rendering would draw the old ranges against new code and
confidently point at the wrong lines. A walkthrough that points at the wrong
lines is worse than none, because the reader learns a wrong map and has no reason
to doubt it.

Instead:

1. Tell the user which files changed.
2. Re-read those files and re-trace that part of the flow.
3. Edit `.walkthroughs/$1.json` — fix the line ranges, and revise any bubble
   whose code no longer says what it said. Steps may need adding or dropping if
   the implementation actually changed shape.
4. Re-render.

Load the `authoring-walkthroughs` skill before step 3. The rules about ordering,
titles and bubbles apply to an edit exactly as they do to a fresh build.

Keep the same `id` so it replaces the existing walkthrough rather than piling up
a second copy.

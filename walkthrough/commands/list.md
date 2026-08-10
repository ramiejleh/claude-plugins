---
description: List the walkthroughs built for this project, showing how many steps each has, when it was built, and whether the code it explains has changed since. Rebuilds the index page.
argument-hint: (no arguments)
allowed-tools: Bash
---

# List walkthroughs

Show every walkthrough in `.walkthroughs/` and whether each still matches the
tree.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/catalog.py" list --root "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
```

Relay the output. It also rewrites `.walkthroughs/index.html`, so mention that
path — it is the browsable version of the same list.

## On "stale"

Every walkthrough records a hash of each file it cites. **Stale** means one of
those files has changed, so its line numbers may no longer point where they did.

That matters more than it sounds: a walkthrough pointing at the wrong lines is
worse than no walkthrough, because it teaches a wrong map of the system.

The fix is to **regenerate** the stale ones — re-trace the flow and rebuild the
JSON — not to re-render. Re-rendering redraws the same line numbers against
changed code. Offer to regenerate; do not do it unprompted, since the user may
have a reason to keep the old one.

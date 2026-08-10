---
description: Arm a review gate right now, without waiting for the line threshold. Plants the marker and starts the two-stage review over whatever Claude has written so far this session.
argument-hint: (no arguments)
allowed-tools: Bash, Read, Grep, Glob
---

# Review now

Trip the gate deliberately, at a moment of the user's choosing rather than
whenever the counter happens to fill up. Useful at the end of a feature, before a
commit, or any time the user wants to catch up on what has been built.

It covers every unreviewed change in the project, including work from earlier
conversations that never tripped a gate on its own.

Run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gate.py" review
```

This plants the markers and blocks writes exactly as an automatic gate does. From
there, follow the `review-gate` skill: register your question first, orient the
user on what changed, point them at every marker, and submit their answer.

Two cases to handle before charging ahead:

- **A gate is already armed** — the command prints what is still outstanding.
  Pick up from there instead of arming another.
- **Nothing is unreviewed in this project** — the command says so. There is
  nothing to review; do not manufacture a gate.

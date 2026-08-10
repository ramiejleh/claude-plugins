---
description: Show how much unreviewed code has built up in this project, how many lines are left before the next gate fires, which files changed, and whether a gate is currently armed.
argument-hint: (no arguments)
allowed-tools: Bash
---

# Review gate status

Report where this project stands against the review threshold. The count is
per project and carries across conversations, so it is not the current session's
number — it is everything unreviewed in the repo.

Run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gate.py" status
```

Relay the output as-is — it is already written for a human. Do not editorialise
the numbers or reassure the user that they have "plenty of room left"; the count
is the point.

If a gate is armed, the output says so and lists which stages are outstanding. In
that case, follow the `review-gate` skill to run the gate rather than just
reporting the status and stopping.

---
description: Show how much code Claude has written since the last review gate, how many lines are left before the next one fires, which files were touched, and whether a gate is currently armed.
argument-hint: (no arguments)
allowed-tools: Bash
---

# Review gate status

Report where the current session stands against the review threshold.

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

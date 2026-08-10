---
description: Show or change the review gate level — strict (150 lines), medium (400) or loose (1000). Controls how much code Claude may write before it is blocked pending a human review.
argument-hint: [strict|medium|loose]
allowed-tools: Bash
---

# Review gate level

`$1` is optional.

**With no argument**, show the current level and every configured threshold:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gate.py" level
```

**With an argument**, set it:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gate.py" level "$1"
```

The setting is global — it applies to every project and every session, and it
persists in `~/.claude/review-gate/config.json`.

Thresholds count lines added plus lines removed in the project — from the git
working tree where there is one, otherwise from Claude's tool calls — and reset
to zero every time a gate is passed.

| Level  | Trips at   | Roughly                          |
| ------ | ---------- | -------------------------------- |
| strict | 150 lines  | once per feature-sized chunk     |
| medium | 400 lines  | once per typical PR              |
| loose  | 1000 lines | only when things have run away   |

Changing the level does not clear a gate that is already armed. If one is armed,
say so and run it per the `review-gate` skill.

If the user wants a threshold that is not one of the three, the numbers live
under `thresholds` in the config file — point them there rather than inventing a
fourth level.

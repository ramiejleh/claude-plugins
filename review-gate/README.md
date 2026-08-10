# review-gate

Claude writes code faster than you can read it. Left alone, a long session ends
with you nominally owning a few thousand lines you have never looked at.

This plugin stops that. A hook counts every line Claude writes and, once the
count crosses a threshold, **blocks Claude's write tools and shell** until you
have actually reviewed the code.

It is not a reminder or a nudge. It is a hook — the harness runs it, not Claude,
and Claude cannot decline to run it.

## The gate

When the threshold trips, two things have to happen before writing resumes.

**Stage 1 — a marker in your code.** The hook plants a comment line carrying a
random token into the file that changed most:

```js
#!/usr/bin/env node
// [REVIEW-GATE 8669de] 412 lines written since your last review. Read the changes above, then delete this line to unblock Claude.
import { Router } from 'express';
```

You delete it. Claude can't — `Edit`, `Write`, `MultiEdit`, `NotebookEdit` and
`Bash` are all blocked while the gate is armed, so there is no tool left that
could remove it. The script confirms the token is gone by reading the file.

`Read`, `Grep` and `Glob` stay available on purpose: during a gate Claude's job
is to help you read the code, and those are the tools for it.

**Stage 2 — a question.** Claude asks you something about the changes that you
can only answer having read them:

```
The retry helper in client.ts gives up after 3 attempts rather than
retrying forever. What breaks if a request is still failing at that point?
```

Answer it in your own words — matching is lenient about phrasing, strict about
substance. Three misses voids the question and Claude has to walk you through
that part of the code and ask a different one.

Once both stages clear the gate lifts by itself and the counter resets.

## Levels

Thresholds count lines added plus lines removed, per session, reset on every
passed gate.

| Level  | Trips at   | Roughly                        |
| ------ | ---------- | ------------------------------ |
| strict | 150 lines  | once per feature-sized chunk   |
| medium | 400 lines  | once per typical PR (default)  |
| loose  | 1000 lines | only when things have run away |

```
/review-gate:level strict
```

## Commands

| Command | What it does |
| --- | --- |
| `/review-gate:status` | Lines since the last review, how many are left, files touched |
| `/review-gate:level [strict\|medium\|loose]` | Show or change the threshold |
| `/review-gate:review` | Arm a gate now, before the threshold — good before a commit |

## Install

```
/plugin marketplace add ramiejleh/claude-plugins
/plugin install review-gate@ramiejleh-plugins
/reload-plugins
```

It applies to every project and every session from then on. Requires `python3`,
which ships with macOS and most Linux distributions.

## Configuration

`~/.claude/review-gate/config.json`, created on first use:

```json
{
  "level": "medium",
  "thresholds": { "strict": 150, "medium": 400, "loose": 1000 },
  "quiz_enabled": true,
  "block_bash_during_gate": true,
  "ignore_globs": ["**/node_modules/**", "**/*.lock", "..."]
}
```

Set your own numbers under `thresholds` if none of the three levels fit. Turning
`quiz_enabled` off leaves stage 1 only — still a real gate, just a faster one.
Generated and vendored files are excluded via `ignore_globs` so a `npm install`
diff never trips it.

There is deliberately no command to pause or skip a gate. If you want it off,
disable the plugin.

## The audit log

Every gate is appended to `~/.claude/review-gate/log.jsonl` — when it armed, what
was asked, what was answered, how many attempts:

```
{"at":"2026-08-10T09:12:44+00:00","event":"gate_armed","lines":412,...}
{"at":"2026-08-10T09:14:02+00:00","event":"quiz_passed","attempts":1,"given":"..."}
```

## What this does and does not guarantee

Worth being straight about, because the two stages are not equally strong.

**Stage 1 is enforced.** The token is verified by reading the file, and every
tool that could edit it is blocked. Claude cannot clear it, and cannot fake
having cleared it.

**Stage 2 rests on good faith.** Claude writes the question, registers the
expected answer, and submits yours. Nothing at the file level prevents it from
arming a trivial question or answering on your behalf. The bundled skill is
explicit about why that defeats the purpose, and the log records every gate so
you can check. But it is a norm, not a lock.

The honest summary: stage 1 guarantees a human opened the file. Stage 2 makes it
likely they understood it. If you only trust one, trust the first.

## How lines are counted

- `Write` — every line of the content, since all of it is newly authored
- `Edit` / `MultiEdit` — lines added plus lines removed, per hunk
- `NotebookEdit` — lines of the new cell source

At 80% of the threshold Claude gets a heads-up so it can reach a coherent
stopping point instead of being cut off mid-refactor.

## License

MIT © Rami Ejleh

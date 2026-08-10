# review-gate

Claude writes code faster than you can read it. Left alone, a long session ends
with you nominally owning a few thousand lines you have never looked at.

This plugin stops that. A hook measures how much unreviewed code has built up in
the project and, once it crosses a threshold, **blocks Claude's write tools and
shell** until you have actually reviewed it.

It is not a reminder or a nudge. It is a hook — the harness runs it, not Claude,
and Claude cannot decline to run it.

The count is **per project and persists across conversations**, so starting a
fresh session does not wipe the slate.

## The gate

When the threshold trips, two things have to happen before writing resumes.

**Stage 1 — markers in your code.** The hook plants a comment line carrying a
random token into each of the files that changed most (up to five). A single
marker only ever proves one file was opened:

```js
#!/usr/bin/env node
// [REVIEW-GATE 8669de] 412 lines written since your last review. Read the changes above, then delete this line to unblock Claude.
import { Router } from 'express';
```

You delete them — all of them. Claude can't: `Edit`, `Write`, `MultiEdit`,
`NotebookEdit` and `Bash` are blocked while the gate is armed, so there is no tool
left that could. The script confirms each token is gone by reading the file.

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

Claude has to register the question **before** you start deleting markers. A
question written after you have been talking about the code can be shaped around
something you already said, which tests nothing.

Both stages always apply. There is no setting that reduces a gate to one of them.

There is also a short minimum on each gate, scaled to the size of the diff — it
only rules out the clear that happens faster than anyone could open a file.

Once both stages clear the gate lifts by itself and the counter resets.

## Levels

Thresholds count lines added plus lines removed in the project, reset on every
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
| `/review-gate:status` | Unreviewed lines in the project, how many are left, files changed |
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
  "ignore_globs": ["**/node_modules/**", "**/*.lock", "..."]
}
```

Set your own numbers under `thresholds` if none of the three levels fit.
Generated and vendored files are excluded via `ignore_globs` so an `npm install`
diff never trips it.

That is the whole of it. There is deliberately no knob to drop a stage, unblock
the shell, pause a gate or skip one — each of those would hollow out the gate
rather than tune it. If you want it off, disable the plugin.

## The audit log

Every gate is appended to `~/.claude/review-gate/log.jsonl` — when it armed, what
was asked, what was answered, how many attempts:

```
{"at":"2026-08-10T09:12:44+00:00","event":"gate_armed","lines":412,...}
{"at":"2026-08-10T09:14:02+00:00","event":"quiz_passed","attempts":1,"given":"..."}
```

## What this does and does not guarantee

Worth being straight about, because the two stages are not equally strong.

**Stage 1 is enforced.** Every token is verified by reading the file, and every
tool that could edit it is blocked. Claude cannot clear a marker, and cannot fake
having cleared one.

**The count is enforced.** In a git repo it comes from the working tree, so
writing through a shell heredoc or handing the job to a subagent does not dodge
it. Per-project state means a fresh conversation does not reset it either.

**Stage 2 rests on good faith.** Claude writes the question, registers the
expected answer, and submits yours. Nothing at the file level prevents it from
arming a trivial question or answering on your behalf. Requiring the question up
front stops it being retro-fitted to something you said, and the log records
every gate — but it is a norm, not a lock.

The honest summary: the count and stage 1 guarantee a human opened the files.
Stage 2 makes it likely they understood them. A user determined to wave a gate
through can still do it, and that is out of scope for a tool like this.

## How lines are counted

**In a git repo** (the normal case) — `git diff` against the last reviewed state
plus untracked files, added and deleted lines both. This is what makes writes
outside a tool call count.

**Outside a repo** — a tally of Claude's tool calls: a `Write` counts its whole
content, an `Edit` counts added plus removed lines.

Whichever is larger wins, so code that was generated and then reverted before you
ever saw it still counts. A commit rebaselines the number, since committing is a
natural review point. Your own hand edits count too — they are cheap to review.

At 80% of the threshold Claude gets a heads-up so it can reach a coherent
stopping point instead of being cut off mid-refactor.

## License

MIT © Rami Ejleh

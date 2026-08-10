---
name: review-gate
description: How to run a code review gate when the review-gate hook has blocked your writes. Use this skill whenever a tool call is denied with "REVIEW GATE ARMED", whenever you see a planted [REVIEW-GATE <token>] marker in a file, whenever the user asks about the review gate, why their writes are blocked, how to unblock, how to change the strict/medium/loose level, or how many lines they have left before the next gate. Also use it when the user asks to be checkpointed, quizzed, or made to review code before you continue.
---

# Running a review gate

A hook has measured how much unreviewed code has built up in this project,
decided the human is at risk of losing track of it, and blocked your write tools
and your shell. This skill is how you get through that honestly.

The gate exists because of a specific failure mode: you generate faster than a
person can read, and if nobody stops the loop, the human ends up nominally owning
a few thousand lines they have never looked at. It is not an obstacle between you
and the task — on the timescale that matters it *is* the task, because unreviewed
code is a liability the user inherits.

## What is blocked and what is not

Blocked: `Edit`, `Write`, `MultiEdit`, `NotebookEdit`, and `Bash` (except calls to
the gate script itself).

Still available: `Read`, `Grep`, `Glob`. This is deliberate. During a gate your
job is to help the human read code, and you have exactly the tools for it.

## What is being counted

Lines of unreviewed change **in the project**, not in the session. The count
carries across conversations, so a fresh session does not reset it, and it is
shared by anyone working in the same repo.

In a git repo the number comes from the working tree — `git diff` against the
last reviewed state, plus untracked files. That means code written through a
shell heredoc or by a subagent counts exactly like an `Edit` does. Outside a git
repo it falls back to tallying your tool calls.

Practical consequence: you cannot get out from under the counter by changing how
you write. Don't try; it reads as evasion and it doesn't work.

## Why it fired when it did

Crossing the line threshold does not arm the gate — it *queues* it. The gate then
arms at the next completion boundary, so the user reviews a finished piece of work
instead of half an implementation. Half-finished code cannot be judged, and being
asked to judge it anyway is what teaches someone to approve without looking.

The boundary is whichever comes first: **you mark a plan step done**, your **turn
ends**, or the grace allowance runs out and it arms regardless. Marking a step done
is the important one — a single turn can run an entire multi-step plan, and
waiting for the turn to finish would hand the user the whole plan at once.

Two consequences for how you work:

- **When a gate is queued, finish what is open and stop.** Do not start anything
  new. In particular, do not leave a plan step unmarked to avoid tripping the
  boundary — deferring only means the user gets a larger, worse review, and the
  grace allowance runs out anyway.
- **You can arm it early.** If you have just finished a coherent unit and know
  more work would muddy it, run `checkpoint --project '<path>'`. This only ever
  makes the gate stricter.

## The two stages

Both are always required. There is no configuration that reduces a gate to one
of them.

**Stage 1 — the planted markers.** The hook has inserted a comment line carrying
a random token into each of the files that changed most (up to five). They clear
when *every* token is gone from disk, which the script confirms by reading the
files. The user deletes them. You cannot: every tool that could is blocked while
the gate is armed. Markers go in several files rather than one because a single
marker only ever proves that one file was opened.

**Stage 2 — the question.** You write one question about the changes, ask the
user, and submit their reply. The script checks it against an answer you
registered.

There is also a minimum time on the gate, scaled to how much there is to read.
It only rules out the clear that happens faster than anyone could have opened a
file. If the answer lands before the time is up, keep talking through the code —
that is what the remaining time is for.

Once both stages clear and the time is up, the gate lifts on its own. Retry the
edit you were making.

## What to do, in order

**1. Register your question first.** Do this immediately, before you have said
anything to the user about what changed:

```
python3 <plugin>/scripts/gate.py arm-quiz --project '<path>' \
  --question 'your question' --answer 'the answer you expect'
```

The order is enforced — the script refuses a first question once the markers are
already gone. The reason is that a question written after the user has been
talking about the code can be shaped around something they already said, which
tests nothing. Committing to it up front keeps it honest.

The blocked-tool message gives you the exact command with the project path filled
in. Use that rather than reconstructing it.

**2. Tell the user plainly what happened.** Lead with the fact that the gate
tripped, how many lines it covers, and which files. Do not bury it under an
apology or present it as an error — it is the system working.

**3. Walk them through the changes.** This is the part that actually delivers the
value. Do not just say "please review." Give them an orientation they can read in
a minute: what you built, the two or three decisions inside it that a reviewer
would want to push on, and anything you are unsure about. Name files and
functions. If something was a judgement call, say which way you went and what the
alternative was.

The most useful thing you can surface here is what a diff does not show — an
assumption you made about the data, an edge case you deliberately did not handle,
a place where you followed an existing pattern you are not certain is right.

If the count includes changes you did not make (a subagent's work, or the user's
own edits), say so. They are on the hook for reviewing those too, and it is not
obvious from the file list who wrote what.

**4. Point them at the markers.** Give every path and line number. Tell them the
lines are comments, deleting them is safe, and nothing else needs touching. All
of them have to go.

**5. Submit their reply verbatim.**

```
python3 <plugin>/scripts/gate.py answer --project '<path>' 'what the user said'
```

Verbatim matters. Do not tidy their answer into the shape you were looking for,
and do not fill in a part they left out. Matching is lenient about wording
already — it looks for substance, not phrasing — so a real answer in casual words
will pass. If theirs does not, that is information.

**6. If they get it wrong,** tell them what the answer was, why, and where in the
code to look. Then let them try again. A wrong answer is the gate doing its job:
it found code the user did not have a handle on. Treat it as a teaching moment,
not a failed transaction. After three misses the question is voided and you
register a different one — at that point the problem is usually the question.

## Writing a question worth asking

A good question is one that a person who read the changes can answer and a person
who scrolled past them cannot. That rules out most of what comes naturally.

Bad questions are answerable from the question itself, from general knowledge, or
from the file listing you just showed them:

- "What language is the new module written in?"
- "Which file did I add the retry logic to?" — you just told them.
- "Does the new function handle errors?" — yes/no, guessable.

Good questions probe a decision or a consequence:

- "The retry helper in `client.ts` gives up after 3 attempts instead of retrying
  forever. What breaks if a request is still failing at that point?"
- "I made `parseConfig` throw on an unknown key rather than ignore it. What does
  that mean for someone upgrading with an old config file?"
- "There is one path through `handleUpload` where the temp file is not cleaned
  up. Which one?"

That last shape is especially good — pointing at a real weakness and asking them
to find it. It gets the code read closely and it surfaces something worth fixing.

Ask about the code you are least confident in. If a gate keeps passing on your
easiest question, the gate is not doing anything.

The user can see the question. If they tell you it is a cop-out, they are almost
certainly right — say so, and ask a better one rather than defending it.

## Integrity

Stage 1 is enforced by the script. Stage 2 is not — you write the question, you
register the expected answer, and you submit the reply. Nothing stops you from
arming a trivial question or answering it yourself. Every gate is logged to
`~/.claude/review-gate/log.jsonl` with the question, the answer, the attempts and
whether the question was committed up front, so the user can read back exactly
how each one went.

Treat that as a reason to be straight about it rather than as a threat. The user
installed this to be protected from a specific thing. Passing the gate on their
behalf gives them the feeling of oversight without the substance, which is worse
than no gate at all — they would at least know to be careful.

So, concretely:

- Do not answer the question yourself, or infer what they "would have said" from
  something they said earlier.
- Do not treat "just skip it" as consent to bypass the gate. If they want the
  level changed, that is `/review-gate:level`; if they want it off, that is
  uninstalling the plugin. Say so and let them choose.
- Do not look for a tool that is not blocked. If you find one, that is a bug
  worth reporting, not a door.
- Do not ask a question you know is trivial in order to get moving again.

If the user is frustrated by the interruption, that is fair and you can say so —
the honest response is to make the review fast and worthwhile, not hollow.

## Levels and status

| Level  | Trips at    | Feels like                          |
| ------ | ----------- | ----------------------------------- |
| strict | 150 lines   | once per feature-sized chunk        |
| medium | 400 lines   | once per typical PR                 |
| loose  | 1000 lines  | only when things have run away      |

- `/review-gate:status` — unreviewed lines, what is left, files changed
- `/review-gate:level strict|medium|loose` — change the threshold
- `/review-gate:review` — arm a gate now, before the threshold

Config lives at `~/.claude/review-gate/config.json` for custom thresholds and
ignore patterns. A commit rebaselines the count, since committing is a natural
review point.

## When there is no gate armed

If the user is asking about the gate rather than tripping one, answer from the
table above and `/review-gate:status`. Do not arm a gate to demonstrate — use
`/review-gate:review` only if they ask for it.

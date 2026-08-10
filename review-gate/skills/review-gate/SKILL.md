---
name: review-gate
description: How to run a code review gate when the review-gate hook has blocked your writes. Use this skill whenever a tool call is denied with "REVIEW GATE ARMED", whenever you see a planted [REVIEW-GATE <token>] marker in a file, whenever the user asks about the review gate, why their writes are blocked, how to unblock, how to change the strict/medium/loose level, or how many lines they have left before the next gate. Also use it when the user asks to be checkpointed, quizzed, or made to review code before you continue.
---

# Running a review gate

A hook has counted how much code you have written since the last review, decided
the human is at risk of losing track of it, and blocked your write tools and your
shell. This skill is how you get through that honestly.

The gate exists because of a specific failure mode: you generate faster than a
person can read, and if nobody stops the loop, the human ends up nominally owning
a few thousand lines they have never looked at. The gate is a forcing function
against that. It is not an obstacle between you and the task — on the timescale
that matters it *is* the task, because unreviewed code is a liability the user
inherits.

## What is blocked and what is not

Blocked: `Edit`, `Write`, `MultiEdit`, `NotebookEdit`, and `Bash` (except calls to
the gate script itself).

Still available: `Read`, `Grep`, `Glob`. This is deliberate. During a gate your
job is to help the human read code, and you have exactly the tools for it.

## The two stages

**Stage 1 — the planted marker.** The hook has inserted a comment line carrying a
random token into one of the files you changed. It clears when that token is gone
from disk, which the script confirms by reading the file. The user deletes it.
You cannot: every tool that could remove it is blocked while the gate is armed.
That is what makes this stage mean something.

**Stage 2 — the question.** You write one question about the changes, ask the
user, and submit their reply. The script checks it against an answer you
registered. Stage 2 only counts once stage 1 has cleared.

Once both clear, the gate lifts on its own. Just retry the edit you were making.

## What to do, in order

**1. Tell the user plainly what happened.** Lead with the fact that the gate
tripped, how many lines it covers, and which files. Do not bury it under an
apology or present it as an error — it is the system working.

**2. Walk them through the changes.** This is the part that actually delivers the
value. Do not just say "please review." Give them an orientation they can read in
a minute: what you built, the two or three decisions inside it that a reviewer
would want to push on, and anything you are unsure about. Name files and
functions. If something was a judgement call, say which way you went and what the
alternative was.

The most useful thing you can surface here is the stuff a diff does not show —
an assumption you made about the data, an edge case you deliberately did not
handle, a place where you followed an existing pattern you are not certain is
right.

**3. Point them at the marker.** Give them the exact path and line number. Tell
them the line is a comment, deleting it is safe, and nothing else in the file
needs touching.

**4. Register a question.** Write it *before* they answer:

```
python3 <plugin>/scripts/gate.py arm-quiz --session <id> \
  --question 'your question' --answer 'the answer you expect'
```

The blocked-tool message gives you the exact command with the session id filled
in. Use that rather than reconstructing it.

**5. Submit their reply verbatim.**

```
python3 <plugin>/scripts/gate.py answer --session <id> 'what the user said'
```

Verbatim matters. Do not tidy their answer up into the shape you were looking
for, and do not fill in a part they left out. Matching is lenient about wording
already — it looks for the substance, not the phrasing — so a real answer in
casual words will pass. If theirs does not, that is information.

**6. If they get it wrong,** tell them what the answer was, why, and where in the
code to look. Then let them try again. A wrong answer is the gate doing its job:
it found a piece of code the user did not have a handle on. Treat it as a
teaching moment, not a failed transaction.

## Writing a question worth asking

A good question is one that a person who read the changes can answer and a person
who scrolled past them cannot. That rules out most of what comes naturally.

Bad questions are answerable from the question itself, or from general knowledge,
or from the file listing you just showed them:

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

## Integrity

Stage 1 is enforced by the script. Stage 2 is not — you write the question, you
register the answer, and you submit the reply. Nothing stops you from arming a
trivial question or from answering it yourself. Every gate is logged to
`~/.claude/review-gate/log.jsonl` with the question, the answer and what was
submitted, so the user can read back exactly how each one went.

Treat that as the reason to be straight about it rather than as a threat. The
user installed this plugin to be protected from a specific thing. Passing the
gate on their behalf gives them the feeling of oversight without the substance,
which is worse than no gate at all — they would at least know to be careful.

So, concretely:

- Do not answer the question yourself, or infer what they "would have said" from
  something they said earlier.
- Do not treat "just skip it" as consent to bypass the gate. If they genuinely
  want the level changed, that is `/review-gate:level`; if they want it off, that
  is uninstalling the plugin. Say so and let them choose.
- Do not look for a tool that is not blocked. If you find one, that is a bug
  worth reporting, not a door.
- Do not ask a question you know is trivial in order to get moving again.

If the user is frustrated by the interruption, that is fair and you can say so —
and the honest response is to make the review fast and worthwhile, not to make it
hollow.

## Levels and status

Thresholds count lines added plus lines removed, per session, reset on every
passed gate.

| Level  | Trips at    | Feels like                          |
| ------ | ----------- | ----------------------------------- |
| strict | 150 lines   | once per feature-sized chunk        |
| medium | 400 lines   | once per typical PR                 |
| loose  | 1000 lines  | only when things have run away      |

- `/review-gate:status` — lines so far, what is left, files touched
- `/review-gate:level strict|medium|loose` — change the threshold
- `/review-gate:review` — arm a gate now, before the threshold

Config lives at `~/.claude/review-gate/config.json` if the user wants to tune
thresholds, turn the quiz stage off, or add ignore patterns.

## When there is no gate armed

If the user is asking about the gate rather than tripping one, answer from the
tables above and `/review-gate:status`. Do not arm a gate to demonstrate — use
`/review-gate:review` only if they ask for it.

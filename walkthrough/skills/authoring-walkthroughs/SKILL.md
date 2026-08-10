---
name: authoring-walkthroughs
description: How to build a paginated HTML walkthrough of a feature — tracing a flow through a codebase, ordering the steps, pinning real code excerpts and writing the hidden explanation bubbles. Use this skill whenever the user runs /walkthrough, asks you to walk them through how something works in their codebase, asks how a feature or flow is implemented, says they cannot explain part of their own system, or wants to understand code they did not write. Also use it when editing or regenerating an existing walkthrough.
---

# Authoring a walkthrough

The output is an HTML page the user reads like a book: one step per page, real
code excerpts with the relevant lines highlighted, and an explanation for each
that **stays hidden until they click it**.

That last detail is the entire design. Visible prose gets read *instead of* code.
Prose behind a click inverts the order — they read the highlighted lines, form
their own understanding, then click to check themselves. Every page is a small
self-test.

Everything below follows from that. If you find yourself writing something that
would be just as useful with all the bubbles open, you have written a summary,
and a summary is what the user is trying to escape.

## The shape of the job

You author a JSON file. A script renders it, reading the actual code from disk.

**Never transcribe source into the JSON.** You supply `path`, `focus` line range
and the bubble text; the renderer pulls the bytes. Transcribed code drifts from
the file or gets invented outright, and the filesystem already has the truth.

```jsonc
{
  "id": "auth",                    // short slug, becomes the filename
  "title": "Authentication",
  "subtitle": "How a request gets authenticated, from middleware to refresh",
  "root": "/abs/path/to/repo",
  "steps": [
    {
      "title": "Where every request enters",
      "excerpts": [
        {
          "path": "src/middleware.ts",   // relative to root
          "focus": [12, 18],             // real 1-indexed file lines
          "context": 5,                  // optional, default 5
          "bubble": "…"                  // hidden until clicked
        }
      ]
    }
  ]
}
```

Render with:

```
python3 <plugin>/scripts/render.py /tmp/walkthrough-<id>.json
```

It validates first and refuses to render on contract errors. Warnings are advice
about the walkthrough drifting off-design — read them, they are usually right.

## Tracing

1. **Resolve the target.** "The auth of this application" could mean the login
   flow, session handling, or permission checks. Glob and Grep broadly first.
2. **Find the entry point** — the route, handler, listener or CLI command where
   control actually enters. Everything downstream orders itself from there.
3. **Follow it through** to where the work completes. Note the branches worth
   showing: the error path, the cache hit, the retry.
4. **Read every file you cite.** Line numbers must be real; a walkthrough that
   points at the wrong line is worse than none, because it teaches the wrong map.

**Propose the outline first** — step titles only — when it runs past about five
steps. It costs almost nothing and it is the moment the user can say "you missed
the OAuth callback" or "skip the middleware, I wrote it." Correcting scope before
you write eleven pages of bubbles is worth the interruption. Below five steps,
just build it.

## Ordering

**Execution order, not the order the code was written or the order files sit in a
directory.** Entry point → validation → the work → storage → response. That is
what "understanding the flow" means, and it is the thing a plain diff can never
give them.

Where a flow forks, finish the main path first and treat the branch as its own
later step. Interleaving them loses the thread.

## Writing titles

Titles **orient**. They tell the reader where they are, not what to conclude.

- Good: "Where the session token gets refreshed"
- Bad: "Refreshes the token inside the skew window so queued writes don't 401"

The second is the bubble's content. Putting it in the title leaks the answer
before they have looked at the code, and the click becomes pointless.

Keep them under about a dozen words. The validator warns past that, because a
long title is nearly always one that has started explaining.

## Writing bubbles

Write for someone who has **just read the highlighted lines** and formed a guess.
The bubble confirms or corrects it. It is not a briefing for someone who arrived
cold.

- **Lead with the non-obvious part.** If the code plainly says it, there is
  nothing to reveal — which usually means the excerpt is wrong, not that the
  bubble should restate it.
- **Say why, not what.** "Retries three times" is visible. "Three because the
  upstream rate-limits after that, and a fourth would get the whole IP blocked"
  is not.
- **Name the consequence.** What breaks if this line were different. That is what
  turns a reader into someone who could have written it.
- **Ask, where asking beats telling.** Ending on a question that points back at
  the code — "what happens to the queued writes if this throws?" — keeps them in
  the code instead of moving them past it.
- **Under 60 words.** The validator warns past that. A long bubble is a step that
  should have been two.

## Several regions of the same file

Excerpts sharing a path inside one step are rendered as **one continuous file
view** with each region highlighted in place. Regions close together sit in the
same run of code; the stretch between distant ones collapses to a `⋯ N lines
hidden` seam the reader can open. You never get the same file twice on a page.

That makes the choice a question about meaning, not layout:

- **Two regions, one step** when they are a single idea that happens to live in
  two places — a guard and the branch it protects, a queue and its drain.
- **Two steps** when they are two ideas. The file view stays continuous either
  way, so the next step simply highlights the next section. Splitting costs you
  nothing and gives the reader a page per idea.

Ordering within a file is by line number, not by the order you list them, since
the merged view has to read top to bottom.

## Excerpt discipline

- **Focus tightly.** The `focus` range is the lines this step is *about*; context
  either side is orientation. If the focus is 30 lines, the step is too big.
- **One step, one idea**, even across several files. A step that shows the caller
  and the callee together is good; a step that shows four unrelated functions is
  a page the reader will skim.
- **Cross the seam.** The valuable steps are boundary crossings, error paths, and
  the places someone would guess wrong — not the happy path in a straight line.
- **Prefer more, smaller steps.** Ten legible pages beat four dense ones. Nothing
  is gained by compressing.
- **No page scrolling.** If an excerpt runs past ~40 lines the validator warns.
  Split it.

## Afterwards

Give the user the path and stop. **Do not summarise what the walkthrough
contains** — if they can get the gist from your message, they will not open it,
and the whole thing was pointless.

If they come back with corrections ("step 4 is wrong", "you missed the refresh
path"), edit the JSON and re-render to the same id.

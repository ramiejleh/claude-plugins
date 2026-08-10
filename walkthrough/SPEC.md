# walkthrough — specification

**Status:** draft, pre-implementation
**Plugin:** `walkthrough@DefyAtrophy`

## What it is

You type `/walkthrough the auth of this application`. Claude traces how that
feature actually works, and produces a self-contained HTML page you read **like a
book** — one step per page, each page showing the real code involved, with the
relevant lines highlighted and an explanation that stays **hidden until you click
it**.

The hidden explanation is the point, not a flourish. Visible prose gets read
*instead of* code. Prose that costs a click inverts the order: you read the
highlighted lines, form your own understanding, then click to check yourself.
Every page is a small self-test. That is what makes this a tool against atrophy
rather than another summarizer.

## What it is not

- **Not a review tool.** `review-gate` and `interactive-pr-review` are adversarial
  and non-linear — you hunt for problems and produce output. This is linear and
  receptive: you produce nothing but understanding. Opposite layouts, opposite
  navigation. It shares low-level primitives with them (highlighter, colour
  tokens) and nothing else.
- **Not a summary.** If a reader can get the gist without reading code, it has
  failed.
- **Not tied to a diff.** It explains code as it *is*, whoever wrote it and
  whenever. Works equally on a feature built ten minutes ago and one built two
  years ago by someone else.

## Two entry points, one engine

| Entry | Source | Answers |
| --- | --- | --- |
| `/walkthrough <free text>` | existing code, traced live | "what is already in my codebase that I can't explain" |
| programmatic (later) | a recorded change set | "what was just built" |

The on-demand path is the one that matters first, and it is independently useful
without anything else installed.

---

## Reading model

- One **step** per page. No page scrolling — if a step does not fit, it is two
  steps. This constraint is load-bearing: it forces small steps and stops any
  page becoming a wall.
- A step contains **1..n excerpts**, possibly across different files. Most
  interesting steps cross a boundary ("the route calls this service method"), and
  showing both halves together is where flow becomes legible.
- Each excerpt shows the **full path**, **real file line numbers**, the
  **focus range** highlighted, and a few lines of context either side.
- Each excerpt carries a **bubble**: hidden by default, revealed on click.

```
┌──────────────────────────────────────────────────┐
│  Step 4 of 11        Where the token gets refreshed│
├──────────────────────────────────────────────────┤
│  src/auth/session.ts                        42–58 │
│    42   const stale = Date.now() > exp - SKEW;    │
│  ▸ 44   if (stale) await refresh(token);       ●  │
│  ▸ 45     queue.flush();                          │
│    47   return session;                           │
├──────────────────────────────────────────────────┤
│  src/auth/refresh.ts                        11–19 │
│  ▸ 13   const next = await api.rotate(token);  ●  │
├──────────────────────────────────────────────────┤
│  ◀ prev                    ●●●●○○○○○○○     next ▶ │
└──────────────────────────────────────────────────┘
```

---

## Data contract

Claude authors **`walkthrough.json`**. A script renders it. Claude specifies
*where*; the renderer reads *what* from disk.

This split is deliberate: Claude never transcribes source into JSON. Transcription
invites hallucinated code and burns tokens on something the filesystem already
has. Fidelity is guaranteed because the bytes come from the file.

```jsonc
{
  "id": "auth",
  "title": "Authentication",
  "subtitle": "How a request gets authenticated, from middleware to session refresh",
  "root": "/abs/path/to/repo",
  "commit": "abc1234",              // stamped at render time
  "generated_at": "2026-08-10T…",
  "steps": [
    {
      "title": "Where every request enters",   // orients — never explains
      "excerpts": [
        {
          "path": "src/middleware.ts",         // repo-relative
          "focus": [12, 18],                   // 1-indexed, real file lines
          "context": 5,                        // optional, default 5
          "bubble": "Runs before every route…" // hidden until clicked
        }
      ],
      "note": "optional step-level bubble"
    }
  ]
}
```

**Renderer validation** (fail loudly, these are the failure modes that matter):

- `path` exists, is text, is inside `root`
- `focus` within file bounds, `focus[0] <= focus[1]`
- rendered excerpt ≤ 40 lines → otherwise warn "split this step"
- `bubble` ≤ ~60 words → otherwise warn "the step is doing too much"
- every step has ≥ 1 excerpt → a step with no code is not a step

---

## Pipeline

1. **Resolve the target.** `"the auth of this application"` → candidate files and
   entry points, via Glob/Grep. Breadth-first, cheap.
2. **Trace the flow.** From the entry point, follow the call graph. This produces
   the step order.
3. **Propose an outline.** Step titles only, shown to the user before any
   expensive work. They can add ("you missed the OAuth path"), cut, or reorder.
   Skipped for walkthroughs under ~5 steps.
4. **Select excerpts.** Read files, pin exact line ranges.
5. **Write bubbles.**
6. **Render.** `render.py walkthrough.json` → self-contained HTML.

### Step order is execution order, not build order

The order code was *written* reproduces the author's wandering — model, route,
back to fix the model. The order it *runs* (entry → handler → service → storage)
is what "understanding the flow" means. Sequencing is legitimate machine work; the
understanding is what must stay human.

---

## Authoring rules

These keep it from degrading into a summarizer. They belong in the skill, with
reasons attached, because a model that understands *why* will apply them to cases
this list does not name.

1. **Titles orient, never explain.** "Where the session token gets refreshed" —
   not "Refreshes the token inside the skew window so queued writes don't 401."
   The second is the bubble's job, and putting it in the title leaks the answer
   and kills the click.
2. **Bubbles are written for someone who just read the code**, to confirm or
   correct a guess — not to brief someone cold. Lead with the non-obvious part.
   If a bubble would restate what the code plainly says, there is nothing to
   reveal and the step is wrong.
3. **Ask, where asking beats telling.** A bubble may end on a question that points
   back at the code ("what happens to the queued writes if this throws?").
4. **Never invent code.** Only real paths and ranges.
5. **Prefer more, smaller steps.** Ten legible pages beat four dense ones.
6. **Show the seams.** The valuable steps are boundary crossings, error paths and
   the decisions someone would get wrong — not the happy path in a straight line.

---

## Renderer

Self-contained HTML. No toolchain, no server, no dependencies — `open` it. (MDX
was considered and rejected: it is a source format needing a React runtime and a
bundler in every project, which cannot hold for arbitrary repos.)

**Layout:** fixed-height page, code in a scroll-free column, prev/next fixed at
the bottom.

**Interactions**

| Action | Binding |
| --- | --- |
| next / prev step | `→` `←`, buttons |
| reveal bubble | click the `●`, or `1`–`9` for the nth bubble |
| table of contents | `t` |
| copy file path | click the path header |
| jump to step | TOC entry |

**Persistence.** Last position per walkthrough id in `localStorage`, so you can
leave and come back.

**Bubbles reset on reload** rather than remembering what you revealed. A second
pass should re-test you; convenience would cost the whole mechanism. *(Decision —
see open questions.)*

**Theming.** Light and dark, following `prefers-color-scheme`.

**No horizontal page scroll.** Long lines scroll inside their own code block.

---

## Output location

`.walkthroughs/<id>.html` in the repo, with `.walkthroughs/` appended to
`.gitignore` on first run.

In-repo rather than a temp dir because these accumulate into something valuable:
a growing set of explanations of your own system, per project. An index page
listing them is the closest thing to the "map of what I actually understand" that
this whole marketplace is aiming at.

---

## Plugin layout

```
walkthrough/
├── .claude-plugin/plugin.json
├── README.md
├── SPEC.md
├── commands/
│   ├── walkthrough.md          # /walkthrough <target>
│   └── list.md                 # /walkthrough:list
├── skills/
│   └── authoring-walkthroughs/
│       └── SKILL.md            # tracing flow, ordering steps, writing bubbles
├── scripts/
│   ├── render.py               # walkthrough.json + files -> self-contained HTML
│   └── validate.py             # contract checks, run before render
└── assets/
    ├── template.html
    ├── ui.css
    ├── ui.js
    └── vendor/highlight.min.js
```

---

## Milestones

- **0.1.0** — JSON contract, validator, renderer, `/walkthrough` with outline
  confirmation. Single walkthrough, keyboard nav, hidden bubbles.
- **0.2.0** — TOC, position persistence, theming, `/walkthrough:list` and index.
- **0.3.0** — staleness detection (hash files at render; flag when a walkthrough
  no longer matches the tree) and a `refresh` path.
- **Later** — programmatic entry from a recorded change set; completion code the
  page emits on reaching the last step.

---

## Open questions

1. **Bubble reveal state on revisit** — reset (recommended: preserves the
   self-test) or remember?
2. **Output location** — in-repo `.walkthroughs/` (recommended: they compound)
   or a scratch dir like `interactive-pr-review` uses?
3. **Outline confirmation** — always, or only above ~5 steps (recommended)?
4. **Non-code targets** — should `/walkthrough` accept config, SQL, infra and
   docs as first-class? (Recommended: yes; the contract is already file-and-line
   based and nothing about it is language-specific.)
5. **Staleness** — warn on open when the underlying files have moved on, or
   silently re-render?

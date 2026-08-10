# walkthrough

```
/walkthrough the auth of this application
```

Claude traces how that feature actually works and produces a self-contained HTML
page you read **like a book**: one step per page, the real code involved, the
relevant lines highlighted — and the explanation for each **hidden until you
click it**.

## Why the explanation is hidden

Because visible prose gets read *instead of* the code.

An explanation that costs a click inverts the order. You read the highlighted
lines, form your own understanding, and only then check yourself. Every page
becomes a small self-test, which is the difference between finishing with a
mental model and finishing with the feeling of one.

It is the same reason the rest of this marketplace exists: the human produces
first, the machine confirms second.

## What a page looks like

```
┌──────────────────────────────────────────────────┐
│  STEP 4 OF 11                                     │
│  Where the token gets refreshed                   │
├──────────────────────────────────────────────────┤
│  src/auth/session.ts                      42–91   │
│    42   const stale = Date.now() > exp - SKEW;    │
│  ▸ 44   if (stale) await refresh(token);          │
│  ▸ 45     queue.flush();                          │
│  ① What does this do?                             │
│    47   return session;                           │
│         ⋯ 31 lines hidden                         │
│    86   async function drain(queue) {             │
│  ▸ 88     for (const job of queue) await job();   │
│  ② What does this do?                             │
├──────────────────────────────────────────────────┤
│  ◀ prev              ●●●●○○○○○○○           next ▶ │
└──────────────────────────────────────────────────┘
```

Highlighted lines are the step; the dimmed lines around them are context. A step
can highlight several places at once — the interesting moments are usually the
ones that cross a boundary.

When two of them are in the **same file**, you get one continuous file view with
both highlighted and the stretch between them collapsed to a seam you can open.
Never the same file twice on a page.

| Key | Does |
| --- | --- |
| `→` `←` | next / previous step |
| `1`–`9` | reveal the nth explanation |
| `t` | contents |
| click path | copy it |

Your position is remembered. **Revealed explanations are not** — a second pass
should test you again.

## Commands

| Command | Does |
| --- | --- |
| `/walkthrough <target>` | Build one. `/walkthrough the auth of this application` |
| `/walkthrough:list` | What exists, and whether the code it explains has changed |
| `/walkthrough:refresh <id>` | Rebuild one, re-tracing it if the code moved on |

## Install

```
/plugin marketplace add ramiejleh/DefyAtrophy
/plugin install walkthrough@DefyAtrophy
/reload-plugins
```

Needs `python3`. No Node, no build step, no server — the output is one HTML file
you open.

## It works on any code

Not just code Claude wrote, and not tied to a diff. It explains code as it *is* —
a feature built ten minutes ago, or one built two years ago by someone who has
since left. That is the point: the hardest thing to explain in your codebase is
usually the part you never wrote.

Non-code targets work too. Nothing in the format is language-specific, so SQL,
infrastructure, config and docs are all fair game.

## Output

```
.walkthroughs/
├── index.html      # browsable list of everything, with freshness
├── index.json      # metadata + the file hashes staleness is measured against
├── auth.html       # the walkthrough
└── auth.json       # its source, so it can be edited and rebuilt
```

Gitignored on first run. They accumulate, which is deliberate — a growing set of
explanations of your own system is worth more than any single one.

## Staleness

Every render records a content hash of each file it cites. `/walkthrough:list`
re-hashes them and flags any walkthrough whose code has moved on.

This matters more than it sounds. A walkthrough pointing at the wrong lines is
worse than no walkthrough, because the reader learns a wrong map and has no
reason to doubt it. So a stale one wants **regenerating**, not re-rendering —
re-rendering just draws the old line numbers against new code.

The page itself cannot tell you this. It is a `file://` document with no network
and no filesystem access, so freshness is answered by the scripts, not the
reader.

## How it is built

Claude authors a small JSON file: which files, which line ranges, what each
explanation says. `render.py` reads the **actual code from disk** and inlines
everything — CSS, JS, the syntax highlighter — into one file.

That split matters. Claude never transcribes source, because transcribed code
drifts from the file or gets invented, and the filesystem already holds the
truth. What you read is what is there.

```
python3 scripts/validate.py walkthrough.json   # contract check
python3 scripts/render.py   walkthrough.json   # -> .walkthroughs/<id>.html
```

The validator refuses to render on broken line ranges or missing files, and warns
when a walkthrough drifts off-design — a title that explains instead of pointing,
an excerpt too long to fit a page, an explanation carrying a whole step on its
own.

## License

MIT © Rami Ejleh

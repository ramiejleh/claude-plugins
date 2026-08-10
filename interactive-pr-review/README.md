# interactive-pr-review

Interactively review GitHub pull requests with Claude Code. Reference a PR by number and
Claude will fetch it, reason about the diff, and build a local review UI that walks you
through the **exact GitHub diff** — grouped into logical chunks, with reasoning for each
group *and* each file — so long PRs become easy to review and understand. The diff is
syntax-highlighted like an IDE, each file gets a rich header and an AI description of
what changed in it, and you can comment at the line, file, or whole-review level. Paste
the comments back and Claude posts them to the PR at the exact lines you chose.

The diff shown is always byte-for-byte what GitHub returns. Claude only groups, orders,
highlights, and annotates it — it never rewrites the code under review.

## Highlights

- **PR overview** — a section at the top gives a concise, holistic summary of what the PR
  achieves in plain terms, so you get the big picture before reading a single diff.
- **Sidebar layout** — a sticky sidebar puts the review summary and export button right
  under the PR title, then the diff-view selector, compact show/hide toggle chips, and the
  file + group navigators. Collapse it to a narrow strip to give the diff the full width.
- **IDE-style syntax highlighting** — diffs are colored with a vendored copy of
  highlight.js (GitHub light/dark themes), so the output is fully self-contained and
  needs no network at view time.
- **Per-file context** — every file shows a header (status, path, rename arrow, language,
  +/− counts) and a neutral, one-to-two sentence description of what changed (purely
  descriptive — it never judges the code).
- **"Things worth confirming"** — each group lists a few concrete things to focus on, so
  evaluative guidance is separated from the neutral descriptions. Toggle them off from the
  sidebar (like the insight annotations) when you want just the diff.
- **Per-group file manifest** — each group opens with a two-column table (file, role)
  linking down to each file's diff, so you can trace how the files work together before
  diving in — aligned columns regardless of how long any path or role is. Toggle it off from
  the sidebar for a leaner page.
- **Two diff views** — switch between **Unified** (inline) and **Split** (old / new side
  by side) from the sidebar.
- **Expandable context** — "⋯ expand" affordances around each hunk reveal the surrounding
  real file lines on demand (a chunk at a time, or all), so you can trace how a change
  sits in the file without leaving the diff. Expanded lines are commentable too.
- **Inline insight annotations** — read-only 💡 notes aimed at the **change**, not a
  narration of every block: *what moved and its consequence*; *who calls it* (real callers
  looked up in the repo, so you see the blast radius); *why it exists* (from the PR
  description); and a plain what-it-does only where the code isn't self-evident. Each sits
  above its block with a left rail and line label; **notable** insights render emphasized so
  the ones you shouldn't skim past stand out from **routine** context. Toggle them off for a
  bare diff — they stay descriptive and are never part of your review.
- **Three comment levels** — click a line, comment on a whole file, or write an overall
  review summary. **One click** copies all of them (summary + line + file comments)
  together as JSON.
- **Comment counters** — a `💬 N` badge on each group header and each file header shows how
  many comments you have left there, so you can see at a glance where you stopped. The badge
  stays hidden where you haven't written anything.
- **Written for a global audience** — the overview, group and file descriptions, "things worth
  confirming", and insight notes are all written in plain, direct English aimed at non-native
  speakers: short sentences and common words, with no idioms or unexplained shorthand. The
  technical substance is unchanged; only the wording is made easier to read.
- **Multi-line comments** — drag across a run of lines to pin one comment to that whole
  range instead of a single line. The span tints as you drag, and a saved range keeps an
  accent rail down the lines it covers and into the comment box's own border, so the range
  and its comment read as one block. It posts to GitHub as a real multi-line review comment.
- **Your draft survives a reload** — comments, the summary, and reviewed/folded marks are
  saved locally, keyed to the PR *and* its head commit, so closing the tab doesn't lose your
  work and a new push starts you on a clean draft instead of comments written against older
  code. Drafts stay local; nothing reaches GitHub until you export and confirm.
- **Comments only** — this tool exists to leave review comments. It never approves or
  requests changes.
- **Deterministic UI** — the HTML structure is fixed; only the injected data differs, so
  every review page looks and behaves the same.
- **Files spanning concerns appear in each relevant group, with their full diff every
  time** — a helper touched by several concerns is shown complete in each group it belongs
  to (never chunked away), and a focus note names which lines are that group's concern while
  the rest of the file's diff stays visible for context.

## Requirements

- [GitHub CLI (`gh`)](https://cli.github.com) installed and authenticated
  (`gh auth login`). The plugin checks this on session start and warns you if it's
  missing.
- A web browser (the review UI is a local HTML file).

## Installation

Add the marketplace once, then install the plugin from it:

```
/plugin marketplace add ramiejleh/DefyAtrophy
/plugin install interactive-pr-review@DefyAtrophy
```

To pick up a newer version after one is published:

```
/plugin marketplace update DefyAtrophy                 # refresh the marketplace listing
/plugin install interactive-pr-review@DefyAtrophy      # reinstall at the new version
```

> You **add** by `owner/repo` and **install/update** by the marketplace's registered
> *name* — the `name` field in `marketplace.json`. Here both are `DefyAtrophy`, so the
> commands read the same either way.

### From a local checkout (development)

Plugins always install **from a marketplace**, so point Claude Code at the repo root (the
directory holding `.claude-plugin/marketplace.json`, not the plugin subdirectory) and then
install by name — `/plugin install ./interactive-pr-review` does not work, because `install`
takes a name and only `marketplace add` takes a path:

```
/plugin marketplace add /absolute/path/to/this/repo
/plugin install interactive-pr-review@DefyAtrophy
```

A local marketplace whose `name` matches one you already have replaces that entry, so the
checkout takes over; re-add `ramiejleh/DefyAtrophy` to switch back. After editing
`commands/` or `hooks/`, run `/reload-plugins` to pick the change up — skill files are re-read
live.

## Usage

Run the command with a PR number:

```
/interactive-pr-review:review 128
```

If you're not inside the target repository, add an `owner/repo` slug:

```
/interactive-pr-review:review 128 ramiejleh/some-repo
```

**List** the PRs you've already analyzed and can reopen from cache, each flagged **fresh**
or **STALE** by checking its head commit against GitHub:

```
/interactive-pr-review:list
```

**Reopen** a PR you already analyzed — instant, from cache, no re-analysis (unless the PR
moved):

```
/interactive-pr-review:reopen 128
```

**Clean up** cached artifacts when you're done — one PR, or all of them:

```
/interactive-pr-review:cleanup 128        # just PR 128
/interactive-pr-review:cleanup            # every cached PR
```

### What happens

1. **Fetch** — Claude pulls the PR metadata, file list, unified diff, and head commit
   SHA via `gh`.
2. **Group** — the diff is split into logical chunks (feature, tests, config,
   incidental changes…), each with a neutral reasoning line, a "Things worth confirming"
   list, per-file descriptions, and inline insight bubbles, plus a holistic **overview** of
   what the whole PR achieves. Claude writes this analysis layer to files in `.reviews/` — no code, only
   references to the parsed hunks — keeping your main context clean.
3. **Review** — Claude generates and opens a self-contained HTML UI at
   `.reviews/pr-<number>-review.html`: a collapsible sidebar (summary, one-click export, diff
   view, show/hide toggles, file + group nav) beside collapsible groups, each file
   syntax-highlighted with a rich header,
   description, and 💡 insight bubbles. Comment at the line, file, or whole-review level.
4. **Export** — click **Copy all comments as JSON** (one click captures the summary and
   every line and file comment together) and paste the result back into the chat.
5. **Post** — Claude validates your comments, shows you a summary, and — after you
   confirm — posts them to GitHub as a single **comment** review, anchored to the exact
   lines and the PR's head commit. It never approves or requests changes.
6. **Keep or clean** — artifacts land in a `.reviews/` directory **inside the project**
   (gitignored on first run), so a review survives a reboot and sits next to the code it
   belongs to. They are **kept**, not auto-deleted, so you can
   `reopen` the PR later without re-fetching or re-analyzing. Reopen is **fresh-only**: if
   the PR's head commit has moved since it was analyzed, it re-runs the analysis instead of
   showing a stale diff. Remove artifacts when you're done with `cleanup`.

Nothing is ever posted to GitHub without your explicit confirmation.

## Components

| Type | Name | Purpose |
| --- | --- | --- |
| Command | `/interactive-pr-review:review <pr#> [owner/repo]` | The entry point that runs the full review workflow. |
| Command | `/interactive-pr-review:reopen <pr#> [owner/repo]` | Reopens a previously analyzed PR from cached artifacts. Fresh-only: rebuilds the UI instantly if the PR is unchanged, otherwise re-analyzes. |
| Command | `/interactive-pr-review:list` | Lists the PRs with cached artifacts (number, title, size, age) and checks each one's freshness against GitHub (fresh = head commit unchanged since analysis; any new commit / force-push / rebase marks it STALE). |
| Command | `/interactive-pr-review:cleanup [pr#]` | Removes cached artifacts — one PR's, or all (`.reviews/pr-*`). Lists and confirms before deleting. |
| Skill | `pr-review-ui` | Procedures for the parse → analyze → merge pipeline, building the review UI, and posting comments. The whole workflow runs in the main chat — no subagent. |
| Scripts | `parse_diff.py`, `assemble_analysis.py`, `merge_analysis.py` | Deterministic diff parsing, fragment assembly, and analysis-merge. The diff never passes through the model. |
| Hook | `SessionStart` gh check | Warns (non-blocking) if `gh` is missing or unauthenticated. |

### Architecture: the diff never passes through the model

`parse_diff.py` parses the diff into byte-exact hunks with stable `hunkId`s. Claude emits
**only the analysis** — an overview, titles, neutral descriptions, "things worth confirming",
insights, and which `hunkId`s are each group's concern — written as small per-group fragments
so one dropped write costs a fragment rather than the whole analysis. `merge_analysis.py`
joins the two, embeds full file contents, and enforces the invariants: every referenced hunk
must exist, and **every changed file is always shown** — anything the analysis didn't place is
swept into an "Other changes" group, and the merge fails rather than hide a file (a file
spanning groups carries its full diff in each).

Because the model never emits a line of code, it cannot alter or drop one — "the diff is
sacred" is structural, not a rule the model is asked to follow.

## How comments are anchored

GitHub anchors review comments to a line on a side of the diff:

- Added / context lines → `side: RIGHT` with the **new** file line number.
- Removed lines → `side: LEFT` with the **old** file line number.

The UI computes these from each hunk header, so comments land on exactly the line you
clicked. Only lines present in the diff can be commented on.

## Development

The plugin structure:

```
interactive-pr-review/
├── .claude-plugin/plugin.json     # manifest
├── commands/review.md             # /interactive-pr-review:review
├── commands/reopen.md             # /interactive-pr-review:reopen  (from cache, fresh-only)
├── commands/list.md               # /interactive-pr-review:list    (cached PRs + freshness)
├── commands/cleanup.md            # /interactive-pr-review:cleanup (remove artifacts)
├── skills/pr-review-ui/
│   ├── SKILL.md                   # parse / analyze / merge / UI / post procedures
│   ├── scripts/
│   │   ├── parse_diff.py          # diff → canonical hunks (deterministic)
│   │   ├── assemble_analysis.py   # per-group fragments → one analysis JSON
│   │   └── merge_analysis.py      # analysis + hunks → final groups JSON
│   └── assets/
│       ├── review-template.html   # UI skeleton (markup + injection tokens)
│       ├── ui.css                 # stylesheet, inlined at build time
│       ├── ui.js                  # behaviour, inlined at build time
│       └── vendor/                # highlight.js + GitHub theme (no network at view time)
├── hooks/
│   ├── hooks.json                 # SessionStart hook registration
│   └── check-gh-auth.sh           # gh install + auth check
└── README.md
```

This repo is a Claude Code plugin **marketplace**; see the [repo root README](../README.md)
for the marketplace-level view.

## License

MIT © Rami Ejleh

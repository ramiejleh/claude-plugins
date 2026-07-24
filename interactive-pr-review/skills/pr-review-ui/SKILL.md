---
name: pr-review-ui
description: Procedures for interactive GitHub PR review — fetch PR data with the gh CLI, group a unified diff into logical chunks (with group- and file-level AI reasoning plus inline read-only insight bubbles explaining the code) authored directly in the main chat and written to a temp JSON file, inject that data plus vendored highlight.js into a self-contained HTML review UI with a sidebar and per-file headers, collect line- and file-level comments, and post them back to GitHub as a pull request review. Use when reviewing, walking through, or commenting on a GitHub pull request by number.
user-invocable: false
---

# PR Review UI

This skill turns a GitHub pull request into a guided, chunk-by-chunk review experience
and posts the reviewer's comments back to GitHub. It is the engine behind the
`/interactive-pr-review:review` command, but can be used directly any time the user
wants to review a PR carefully.

## Non-negotiables

1. **The diff is sacred.** Everything shown to the reviewer must be exactly what GitHub
   returns — same paths, same hunk headers, same whitespace, same line content. You may
   reorder and group hunks, syntax-highlight them for display, and add commentary
   *around* them, but never edit the code inside a hunk.
2. **No surprise writes to GitHub.** Fetching is read-only and always fine. Posting
   comments requires explicit user confirmation of the exact comment set first.
3. **Anchor to the head commit.** Review comments must reference the PR's latest head
   SHA, or they will fail to attach or attach to the wrong revision.
4. **The grouping JSON lives in a temp file, by design.** It is far too large to pass
   through the conversation. You author the analysis directly (in the main chat — no
   subagent) and write it to a temp file, then inject it into the HTML with a script.
   Neither the big JSON nor the big HTML should be pasted into the main context. The temp
   files are kept afterward (so a review can be reopened) and removed only via the manual
   `cleanup` command — never auto-deleted at the end of a review.

## 1. Fetch PR data (read-only)

Prefer the `gh` CLI. When the user gave an `owner/repo`, pass `--repo <slug>` to every
command; otherwise `gh` uses the current directory's remote.

Build the repo flag as a **bash array** (`REPO_ARGS`), not an unquoted string. zsh (the
default macOS shell) does not word-split an unquoted `$REPO_FLAG`, so `--repo owner/name`
would reach `gh` as one glued argument and every call would fail. `"${REPO_ARGS[@]}"`
expands to zero or two words correctly in both bash and zsh.

```bash
PR=<number>                       # e.g. 128
REPO_ARGS=()                      # or: REPO_ARGS=(--repo owner/name)

# Metadata
gh pr view "$PR" "${REPO_ARGS[@]}" --json number,title,author,baseRefName,headRefName,body,url,additions,deletions,changedFiles,state,isDraft,mergeable

# Changed files with stats and status (added/modified/removed/renamed)
gh pr view "$PR" "${REPO_ARGS[@]}" --json files

# The unified diff — save it, do not paste the whole thing around
gh pr diff "$PR" "${REPO_ARGS[@]}" > /tmp/pr-$PR.diff

# Head commit SHA — required to anchor review comments
HEAD_SHA=$(gh pr view "$PR" "${REPO_ARGS[@]}" --json commits --jq '.commits[-1].oid')

# Resolve owner/repo for later gh api calls
gh pr view "$PR" "${REPO_ARGS[@]}" --json url --jq '.url'   # .../{owner}/{repo}/pull/{n}
```

If `gh` is unavailable, fall back to the REST API with `curl` and a
`GITHUB_TOKEN`/`GH_TOKEN`, requesting `Accept: application/vnd.github.v3.diff` for the
diff. Always prefer `gh`.

## 2. Parse → analyze → merge (the pipeline)

The grouping JSON is built by three stages. **The diff never passes through the language
model as output** — deterministic scripts own it; you (in the main chat) produce only the
analysis layer, which is small. This makes the "diff is sacred" rule structural (you can't
alter a line you never emit) and eliminates the huge-response failures that plagued the
earlier one-shot approach. The scripts and assets live under the plugin root at
`skills/pr-review-ui/{scripts,assets}/` — see **Resolving the plugin root** below for how to
locate it reliably from a shell.

**Run every stage in the main conversation — do not spawn a subagent.** Stages A and C are
plain script calls. Stage B is authored by you directly: you read the parsed diff and write
a small analysis-only JSON.

### Resolving the plugin root

`$CLAUDE_PLUGIN_ROOT` is only exported while one of *this plugin's own commands* is
executing. When you run these steps as ad-hoc Bash (e.g. from `reopen`, or when driving the
pipeline directly), that variable is **empty**, and a path like
`$CLAUDE_PLUGIN_ROOT/skills/...` resolves to `/skills/...` and fails. Also, the Bash tool
does not persist shell state between calls, so resolve the root **inside each bash block**
that needs it. Use this snippet — it prefers the env var when set and otherwise discovers
the newest installed copy in the plugin cache:

```bash
PLUGIN_ROOT="$CLAUDE_PLUGIN_ROOT"
if [ -z "$PLUGIN_ROOT" ] || [ ! -e "$PLUGIN_ROOT/skills/pr-review-ui/SKILL.md" ]; then
  PLUGIN_ROOT=$(ls -d "$HOME"/.claude/plugins/cache/*/interactive-pr-review/*/ 2>/dev/null | sort -V | tail -1)
fi
```

**Stage A — parse (deterministic, no LLM).** Turn the diff into a canonical structure with
stable `hunkId`s (`<path>#<index>`):

```bash
PR=<number>
PLUGIN_ROOT="$CLAUDE_PLUGIN_ROOT"
if [ -z "$PLUGIN_ROOT" ] || [ ! -e "$PLUGIN_ROOT/skills/pr-review-ui/SKILL.md" ]; then
  PLUGIN_ROOT=$(ls -d "$HOME"/.claude/plugins/cache/*/interactive-pr-review/*/ 2>/dev/null | sort -V | tail -1)
fi
SCRIPTS="$PLUGIN_ROOT/skills/pr-review-ui/scripts"
# Write PR metadata (from step 1) to /tmp/pr-$PR-meta.json first, e.g. with gh --json.
python3 "$SCRIPTS/parse_diff.py" /tmp/pr-$PR.diff /tmp/pr-$PR-parsed.json --pr-json /tmp/pr-$PR-meta.json
# -> OK parsed files: N hunks: M lines: L -> /tmp/pr-$PR-parsed.json
```

`parsed.json` = `{ pr, files: [ { path, previousPath, status, language, additions,
deletions, hunks: [ { hunkId, header, oldStart, newStart, lines: [ {type, oldLine,
newLine, text} ] } ] } ] }`. This is the byte-exact source of truth for the code.

**Stage B — analyze (you, in the main chat — no subagent).** Read `/tmp/pr-<number>-parsed.json`
(and the raw `.diff` for extra context if useful) and author the **analysis only** — groups,
titles, neutral `reasoning`, `thingsToConfirm`, per-file `role`/`description`/`insights`, and
the `hunkIds` each group's file includes. Emit **no code**: reference hunks by their
`hunkId`, never reproduce their lines. Write the result to `/tmp/pr-<number>-analysis.json`
with a heredoc using a **quoted delimiter** (`<<'JSON'`), then validate it (see the
validation snippet below). Because this payload is titles + prose + ids + line numbers, it
is a small fraction of the diff's size — write it in a single heredoc; there is no need to
chunk it, and there is no subagent round-trip.

How to author each field:

- **Group logically.** Bundle changes a reviewer should consider together — by
  feature/concern (across files), pairing implementation with its tests, separating core
  changes from incidental ones (config, lockfiles, generated, vendored). Order groups
  most-important-first; incidental last.
- **Assign hunks to groups by hunk.** Each group's file entry lists the `hunkIds` it
  includes. If a file's hunks belong to different concerns, list that file in EACH relevant
  group with only that group's `hunkIds`. **Every `hunkId` from `parsed.json` should be
  assigned to exactly one group** — none dropped, none duplicated. Still, aim for full,
  deliberate coverage: anything you don't place is a file the reviewer sees under a generic
  "Other changes" heading instead of in a meaningful group. (The merge step **guarantees**
  nothing is hidden — see below — but a well-grouped review places every file on purpose.)
- **`reasoning`** (per group, 1–3 sentences): purely descriptive and neutral — what this
  group does and how the pieces relate. No evaluation, no "you should check", no
  "risk/concern/looks good".
- **`thingsToConfirm`** (per group): 2–5 short, concrete items to focus on. THIS is the only
  place evaluative "worth checking" guidance belongs (e.g. "That the limiter runs before
  authentication."). A trivial group may use a short list or `[]`.
- **`role`** (per file, ≤ ~8 words): the file's responsibility in this group (e.g. "HTTP
  entry point", "unit tests"). Keep roles distinct across a group's files where possible.
- **`description`** (per file, 1–2 sentences): neutral, descriptive — what changed in this
  file for this group's hunks. Describe, don't evaluate.
- **`insights`** (per file): descriptive subtitles for EVERY logical block — see the detailed
  rules just below.

**Authoring `insights` — tell the reviewer what they can't already see.** Insights are
read-only annotations layered over a block of code. The reviewer can *read the code*; a
bubble that just restates the signature ("`getToken(req)`: reads the token from the request")
is wasted ink. A good insight answers the questions a reviewer actually has about a **change**:
what moved, why, what it touches elsewhere, and what to look at that isn't visible in this
one block. Aim every insight at one of these, in rough priority order:

1. **The delta (for a modified block): lead with what CHANGED and its consequence**, not a
   fresh description of the whole block. The parser marks each line `add`/`del`/`context`, so
   you know what actually moved. Contrast the before/after and name the effect.
   - Weak (restates): "`retryWithBackoff(fn, opts)`: runs `fn`, retrying with exponential backoff."
   - Strong (delta): "Adds a `maxDelay` cap (backoff was previously unbounded); callers on the long-poll path now top out at 30s instead of growing indefinitely."
   For a genuinely **new** block, a concise what-it-is/does is fine — but still favor *why it
   was added* over mechanics.

2. **Blast radius: name real callers / consumers by looking them up.** You are authoring in
   the main chat with repo access — don't guess who uses a thing, `grep` for it and say so
   concretely. This is the single most reviewer-useful move.
   - Weak (guessed): "`RetrievalSource`: the shape of a citation surfaced to the UI."
   - Strong (resolved): "`RetrievalSource` — consumed by `CitationList.tsx` and the `/copilot/retrieve` handler; the new required `score` field is a breaking change for both call sites."
   Use `grep`/`rg` against the repo for exported names, changed signatures, renamed symbols.
   When a change is local and has no external callers, say that too ("no callers outside this
   file") — it's reassuring signal.

3. **The why, from PR context.** The PR title/body and commit messages carry intent the code
   can't. Thread it in where it explains a block's existence.
   - "New `dedupeByUri` step — added to fix the duplicate-citation bug described in the PR body."

4. **What it is / does — only when non-obvious.** Plain descriptions are the *fallback*, not
   the default. Use them for genuinely intricate logic (subtle async ordering, a non-obvious
   invariant, a tricky reducer), and keep them to the part that isn't self-evident.

**Coverage — quality over density. Annotate where you add information beyond the code.**
This is the opposite of "one bubble per block regardless." A self-evident DTO, a trivial
getter, a plain re-export, a routine import group — leave them bare. Spend insights on: every
**changed** block (delta), anything with **external callers** (blast radius), and any block
whose behavior is **non-obvious**. Fewer, denser, higher-signal bubbles read far better than
uniform wallpaper the reviewer learns to ignore. Config/lockfile/generated files usually get
none. Insights are toggleable in the UI, but that is not a licence to pad.

Each insight has:
- `side` — `RIGHT` for added/context lines (the common case); `LEFT` only when describing
  removed code.
- `startLine` / `endLine` — the inclusive line range of the whole block on that side, using
  the numbers in `parsed.json`. **`startLine` must be exact** (the block's opening
  signature/declaration line). For `endLine`, cover the block to its closing line; if unsure
  of the exact close, under-estimate — the merge step **auto-snaps `endLine` forward** to the
  true closing brace via brace + indentation analysis, so a slightly short `endLine` is fine
  but a wrong `startLine` is not. Single-line blocks use equal values.
- `kind` — a short lowercase label: `function` | `method` | `hook` | `interface` | `type` |
  `enum` | `class` | `const` | `config` | `component` | `jsx` | `logic` | `guard` | `loop` |
  `import` | `export` | `schema` | `test` | `effect`.
- `level` — attention weight, one of `notable` or `routine` (default `routine` if omitted):
  - `notable` — the reviewer should not skim past this: a behavior change with a
    consequence, a breaking change for named callers, a subtle invariant, a why-it-exists
    that reframes the block. The UI renders these emphasized. Use sparingly — a file where
    everything is notable has nothing notable.
  - `routine` — useful context that can be scanned quickly (a straightforward new helper, a
    small local delta with no external reach). The UI renders these dim/compact.
- `text` — one to three sentences. Still **descriptive, not evaluative**: state what changed
  / what it touches / why, but do not judge, praise, or say "you should fix". Evaluative
  "worth checking" guidance stays in the group's `thingsToConfirm`. (An insight can say "the
  new required field breaks `CitationList.tsx`" — a fact; it should not say "this will break
  things, reconsider" — a judgement.)

Analysis schema (what you write to the analysis file):

```json
{
  "groups": [
    {
      "id": "g1",
      "title": "Rate-limit middleware",
      "reasoning": "Adds a token-bucket limiter and applies it in the request pipeline.",
      "thingsToConfirm": [
        "That the limiter runs before authentication, not after.",
        "That the token bucket is shared across requests rather than recreated per call."
      ],
      "files": [
        {
          "path": "src/middleware/rateLimit.ts",
          "role": "token-bucket implementation",
          "description": "New module exporting a `rateLimit` middleware factory.",
          "hunkIds": ["src/middleware/rateLimit.ts#0"],
          "insights": [
            { "side": "RIGHT", "startLine": 1, "endLine": 6, "kind": "function", "level": "notable",
              "text": "New `rateLimit` factory — wired into the pipeline in `app.ts:42`, ahead of `authenticate`. The bucket is created once at factory time and shared across requests, so limits are global, not per-connection." },
            { "side": "RIGHT", "startLine": 8, "endLine": 10, "kind": "const", "level": "routine",
              "text": "Default bucket size / refill rate; only referenced by this factory." }
          ]
        }
      ]
    }
  ]
}
```

**Stage C — merge (deterministic, no LLM).** Join the analysis onto the parsed hunks,
optionally embedding full file contents, producing the final UI groups JSON. This
**enforces coverage as a hard guarantee, not a warning**: it errors if any referenced
`hunkId` is unknown; it sweeps any hunk the analysis left unassigned — and any file with no
hunks at all (binary, pure rename, mode change) that no group surfaced — into a synthetic
**"Other changes"** group in diff order; and it then `die()`s if, after that sweep, any hunk
or file is *still* not shown. So **every changed file always appears in the UI**, regardless
of how the analysis grouped them — the model cannot hide a file by omission.

```bash
python3 "$SCRIPTS/merge_analysis.py" /tmp/pr-$PR-parsed.json /tmp/pr-$PR-analysis.json \
  /tmp/pr-$PR-groups.json --repo owner/name --sha <headSha>
# -> OK groups: G files: F hunks: H insights: I [| swept N unassigned file(s) into 'Other changes'] -> …
```

Passing `--repo`/`--sha` makes the merge fetch each text file's content at the head SHA
via `gh api` and set `fullContent` (powering "⋯ expand context"); omit them to skip that.
If the output notes it `swept N unassigned file(s) into 'Other changes'`, those files were
covered but not thematically grouped — usually worth revising the analysis to place them in
a meaningful group, though the review is still complete either way.

### Validate the analysis before merging

After writing the analysis file, confirm it parses and every `hunkId` exists (cross-checking
against `parsed.json`):

```bash
python3 - /tmp/pr-$PR-analysis.json /tmp/pr-$PR-parsed.json <<'PY'
import json, sys
a = json.load(open(sys.argv[1])); parsed = json.load(open(sys.argv[2]))
valid = {h["hunkId"] for f in parsed["files"] for h in f["hunks"]}
ref = [hid for g in a["groups"] for f in g["files"] for hid in f.get("hunkIds", [])]
missing = sorted(set(ref) - valid)
dropped = sorted(valid - set(ref))
print("OK groups:", len(a["groups"]),
      "files:", sum(len(g["files"]) for g in a["groups"]),
      "hunkRefs:", len(ref),
      "insights:", sum(len(f.get("insights", [])) for g in a["groups"] for f in g["files"]))
if missing: print("ERROR unknown hunkIds:", missing[:10])
if dropped: print("WARNING unassigned hunks:", len(dropped), dropped[:10])
PY
```

Fix any `ERROR unknown hunkIds` before merging. Aim for zero `WARNING unassigned hunks` too
— not because they'd be lost (the merge sweeps them into "Other changes"), but because a
deliberately grouped file reviews better than one dumped in the catch-all.

The analysis is small (titles + prose + ids + line numbers), so authoring it inline is
reliable regardless of PR size. There is no subagent step.

### Final groups JSON (merge output — what the UI consumes)

`files[]` is nested inside each group; each file carries header fields, `role`,
`description`, `insights`, optional `fullContent`, and full byte-exact `hunks` (from the
parser). **A file spanning concerns appears in multiple groups**, each with only that
group's hunks. Shape:

```json
{
  "pr": { "number": 128, "title": "…", "author": "…", "base": "main", "head": "feature/x",
          "url": "…", "additions": 210, "deletions": 34, "changedFiles": 7, "headSha": "…" },
  "groups": [ { "id": "g1", "title": "…", "reasoning": "…", "thingsToConfirm": ["…"],
    "files": [ { "path": "…", "previousPath": null, "status": "added", "language": "typescript",
      "additions": 40, "deletions": 0, "role": "…", "description": "…",
      "insights": [ { "side": "RIGHT", "startLine": 1, "endLine": 6, "kind": "function", "text": "…" } ],
      "fullContent": "…optional…",
      "hunks": [ { "header": "@@ …", "oldStart": 0, "newStart": 1,
        "lines": [ { "type": "add", "oldLine": null, "newLine": 1, "text": "…" } ] } ] } ] } ]
}
```

`type` is `add` | `del` | `context`. `text` is the line without its `+`/`-`/space marker.
`status` is `added` | `modified` | `removed` | `renamed` | `binary`. `language` is a
highlight.js name or null.

`reasoning` (per group) and `description` (per file) are **purely descriptive and
neutral** — no evaluation. All "focus here" guidance lives in `thingsToConfirm` (per
group), rendered as a "Things worth confirming" section under the reasoning.

`role` (per file) is a short phrase naming the file's responsibility in the group; the UI
renders a per-group **file manifest** linking each file to its diff. Falls back to
`description` if absent.

`insights[]` are **read-only annotations that tell the reviewer what the code doesn't** —
for a changed block, what moved and its consequence; who calls it (blast radius); why it
exists (PR intent); and, only where non-obvious, what it does. They are authored for the
*change*, not as a narration of every block (see Stage B for the full authoring rules). Each
has `side` (`RIGHT`/`LEFT`), a line **range** `startLine`/`endLine` spanning the whole block,
a lowercase `kind` (the block type, e.g. `function`/`interface`/`const`/`component`/`test`),
a `level` (`notable` or `routine`, default `routine`), and descriptive `text`. The UI marks
the block with a left rail and renders the 💡 subtitle **above the first line**, labelled
with the covered lines (e.g. "Lines 12–20"); `notable` insights render emphasized and
`routine` ones dim/compact, so the important ones stand out in a long file. A sidebar toggle
hides them all for a bare diff. Insights stay descriptive (facts, not judgements — evaluation
lives in `thingsToConfirm`) and are never part of the exported review. (Older data with a
single `line`, or with no `level`, still works — a missing `level` renders as `routine`.)

`fullContent` (optional per file) is the file's complete text at the head SHA, added by
the merge step's `--repo/--sha` fetch. When present the UI adds **"⋯ expand context"**
affordances around each hunk to reveal surrounding real file lines inline.

### Diff / anchoring mechanics

Track `oldLine` from `oldStart` and `newLine` from `newStart` per hunk: context lines
advance both; `del` advances only `oldLine`; `add` advances only `newLine`. GitHub
anchors a review comment by side + line:
- `side: "RIGHT"` + `line: <newLine>` for added/context lines (the common case).
- `side: "LEFT"` + `line: <oldLine>` for removed lines.
Only lines present in the diff can be commented on. Multi-line comments also send
`start_line` + `start_side`.

## 3. The review UI template (fixed structure, self-contained)

The UI is `assets/review-template.html`. Its structure is **fixed** — every generated
page looks the same and only the injected data differs. It provides:

- A **sticky sidebar** with PR title/stats, the review summary box, a one-click
  "Copy all comments as JSON" button, an insights show/hide toggle, and a clickable group
  table-of-contents. (No approve/request-changes action — this is a comments-only tool.)
- A main column of collapsible **groups**, each with a neutral reasoning line, a
  "Things worth confirming" list, and a **file manifest** (each file's role in the group,
  linking down to its diff) so the reviewer can trace how the files fit together.
- Per **file**: a rich header (status pill, path + rename arrow, language, +/− counts),
  the AI `description`, a "Comment on this file" button, and an IDE-syntax-highlighted
  diff (via vendored highlight.js) with GitHub-style add/remove backgrounds and dual
  line numbers.
- A **Diff view** selector (sidebar) with two modes: **Unified** (inline, default) and
  **Split** (old on the left, new on the right).
- **Expandable context**: when `fullContent` is embedded, "⋯ expand" rows appear in the
  gaps around each hunk; clicking reveals the surrounding real file lines (a chunk at a
  time, or all) inline, so the reviewer can trace how a change sits in the file without
  loading a separate full-file view. Revealed lines are commentable too.
- **Inline insight subtitles**: read-only 💡 annotations (from each file's `insights[]`)
  aimed at the *change* — what moved and its consequence, who calls it, why it exists — not
  a narration of every block. Each marks its block with a left rail and renders **above the
  block's first line**, labelled with the covered lines; `notable` insights render
  emphasized and `routine` ones dim/compact so the important ones stand out. A sidebar
  toggle hides them for a bare diff. They never enter the exported review.
- **Three comment levels**: click a line to comment on it, click "Comment on this file"
  for a file-level comment, and the sidebar summary for the overall review. Comments are
  stored in state and re-rendered, so editing one never loses it.

The template has three placeholder tokens, each appearing exactly once:
`/* __HLJS_LIB__ */`, `/* __HLJS_THEME__ */`, and `/*__PR_REVIEW_DATA__*/ null`.

> Full file contents (for the "⋯ expand context" feature) are fetched by the **merge
> step** (Stage C) when you pass `--repo`/`--sha` — no separate fetch pass is needed. A
> file whose content can't be fetched (deleted, moved, permission) simply has no
> `fullContent` and shows no expand affordance; the diff itself is unaffected. Larger
> content means a larger HTML file — warn the user if the total is very large (> ~5 MB).

## 4. Build the UI by injecting data + vendored assets (script, not by hand)

Do **not** hand-edit the huge JSON into the template. Run a small script that reads the
template, the vendored `highlight.min.js`, the vendored `hljs-github-theme.css`, and the
groups JSON, replaces the three tokens, and writes `/tmp/pr-<number>-review.html`. Resolve
the plugin root first (see **Resolving the plugin root** in §2 — `$CLAUDE_PLUGIN_ROOT` is
empty in ad-hoc Bash, and shell state doesn't carry between blocks, so resolve it again
here); the skill assets live under `skills/pr-review-ui/assets/`.

```bash
PR=<number>
PLUGIN_ROOT="$CLAUDE_PLUGIN_ROOT"
if [ -z "$PLUGIN_ROOT" ] || [ ! -e "$PLUGIN_ROOT/skills/pr-review-ui/SKILL.md" ]; then
  PLUGIN_ROOT=$(ls -d "$HOME"/.claude/plugins/cache/*/interactive-pr-review/*/ 2>/dev/null | sort -V | tail -1)
fi
ASSETS="$PLUGIN_ROOT/skills/pr-review-ui/assets"

python3 - "$ASSETS" "/tmp/pr-$PR-groups.json" "/tmp/pr-$PR-review.html" <<'PY'
import json, sys, pathlib
assets, data_path, out_path = map(pathlib.Path, sys.argv[1:4])
tpl   = (assets / "review-template.html").read_text()
lib   = (assets / "vendor" / "highlight.min.js").read_text()
theme = (assets / "vendor" / "hljs-github-theme.css").read_text()
data  = pathlib.Path(data_path).read_text().strip()
json.loads(data)  # validate before injecting

# Inject lib and theme first (they contain no other tokens), then the data.
for tok, val in [("/* __HLJS_LIB__ */", lib),
                 ("/* __HLJS_THEME__ */", theme),
                 ("/*__PR_REVIEW_DATA__*/ null", data)]:
    assert tpl.count(tok) == 1, f"expected exactly one {tok!r}, found {tpl.count(tok)}"
    tpl = tpl.replace(tok, val)
for leftover in ("__HLJS_LIB__", "__HLJS_THEME__", "__PR_REVIEW_DATA__"):
    assert leftover not in tpl, f"leftover token {leftover}"
pathlib.Path(out_path).write_text(tpl)
print("OK wrote", out_path, tpl.__len__(), "chars")
PY

open "/tmp/pr-$PR-review.html"      # macOS; use xdg-open (Linux) / start (Windows)
```

Then tell the user to walk the groups (sidebar TOC to jump around), read each file's
description and the group's "Things worth confirming", click lines and/or "Comment on
this file" to leave comments, optionally fill the summary, click **Copy all comments as
JSON**, and paste the result back into the chat.

## 5. Exported comment JSON (UI → Claude)

This is a **comments-only** review tool — there is no approve / request-changes action.
One click on **Copy all comments as JSON** produces a single object with the summary,
every line comment, and every file comment together:

```json
{
  "pr": 128,
  "headSha": "4d5e6f7…",
  "summary": "Overall thoughts, if any.",
  "comments": [
    { "path": "src/middleware/rateLimit.ts", "line": 22, "side": "RIGHT",
      "body": "Does this refill run per-request?" }
  ],
  "fileComments": [
    { "path": "src/middleware/rateLimit.ts",
      "body": "Where is the limiter config meant to live?" }
  ]
}
```

There is no `action` field. Every posted review is a plain `COMMENT` review.

## 6. Post comments back to GitHub (always a COMMENT review)

1. **Validate** the pasted JSON: it parses, `pr` matches, every line comment has `path`,
   `line`, `side`, non-empty `body`; every file comment has `path` + `body`.
2. **Summarize and confirm.** Print line comments as `path:line — <first line of body>`
   and file comments as `path (file) — <first line>`, and ask the user to confirm before
   posting.
3. **Post one COMMENT review** so everything lands together. Line comments become the
   review's `comments[]`. **File-level comments** have no diff line to anchor to — fold
   each into the review `body` under a "File notes" heading (prefixed with the path), or
   post them separately with `gh pr comment`. Build the payload from a file (never
   hand-concatenate JSON) and submit via `gh api`:

```bash
gh api --method POST -H "Accept: application/vnd.github+json" \
  "/repos/{owner}/{repo}/pulls/$PR/reviews" --input /tmp/pr-$PR-review-payload.json
```

Payload shape: `{ "commit_id": "<headSha>", "event": "COMMENT", "body": "<summary + file notes>", "comments": [ { "path", "line", "side", "body", (optional) "start_line", "start_side" } ] }`.
The `event` is always `COMMENT`.

4. **Report** the returned review URL and counts. If GitHub rejects a comment whose line
   isn't in the diff, name it and offer to repost via `gh pr comment $PR --body "…"`.

## 7. Temp files persist (manual cleanup only)

**Do not auto-delete the artifacts.** After a review is posted (or abandoned), the temp
files are kept on purpose so the PR can be reopened later with
`/interactive-pr-review:reopen <PR>` — which rebuilds the UI from `/tmp/pr-$PR-groups.json`
without re-fetching or re-analyzing (as long as the PR's head SHA hasn't moved).

A PR's full artifact set is these 7 files:

```bash
/tmp/pr-$PR.diff /tmp/pr-$PR-meta.json /tmp/pr-$PR-parsed.json \
/tmp/pr-$PR-analysis.json /tmp/pr-$PR-groups.json /tmp/pr-$PR-review.html \
/tmp/pr-$PR-review-payload.json
```

Removal is manual, via the `cleanup` command — never as an end-of-review step:
- `/interactive-pr-review:cleanup <PR>` removes one PR's artifacts.
- `/interactive-pr-review:cleanup` removes all cached PRs (`/tmp/pr-*`).

Of the set, `/tmp/pr-$PR-groups.json` is the one that matters for reopening — it drives the
UI and carries `pr.headSha` / `pr.url`. The others are regenerable intermediates.

### Notes & gotchas

- This is a comments-only tool: every review is posted with `event: "COMMENT"`. Never
  use `APPROVE` or `REQUEST_CHANGES`, and there is no action field to read.
- A review with zero comments and an empty body is rejected — require a summary or at
  least one comment before posting.
- A `COMMENT` review works even on your own PR, so there is no author check to make.
- `line` uses the **new** file's numbers for `RIGHT`, the **old** file's for `LEFT` —
  matching what the UI computed from hunk headers.
- highlight.js is vendored under `assets/vendor/` so the generated HTML is fully
  self-contained (no network at view time). Don't switch to a CDN link.

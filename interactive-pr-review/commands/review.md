---
description: Fetch a GitHub PR by number, group its diff into logical chunks with per-group and per-file reasoning plus block-level insight subtitles that explain each function/interface/const/etc as you read, present an interactive review UI (sidebar, IDE-highlighted diffs, per-file headers), and post your line- and file-level comments back to GitHub.
argument-hint: <pr-number> [owner/repo]
allowed-tools: Bash, Read, Write, Glob, Grep, Skill
---

# Interactive PR Review

You are running the **interactive PR review** workflow. The user wants to review pull
request **#$1** carefully, chunk by chunk, then post their comments back to GitHub.

`$ARGUMENTS` contains everything the user passed. Conventionally:
- `$1` = the PR number (required). If the user pasted a full PR URL, extract the number
  from it and the `owner/repo` slug too.
- `$2` = an optional `owner/repo` slug (or full repo URL) when the user is not inside the
  target repo.

## Guiding principles

- **Never alter the diff content.** The hunks you show must be byte-for-byte what
  GitHub returns. You group, order, syntax-highlight, and annotate — you do not rewrite,
  reformat, or "clean up" the code being reviewed.
- **The user is the reviewer.** Your reasoning is advisory. Nothing is posted to
  GitHub until the user has explicitly approved the exact set of comments.
- **Keep the big blobs out of context.** The grouping JSON and the built HTML are large;
  they live in temp files and are moved around with scripts, never pasted into the chat.
- **Fail loudly and early.** If `gh` is missing, unauthenticated, or the PR can't be
  found, stop and tell the user precisely what to fix.

## Step 0 — Load the skill

Invoke the `pr-review-ui` skill first — it is the source of truth for the mechanics
(fetch, schema, the injection script, comment posting, cleanup).

```
Skill(pr-review-ui)
```

## Step 1 — Resolve the target and preconditions

1. Confirm a PR number. If `$1` is empty or not a number (and no number can be parsed
   from a pasted URL), ask the user for the PR number and stop.
2. Verify tooling: `gh --version` and `gh auth status`. If either fails, tell the user
   to install the GitHub CLI (`https://cli.github.com`) and run `gh auth login`, then
   stop.
3. Determine the repo: if `$2` (or a URL) gives an `owner/repo`, use `--repo <slug>`;
   otherwise rely on the current directory's git remote. If neither is available, ask.

## Step 2 — Fetch the PR (read-only)

Per the skill, gather (use `--repo <slug>` when you have one):

- Metadata → write to `/tmp/pr-$1-meta.json` (number, title, author, base, head, url,
  additions, deletions, changedFiles, headSha). The parser embeds this as the `pr` object.
- The unified diff, saved to `/tmp/pr-$1.diff`: `gh pr diff $1 > /tmp/pr-$1.diff`
- The head commit SHA: `gh pr view $1 --json commits --jq '.commits[-1].oid'`

If the PR is merged/closed with an empty diff, report the state and stop.

## Step 3 — Parse → analyze → merge (the pipeline; see skill §2)

The grouping JSON is built by three stages; **the diff never passes through the model as
output**, and **every stage runs in the main chat — do not spawn a subagent.**

1. **Parse (deterministic):** resolve the plugin root first (see skill §2 "Resolving the
   plugin root" — `$CLAUDE_PLUGIN_ROOT` is empty in ad-hoc Bash), then run the parser:
   ```bash
   PLUGIN_ROOT="$CLAUDE_PLUGIN_ROOT"
   if [ -z "$PLUGIN_ROOT" ] || [ ! -e "$PLUGIN_ROOT/skills/pr-review-ui/SKILL.md" ]; then
     PLUGIN_ROOT=$(ls -d "$HOME"/.claude/plugins/cache/*/interactive-pr-review/*/ 2>/dev/null | sort -V | tail -1)
   fi
   python3 "$PLUGIN_ROOT/skills/pr-review-ui/scripts/parse_diff.py" /tmp/pr-$1.diff /tmp/pr-$1-parsed.json --pr-json /tmp/pr-$1-meta.json
   ```
   This assigns stable `hunkId`s and is the byte-exact source of truth for the code.
2. **Analyze (you, in the main chat):** read `/tmp/pr-$1-parsed.json` (and `/tmp/pr-$1.diff`
   for extra context) and author the **analysis only** — groups, neutral `reasoning`,
   `thingsToConfirm`, per-file `role`/`description`/`insights`, and the `hunkIds` each group
   includes — **no code** (reference hunks by `hunkId`). Write it to
   `/tmp/pr-$1-analysis.json` with a quoted-delimiter heredoc, then run the validation
   snippet from the skill (§2). The payload is small (titles + prose + ids + line numbers),
   so authoring it inline is reliable at any PR size. The skill (§2) has the full field-by-
   field guidance for grouping and for the per-block insight subtitles.
3. **Merge (deterministic):** `python3 …/scripts/merge_analysis.py /tmp/pr-$1-parsed.json /tmp/pr-$1-analysis.json /tmp/pr-$1-groups.json --repo <owner/name> --sha <headSha>`.
   This joins the analysis onto the real hunks, embeds `fullContent` (for "⋯ expand
   context") via `--repo`/`--sha`, and **enforces invariants**: it errors on unknown
   `hunkId`s, and **guarantees coverage**: any unassigned hunk or hunkless file (binary,
   rename) not placed by the analysis is swept into a synthetic "Other changes" group, and
   the merge errors out if anything is still unshown — so every changed file always appears.
   If the output notes files were swept into "Other changes", consider revising the analysis
   to group them meaningfully (the review is complete either way).

## Step 4 — Build and open the review UI

Run the injection script from the skill (§4) to produce `/tmp/pr-$1-review.html`: it reads
the fixed template, the vendored `highlight.min.js` and `hljs-github-theme.css`, and
`/tmp/pr-$1-groups.json`, replaces the three tokens, and writes the self-contained HTML.
Then open it (`open /tmp/pr-$1-review.html` on macOS).

Tell the user the page has: a **sidebar** (summary, one-click export button, a **Diff
view** selector — Unified / Split, an insights show/hide toggle, group nav), collapsible
**groups** with reasoning, a "Things worth confirming" list, and a file manifest (each
file's role in the group, linking to its diff), and per **file** a rich header + neutral
description + IDE-highlighted diff. Around each hunk, "⋯ expand context" reveals the
surrounding real file lines on demand. Inline 💡 **insight subtitles** sit above each
logical block (function, interface, const group, component, conditional, test…),
describing what it is/does/takes/is-used-for — like explanatory comments over the file;
toggle them off for a bare diff. They can comment at three levels —
click a line (including expanded context lines), "Comment on this file", or the overall
summary — then click **Copy all comments as JSON** (one click captures summary + line +
file comments together) and paste it back. This is a comments-only tool: there is no
approve or
request-changes. (Insight bubbles are just guidance; they're never part of the export.)

## Step 5 — Post the comments back to GitHub (comments-only)

When the user pastes the JSON:

1. Parse and validate (schema in the skill; `summary`, `comments[]`, `fileComments[]` —
   no `action`). Echo a concise summary: each line comment as `path:line — <first line>`,
   each file comment as `path (file) — <first line>`. **Ask the user to confirm** before
   posting.
2. On confirmation, submit **one** `COMMENT` review anchored to the head SHA. Line
   comments go in the review `comments[]`; fold `fileComments` into the review `body`
   under a "File notes" heading (they have no diff line to anchor to). Build the payload
   from a file and submit with `gh api POST /repos/{owner}/{repo}/pulls/$1/reviews` using
   `event: "COMMENT"`. Never approve or request changes — even on the user's own PR, a
   `COMMENT` review works.
3. Report the review URL and counts. If a comment's line isn't in the diff, name it and
   offer to repost via `gh pr comment $1`.

## Step 6 — Keep the artifacts (no auto-cleanup)

Do **not** delete the temp files. They persist by design so the review can be reopened
later with `/interactive-pr-review:reopen $1` — which rebuilds the UI from the cached
`/tmp/pr-$1-groups.json` without re-fetching or re-analyzing, as long as the PR's head SHA
hasn't moved. Let the user know they can reopen #$1 anytime, and that when they're done
they can remove the artifacts with `/interactive-pr-review:cleanup $1` (or
`/interactive-pr-review:cleanup` to clear all cached PRs).

## Edge cases

- **Empty diff / already merged:** report the PR state and stop.
- **Binary or renamed files:** include them in the analysis (renamed with a `previousPath`
  arrow in the header; binary with an empty diff and a note) — comments can only anchor
  to text lines present in the diff.
- **User pastes malformed JSON:** show the parse error, point at the offending field,
  ask them to re-copy from the UI.
- **User wants to abandon:** if they don't paste JSON or say to stop, post nothing. The
  artifacts stay in `/tmp` (they can reopen or remove them later); mention
  `/interactive-pr-review:cleanup $1` if they want to discard them now.

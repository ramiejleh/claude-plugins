---
description: Reopen a previously analyzed GitHub PR from its cached review artifacts. Rebuilds and opens the review UI instantly if the cache is still fresh; if the PR's head commit has moved since it was analyzed, it re-runs the full analysis so you never review a stale diff.
argument-hint: <pr-number> [owner/repo]
allowed-tools: Bash, Read, Write, Glob, Grep, Skill
---

# Reopen a cached PR review

You are reopening a pull request the user already analyzed with
`/interactive-pr-review:review`. Its artifacts persist in `/tmp` (this plugin no longer
auto-cleans them), so a review can be revisited without re-fetching and re-analyzing —
**as long as the PR hasn't changed on GitHub.**

`$ARGUMENTS` contains everything the user passed. Conventionally:
- `$1` = the PR number (required). If the user pasted a full PR URL, extract the number.
- `$2` = an optional `owner/repo` slug (or full repo URL). If omitted, it is derived from
  the cached PR data.

## Guiding principles

- **Fresh-only.** Never show a cached review that no longer matches the PR. If the cached
  head SHA differs from the live one, re-analyze instead of reopening stale artifacts.
- **The cache is the groups JSON.** `/tmp/pr-$1-groups.json` drives the UI and carries
  `pr.headSha` / `pr.url` — everything the reopen needs, plus the vendored assets.

## Step 0 — Load the skill

Invoke the `pr-review-ui` skill first — it is the source of truth for the pipeline, the
injection script (§4), and comment posting.

```
Skill(pr-review-ui)
```

## Step 1 — Resolve the PR number

Confirm a PR number from `$1` (or parse it from a pasted URL). If none can be determined,
ask the user for the PR number and stop.

## Step 2 — Require cached data

The reopen only works from a prior analysis. Check for the groups JSON:

```bash
test -f /tmp/pr-$1-groups.json && echo "cache present" || echo "no cache"
```

If it is missing, stop with a clear message — do **not** silently start a fresh analysis:

> No cached analysis found for #$1. Run `/interactive-pr-review:review $1` to analyze it
> first.

## Step 3 — Resolve the repo

If `$2` (or a URL) gives an `owner/repo`, use `--repo <slug>`. Otherwise derive it from the
cached PR url:

```bash
SLUG=$(python3 -c "import json,sys; u=json.load(open('/tmp/pr-$1-groups.json'))['pr'].get('url',''); import re; m=re.search(r'github\.com/([^/]+/[^/]+)/pull', u); print(m.group(1) if m else '')")
```

Use `$2` if the user gave a slug; otherwise `$SLUG` from the cached url. If both are empty,
fall back to the current directory's git remote (as `review` does).

## Step 4 — Freshness check (cached vs live head SHA)

Read the cached head SHA and fetch the live one. **Build the `--repo` flag as a bash array**,
not an unquoted string — an unquoted `$REPO_FLAG` is *not* word-split by zsh (the default
macOS shell), so `--repo owner/name` would be passed to `gh` as a single glued argument and
the fetch would fail (yielding a false "stale" verdict). An array expands correctly to zero
or two words in both bash and zsh:

```bash
CACHED_SHA=$(python3 -c "import json; print(json.load(open('/tmp/pr-$1-groups.json'))['pr'].get('headSha',''))")
REPO_ARGS=(); [ -n "$SLUG" ] && REPO_ARGS=(--repo "$SLUG")   # use $2's slug here if given
LIVE_SHA=$(gh pr view $1 "${REPO_ARGS[@]}" --json commits --jq '.commits[-1].oid')
echo "cached=$CACHED_SHA live=$LIVE_SHA"
```

- **If `CACHED_SHA` equals `LIVE_SHA` → the cache is fresh. Go to Step 5 (reopen).**
- **If they differ → the cache is stale. Go to Step 6 (re-analyze).**
- If the live SHA can't be fetched (network/permission), tell the user, and offer to reopen
  the cached review as-is *with an explicit stale-risk warning* rather than failing hard.

## Step 5 — Reopen (cache is fresh)

Rebuild the HTML from the cached groups JSON using the skill's §4 injection script, then
open it. Rebuild (don't just reopen a leftover `.html`): it guarantees the current template
and assets are used, and works even if `/tmp/pr-$1-review.html` was cleaned while the groups
JSON was kept.

Run the §4 script with `PR=$1` (reads the fixed template + `ui.css` + `ui.js` + the vendored
`highlight.min.js` + `hljs-github-theme.css` + `/tmp/pr-$1-groups.json`, writes
`/tmp/pr-$1-review.html`), then:

```bash
open /tmp/pr-$1-review.html      # macOS; xdg-open (Linux) / start (Windows)
```

Tell the user this is the cached review for #$1 (head `CACHED_SHA`), reopened without
re-analysis, and remind them they can comment and export exactly as before. When they paste
the exported JSON back, post it per the skill §6 (and `review` Step 5) — one `COMMENT`
review anchored to the head SHA.

## Step 6 — Re-analyze (cache is stale)

Tell the user the PR has moved since it was analyzed and you're refreshing it:

> #$1 has new commits since it was analyzed (`<CACHED_SHA short>` → `<LIVE_SHA short>`).
> Re-running the analysis on the current diff…

Then run the fresh pipeline exactly as in `review` Steps 2–4 — fetch (metadata + diff +
head SHA), parse → analyze → merge, build and open the UI — overwriting the cached
artifacts for #$1. Defer to the skill and the `review` command for the mechanics; do not
duplicate the pipeline details here. After it opens, proceed as a normal review (comment,
export, post per Step 5 / skill §6).

## Notes

- Artifacts for #$1 remain in `/tmp` after reopening (by design). Remove them with
  `/interactive-pr-review:cleanup $1` (or with no argument to clear all cached PRs).
- **PR now merged/closed:** the freshness step reports the state; a re-analysis may return
  an empty diff — report it and stop.
- **Malformed/partial cache** (e.g. groups JSON missing `pr.headSha`): treat as stale and
  offer to re-analyze, or point the user at `cleanup $1` then `review $1`.

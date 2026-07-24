---
description: Remove cached PR review artifacts from /tmp. With a PR number, removes just that PR's files; with no argument, removes every cached PR. Lists what it will delete and asks for confirmation first.
argument-hint: [pr-number]
allowed-tools: Bash
---

# Clean up cached PR review artifacts

The `interactive-pr-review` plugin keeps its temp artifacts (`/tmp/pr-<n>.diff`,
`-meta.json`, `-parsed.json`, `-analysis.json`, `-groups.json`, `-review.html`,
`-review-payload.json`) after a review so they can be reopened with
`/interactive-pr-review:reopen <n>`. This command removes them when you're done.

`$1` is optional:
- `$1` given (a PR number) → remove only that PR's artifacts.
- `$1` empty → remove **all** cached PR artifacts (`/tmp/pr-*`).

## Step 1 — List what would be removed

**Do not delete anything yet.** These files can be reopened, and "clean all" could catch a
PR someone is mid-review on — so always show the target set first.

For a **specific PR** (`$1` set), match two non-overlapping patterns. The dot-vs-dash
boundary after the number keeps `pr-128` from also matching `pr-1280`:

```bash
find /tmp -maxdepth 1 \( -name "pr-$1.diff" -o -name "pr-$1-*" \) -type f -print0 \
  | xargs -0 ls -lh 2>/dev/null
```

For **all cached PRs** (`$1` empty):

```bash
find /tmp -maxdepth 1 -name "pr-*" -type f -print0 | xargs -0 ls -lh 2>/dev/null
```

If the listing is empty, tell the user there is nothing to clean for that scope and stop.

## Step 2 — Confirm

Show the user the matched files (paths + sizes) and ask them to confirm deletion. For the
all-PRs case, make it explicit that every cached review will be removed and any in-progress
reopen would need a fresh `review`. Wait for confirmation.

## Step 3 — Delete and report

On confirmation, remove exactly the matched set with the same patterns:

Specific PR:

```bash
rm -f /tmp/pr-$1.diff /tmp/pr-$1-*
```

All cached PRs:

```bash
rm -f /tmp/pr-*
```

Then report what was removed (count and, for a single PR, that its cache is gone — a later
`reopen $1` will say there's no cached analysis until it's re-run with `review`).

## Notes

- This only touches `/tmp/pr-*` artifacts created by this plugin. It never deletes anything
  in the repo or the GitHub PR.
- If the user only wants to free space but keep the ability to reopen, note that
  `-review.html` and the intermediate `-diff`/`-parsed`/`-analysis` files can be
  regenerated from `-groups.json` alone — but this command removes a PR's set as a whole
  for simplicity. Point them at a specific `rm` if they ask for finer control.

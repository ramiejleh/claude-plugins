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

Use **Python `glob`** to build the list — the same mechanism `list` uses. Do **not** use
`find /tmp …`: on macOS `/tmp` is a symlink to `private/tmp`, and `find` in its default
physical mode (`-P`) won't descend a symlinked starting path, so `find /tmp` matches nothing
and the preview would falsely look empty even though files exist (and the `rm` in Step 3,
which uses shell globbing, *would* delete them — a dangerous mismatch). `glob` resolves the
symlink, so its preview always agrees with the delete. It also handles the no-match case
cleanly (empty list, no shell "no matches found" error).

For a **specific PR** (`$1` set), match two non-overlapping patterns — the dot-vs-dash
boundary after the number keeps `pr-128` from also matching `pr-1280`. For **all cached
PRs** (`$1` empty), match `pr-*`:

```bash
python3 - "$1" <<'PY'
import glob, os, sys
pr = sys.argv[1] if len(sys.argv) > 1 else ""
if pr:
    files = sorted(set(glob.glob("/tmp/pr-%s.diff" % pr) + glob.glob("/tmp/pr-%s-*" % pr)))
    scope = "#" + pr
else:
    files = sorted(glob.glob("/tmp/pr-*"))
    scope = "all cached PRs"
if not files:
    print("Nothing to clean for %s." % scope)
else:
    total = 0
    for f in files:
        sz = os.path.getsize(f); total += sz
        print("  %8.1f KB  %s" % (sz / 1024, f))
    print("%d file(s), %.1f KB total, for %s." % (len(files), total / 1024, scope))
PY
```

If it prints "Nothing to clean", tell the user and stop.

## Step 2 — Confirm

Show the user the matched files (paths + sizes) and ask them to confirm deletion. For the
all-PRs case, make it explicit that every cached review will be removed and any in-progress
reopen would need a fresh `review`. Wait for confirmation.

## Step 3 — Delete and report

On confirmation, remove exactly the set from Step 1 — same `glob` patterns, so the delete
can never target anything the preview didn't show:

```bash
python3 - "$1" <<'PY'
import glob, os, sys
pr = sys.argv[1] if len(sys.argv) > 1 else ""
if pr:
    files = sorted(set(glob.glob("/tmp/pr-%s.diff" % pr) + glob.glob("/tmp/pr-%s-*" % pr)))
else:
    files = sorted(glob.glob("/tmp/pr-*"))
n = 0
for f in files:
    try:
        os.remove(f); n += 1
    except OSError as e:
        print("  could not remove %s: %s" % (f, e))
print("Removed %d file(s)." % n)
PY
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

---
description: Remove cached PR review artifacts from the project's .reviews/ directory. With a PR number, removes just that PR's files; with no argument, removes every cached PR. Lists what it will delete and asks for confirmation first.
argument-hint: [pr-number]
allowed-tools: Bash
---

# Clean up cached PR review artifacts

The `interactive-pr-review` plugin keeps its artifacts (`.reviews/pr-<n>.diff`,
`-meta.json`, `-parsed.json`, `-analysis.d/` (the per-group analysis fragments),
`-analysis.json`, `-groups.json`, `-review.html`, `-review-payload.json`) after a review so
they can be reopened with `/interactive-pr-review:reopen <n>`. This command removes them when
you're done.

`$1` is optional:
- `$1` given (a PR number) → remove only that PR's artifacts.
- `$1` empty → remove **all** cached PR artifacts (`.reviews/pr-*`).

## Step 1 — List what would be removed

**Do not delete anything yet.** These files can be reopened, and "clean all" could catch a
PR someone is mid-review on — so always show the target set first.

Use **Python `glob`** to build the list — the same mechanism `list` uses — so the preview
always agrees with what the delete step actually removes. It also handles the no-match case
cleanly (empty list, rather than a shell "no matches found" error).

For a **specific PR** (`$1` set), match two non-overlapping patterns — the dot-vs-dash
boundary after the number keeps `pr-128` from also matching `pr-1280`. For **all cached
PRs** (`$1` empty), match `pr-*`:

```bash
REVIEWS="$(git rev-parse --show-toplevel 2>/dev/null || pwd)/.reviews"
python3 - "$1" "$REVIEWS" <<'PY'
import glob, os, sys
pr = sys.argv[1] if len(sys.argv) > 1 else ""
REVIEWS = sys.argv[2]
def entry_size(p):  # getsize() on a dir reports only the inode; sum the tree instead.
    if not os.path.isdir(p):
        return os.path.getsize(p)
    return sum(os.path.getsize(os.path.join(r, n))
               for r, _, ns in os.walk(p) for n in ns)
if pr:
    files = sorted(set(glob.glob(os.path.join(REVIEWS, "pr-%s.diff" % pr))
                       + glob.glob(os.path.join(REVIEWS, "pr-%s-*" % pr))))
    scope = "#" + pr
else:
    files = sorted(glob.glob(os.path.join(REVIEWS, "pr-*")))
    scope = "all cached PRs"
if not files:
    print("Nothing to clean for %s." % scope)
else:
    total = 0
    for f in files:
        sz = entry_size(f); total += sz
        label = f + "/" if os.path.isdir(f) else f
        print("  %8.1f KB  %s" % (sz / 1024, label))
    print("%d entr(y/ies), %.1f KB total, for %s." % (len(files), total / 1024, scope))
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
REVIEWS="$(git rev-parse --show-toplevel 2>/dev/null || pwd)/.reviews"
python3 - "$1" "$REVIEWS" <<'PY'
import glob, os, shutil, sys
pr = sys.argv[1] if len(sys.argv) > 1 else ""
REVIEWS = sys.argv[2]
if pr:
    files = sorted(set(glob.glob(os.path.join(REVIEWS, "pr-%s.diff" % pr))
                       + glob.glob(os.path.join(REVIEWS, "pr-%s-*" % pr))))
else:
    files = sorted(glob.glob(os.path.join(REVIEWS, "pr-*")))
n = 0
for f in files:
    try:
        shutil.rmtree(f) if os.path.isdir(f) else os.remove(f)  # -analysis.d/ is a directory
        n += 1
    except OSError as e:
        print("  could not remove %s: %s" % (f, e))
print("Removed %d entr(y/ies)." % n)
PY
```

Then report what was removed (count and, for a single PR, that its cache is gone — a later
`reopen $1` will say there's no cached analysis until it's re-run with `review`).

## Notes

- This only touches `.reviews/pr-*` artifacts created by this plugin. It never deletes anything
  in the repo or the GitHub PR.
- This command removes a PR's set as a whole. If the user wants to free space but keep the
  ability to reopen, everything except `-groups.json` is regenerable from it — point them at
  a specific `rm`.

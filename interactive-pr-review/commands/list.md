---
description: List the PRs that have cached review artifacts in /tmp — the ones you can reopen instantly with reopen. Shows each PR's number, title, cached size, and age. Read-only and offline; makes no GitHub calls.
argument-hint: (no arguments)
allowed-tools: Bash
---

# List cached PR reviews

Show which PRs have review artifacts cached in `/tmp` and are therefore available to
`/interactive-pr-review:reopen`. This is a **local, offline** listing — it does not call
GitHub. The freshness check (has the PR moved since analysis?) happens when you actually
`reopen` one.

A PR counts as "available to reopen" when its `/tmp/pr-<n>-groups.json` exists — that's the
single artifact `reopen` rebuilds the UI from.

## Step — Scan and print the table

Run this once and present the result. It finds every cached `groups.json`, reads
`pr.number` / `pr.title` / `pr.headSha` from each, and computes the total size and age of
that PR's artifact set:

```bash
python3 - <<'PY'
import glob, json, os, time

rows = []
for gpath in sorted(glob.glob("/tmp/pr-*-groups.json")):
    # PR number is the token between "/tmp/pr-" and "-groups.json"
    base = os.path.basename(gpath)                      # pr-<n>-groups.json
    num = base[len("pr-"):-len("-groups.json")]
    try:
        pr = json.load(open(gpath)).get("pr", {})
    except Exception:
        pr = {}
    # Total size + newest mtime across this PR's whole artifact set.
    files = glob.glob("/tmp/pr-%s.diff" % num) + glob.glob("/tmp/pr-%s-*" % num)
    size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    mtime = max((os.path.getmtime(f) for f in files if os.path.exists(f)), default=0)
    age_h = (time.time() - mtime) / 3600 if mtime else 0
    age = ("%.0fh" % age_h) if age_h < 48 else ("%.0fd" % (age_h / 24))
    rows.append({
        "num": num,
        "title": (pr.get("title") or "").strip(),
        "sha": (pr.get("headSha") or "")[:7],
        "size_kb": size / 1024,
        "age": age,
        "files": len(files),
    })

if not rows:
    print("NO_CACHE")
else:
    print("%-6s  %-9s  %-6s  %-5s  %s" % ("PR", "cached", "age", "sha", "title"))
    for r in rows:
        print("#%-5s  %6.0f KB  %-6s  %-5s  %s" % (
            r["num"], r["size_kb"], r["age"], r["sha"], r["title"][:70]))
    print("\n%d cached PR(s)." % len(rows))
PY
```

## Present the result

- If the script prints `NO_CACHE`: tell the user there are no cached PR reviews yet, and
  that running `/interactive-pr-review:review <pr#>` will analyze one (its artifacts then
  persist for reopen).
- Otherwise: show the table and remind them they can:
  - `/interactive-pr-review:reopen <pr#>` — reopen one instantly (re-analyzes only if the
    PR moved since it was cached),
  - `/interactive-pr-review:cleanup <pr#>` or `/interactive-pr-review:cleanup` — remove a
    PR's artifacts, or all of them.

## Notes

- Listing is keyed on `groups.json`. If a PR's `groups.json` was removed but stray
  intermediates remain, it won't appear here as reopenable — `cleanup` (all) still clears
  those leftovers.
- `age` is the newest artifact's modification time (how recently it was analyzed/reopened),
  not the PR's own activity. Whether the cache is actually stale is decided by `reopen`'s
  head-SHA check against GitHub.

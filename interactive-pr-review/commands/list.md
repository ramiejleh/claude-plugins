---
description: List the PRs that have cached review artifacts in /tmp — the ones you can reopen — and check each one's freshness against GitHub (fresh = the PR's head commit is unchanged since it was analyzed; any new commit, force-push, rebase, or amend marks it STALE).
argument-hint: (no arguments)
allowed-tools: Bash
---

# List cached PR reviews

Show which PRs have review artifacts cached in `/tmp` (the ones available to
`/interactive-pr-review:reopen`) and, for each, whether the cache is still **fresh** — i.e.
whether the PR's current head commit on GitHub matches the head SHA that was analyzed.

A PR counts as cached when its `/tmp/pr-<n>-groups.json` exists — that's the single artifact
`reopen` rebuilds the UI from, and it carries both `pr.headSha` (the analyzed commit) and
`pr.url` (used to resolve the repo).

**Freshness = head-SHA equality.** Fresh means the live head SHA equals the cached one, so
`reopen` would open the cache as-is. Any change to the PR moves the head SHA — one extra
commit on top, a force-push, a rebase, or an amend — and the PR is reported **STALE**, which
means `reopen` would re-analyze it. This check makes one `gh` call per cached PR, so it is
**not** offline (unlike a bare artifact listing).

## Step — Scan, check freshness, and print the table

Run this once and present the result. For each cached `groups.json` it reads
`pr.number`/`pr.title`/`pr.headSha`/`pr.url`, computes the artifact set's total size and age,
and fetches the live head SHA to classify freshness:

```bash
python3 - <<'PY'
import glob, json, os, time, re, subprocess

def live_sha(slug, num):
    """Live head SHA for PR <num> in <slug>, or None on any failure (offline, auth, gone)."""
    args = ["gh", "pr", "view", str(num), "--json", "commits", "--jq", ".commits[-1].oid"]
    if slug:
        args[3:3] = ["--repo", slug]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        out = r.stdout.strip()
        return out if (r.returncode == 0 and out) else None
    except Exception:
        return None

rows = []
for gpath in sorted(glob.glob("/tmp/pr-*-groups.json")):
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
    cached_sha = pr.get("headSha") or ""
    m = re.search(r"github\.com/([^/]+/[^/]+)/pull", pr.get("url", "") or "")
    slug = m.group(1) if m else ""
    lsha = live_sha(slug, num)
    if not cached_sha or lsha is None:
        fresh = "?"                    # unknown: no cached sha, offline, auth, or PR gone
    elif lsha == cached_sha:
        fresh = "fresh"
    else:
        fresh = "STALE"
    rows.append({"num": num, "title": (pr.get("title") or "").strip(),
                 "sha": cached_sha[:7], "size_kb": size / 1024, "age": age, "fresh": fresh})

if not rows:
    print("NO_CACHE")
else:
    print("%-6s  %-6s  %-8s  %-5s  %-7s  %s" % ("PR", "fresh", "cached", "age", "sha", "title"))
    for r in rows:
        print("#%-5s  %-6s  %5.0f KB  %-5s  %-7s  %s" % (
            r["num"], r["fresh"], r["size_kb"], r["age"], r["sha"], r["title"][:60]))
    print("\n%d cached PR(s)." % len(rows))
PY
```

## Present the result

- If the script prints `NO_CACHE`: tell the user there are no cached PR reviews yet, and
  that `/interactive-pr-review:review <pr#>` will analyze one (its artifacts then persist).
- Otherwise show the table and interpret the `fresh` column:
  - **fresh** — the PR is unchanged since analysis; `/interactive-pr-review:reopen <pr#>`
    opens the cache instantly, no re-analysis.
  - **STALE** — the PR's head moved (new commit / force-push / rebase / amend);
    `reopen <pr#>` will re-analyze it against the current diff.
  - **?** — freshness couldn't be determined (offline, not authenticated, the PR was
    deleted, or the cache lacks a head SHA). `reopen` will fetch and decide, or fail loudly.
- Remind them they can also `/interactive-pr-review:cleanup <pr#>` (one PR) or
  `/interactive-pr-review:cleanup` (all) to remove artifacts.

## Notes

- Listing is keyed on `groups.json`. If a PR's `groups.json` was removed but stray
  intermediates remain, it won't appear here — `cleanup` (all) still clears those leftovers.
- `age` is the newest artifact's modification time (how recently it was analyzed/reopened),
  not the PR's own activity. `fresh`/`STALE` is the authoritative signal for whether a
  reopen would re-analyze; it always agrees with what `reopen` itself would decide, because
  both compare the same cached `pr.headSha` against the same live head SHA.

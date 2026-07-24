#!/usr/bin/env python3
"""Merge analyzer output onto the parsed diff to produce the final UI groups JSON.

Pipeline: parse_diff.py -> (analyzer emits analysis-only) -> merge_analysis.py.

The analyzer NEVER emits code. It references hunks by the `hunkId`s that
parse_diff.py assigned, and lines by number. This script does the join with the
byte-exact hunks, so the "diff is sacred" rule is enforced structurally — the model
cannot alter a line it never emitted.

Usage:
    python3 merge_analysis.py <parsed-json> <analysis-json> <out-groups-json> \
        [--repo owner/name --sha <headSha>]   # optional: fetch fullContent via gh

Analysis JSON schema (produced by the analyzer):
{
  "groups": [
    {
      "id": "g1", "title": "…", "reasoning": "…",
      "thingsToConfirm": ["…", "…"],
      "files": [
        {
          "path": "src/x.ts",
          "role": "…", "description": "…",
          "hunkIds": ["src/x.ts#0", "src/x.ts#2"],
          "insights": [
            { "side": "RIGHT", "startLine": 12, "endLine": 14, "kind": "function",
              "level": "notable", "text": "…" }
          ]
        }
      ]
    }
  ]
}

Final groups JSON matches the review UI schema: groups -> files[] with header fields,
role, description, insights, and full `hunks` (byte-exact, from the parsed data).
Invariants enforced here (not merely requested of the model):
  * every referenced hunkId exists (else error);
  * every parsed hunk is assigned to exactly one group (warn on drops / dupes).
"""
import base64
import json
import subprocess
import sys


def die(msg):
    sys.stderr.write("merge_analysis: " + msg + "\n")
    raise SystemExit(1)


def _indent(s):
    return len(s) - len(s.lstrip(" "))


def block_end(linemap, start, cap=600):
    """Given a file's {newLine: text} map, return the true closing line of the code
    block that begins at `start`. Two-phase: (1) find the line that opens the body brace
    (net-open `{` at end of line) — handling multi-line signatures and inline `{}` in
    params/return types — or a braceless statement's terminating `;`; (2) indentation-
    track the body until it returns to the block's base indent. Used to auto-extend an
    insight range whose endLine stops short of the block it describes."""
    if start not in linemap:
        return start
    last = max(linemap)
    base = _indent(linemap[start])
    n = start
    depth = 0
    body_open = None
    while n <= last and n - start < cap:
        if n not in linemap:
            return n - 1
        for ch in linemap[n]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        if depth > 0:
            body_open = n
            break
        if linemap[n].rstrip().endswith(";") and "{" not in linemap[n]:
            return n
        n += 1
    if body_open is None:
        return start
    n = body_open + 1
    end = body_open
    while n <= last and n - start < cap:
        if n not in linemap:
            break
        line = linemap[n]
        if line.strip() == "":
            end = n; n += 1; continue
        ind = _indent(line)
        if ind > base:
            end = n; n += 1; continue
        stripped = line.lstrip()
        if ind <= base and stripped and stripped[0] in "})]":
            end = n
        break
    while end > start and linemap.get(end, "").strip() == "":
        end -= 1
    return end


def fetch_full_content(repo, sha, paths, statuses):
    """Return {path: text or None} via `gh api contents`. Skips binary/removed."""
    cache = {}
    for p in paths:
        if statuses.get(p) in ("binary", "removed"):
            continue
        try:
            raw = subprocess.run(
                ["gh", "api", "-H", "Accept: application/vnd.github+json",
                 "/repos/%s/contents/%s?ref=%s" % (repo, p, sha), "--jq", ".content"],
                capture_output=True, text=True, check=True).stdout.strip()
            cache[p] = base64.b64decode(raw).decode("utf-8", "replace") if raw else None
        except Exception:
            cache[p] = None
    return cache


def main(argv):
    if len(argv) < 4:
        sys.stderr.write(__doc__)
        return 2
    parsed_path, analysis_path, out_path = argv[1], argv[2], argv[3]
    repo = sha = None
    if "--repo" in argv:
        repo = argv[argv.index("--repo") + 1]
    if "--sha" in argv:
        sha = argv[argv.index("--sha") + 1]

    parsed = json.load(open(parsed_path))
    analysis = json.load(open(analysis_path))

    # Index parsed hunks by id, and parsed files by path.
    hunk_by_id = {}
    file_by_path = {}
    for f in parsed["files"]:
        file_by_path[f["path"]] = f
        for h in f["hunks"]:
            hunk_by_id[h["hunkId"]] = h

    assigned = {}   # hunkId -> count of groups that referenced it
    missing = []    # referenced hunkIds that don't exist

    out_groups = []
    for g in analysis.get("groups", []):
        out_files = []
        for af in g.get("files", []):
            path = af["path"]
            pf = file_by_path.get(path)
            if pf is None:
                # Analyzer named a file the parser didn't produce — skip, but note.
                missing.append("(file) " + path)
                continue
            ids = af.get("hunkIds")
            # If no hunkIds given, default to ALL of the file's hunks (single-group file).
            if not ids:
                ids = [h["hunkId"] for h in pf["hunks"]]
            hunks = []
            for hid in ids:
                h = hunk_by_id.get(hid)
                if h is None:
                    missing.append(hid)
                    continue
                assigned[hid] = assigned.get(hid, 0) + 1
                hunks.append({k: h[k] for k in ("header", "oldStart", "newStart", "lines")})
            if not hunks:
                continue
            # Per-group +/- counts for the subset of hunks shown here.
            add = sum(1 for h in hunks for ln in h["lines"] if ln["type"] == "add")
            dele = sum(1 for h in hunks for ln in h["lines"] if ln["type"] == "del")
            # Snap each RIGHT-side insight's endLine to the true end of its block, so a
            # bubble always brackets the whole function/interface/etc even when the
            # analyzer's estimate stopped short. Only extends within this file's shown
            # hunks; never shrinks a range the analyzer gave.
            linemap = {}
            for h in hunks:
                for ln in h["lines"]:
                    if ln["newLine"] is not None:
                        linemap[ln["newLine"]] = ln["text"]
            insights = af.get("insights", [])
            for ins in insights:
                if ins.get("side", "RIGHT") != "RIGHT":
                    continue
                s = ins.get("startLine")
                if s is None:
                    continue
                real_end = block_end(linemap, s)
                if real_end > ins.get("endLine", s):
                    ins["endLine"] = real_end
            out_files.append({
                "path": path,
                "previousPath": pf.get("previousPath"),
                "status": pf.get("status", "modified"),
                "language": pf.get("language"),
                "additions": add,
                "deletions": dele,
                "role": af.get("role"),
                "description": af.get("description", ""),
                "insights": insights,
                "hunks": hunks,
            })
        out_groups.append({
            "id": g.get("id"),
            "title": g.get("title", ""),
            "reasoning": g.get("reasoning", ""),
            "thingsToConfirm": g.get("thingsToConfirm", []),
            "files": out_files,
        })

    if missing:
        die("analysis referenced hunk/file ids not present in the parsed diff: "
            + ", ".join(sorted(set(missing))[:20]))

    # Coverage report: hunks never assigned, or assigned to more than one group.
    all_ids = set(hunk_by_id)
    dropped = sorted(all_ids - set(assigned))
    dup = sorted(hid for hid, c in assigned.items() if c > 1)

    # Optionally embed full file contents for the expand-context feature.
    if repo and sha:
        statuses = {f["path"]: f.get("status") for f in parsed["files"]}
        cache = fetch_full_content(repo, sha, list(file_by_path), statuses)
        for g in out_groups:
            for f in g["files"]:
                c = cache.get(f["path"])
                if c is not None:
                    f["fullContent"] = c

    out = {"pr": parsed.get("pr", {}), "groups": out_groups}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)

    n_files = sum(len(g["files"]) for g in out_groups)
    n_hunks = sum(len(f["hunks"]) for g in out_groups for f in g["files"])
    n_ins = sum(len(f.get("insights", [])) for g in out_groups for f in g["files"])
    msg = "OK groups: %d files: %d hunks: %d insights: %d" % (
        len(out_groups), n_files, n_hunks, n_ins)
    if dropped:
        msg += " | WARNING %d hunk(s) not shown in any group: %s" % (
            len(dropped), ", ".join(dropped[:10]) + (" …" if len(dropped) > 10 else ""))
    if dup:
        msg += " | note %d hunk(s) shown in multiple groups: %s" % (
            len(dup), ", ".join(dup[:10]) + (" …" if len(dup) > 10 else ""))
    print(msg + " -> " + out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

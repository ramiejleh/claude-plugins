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
  "overview": "Concise, holistic paragraph: what this PR achieves in simple terms.",
  "groups": [
    {
      "id": "g1", "title": "…", "reasoning": "…",
      "thingsToConfirm": ["…", "…"],
      "files": [
        {
          "path": "src/x.ts",
          "role": "…", "description": "…",
          "hunkIds": ["src/x.ts#0", "src/x.ts#2"],   # hunks RELEVANT to this group
                                                      # (full file diff is still shown)
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
role, description, insights, and full `hunks` (byte-exact, from the parsed data). A file
touched by several concerns appears in EACH relevant group, and each copy carries the
file's WHOLE diff — hunks the analyzer flagged for that group are `relevant: true`, the
rest are shown as context (`relevant: false`) so a file is never chunked across groups.
Invariants enforced here (not merely requested of the model):
  * every referenced hunkId exists (else error);
  * every parsed hunk is shown in at least one group (swept to "Other changes" else,
    then a hard error if still unshown).
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

    assigned = {}      # hunkId -> count of groups that referenced it
    missing = []       # referenced hunkIds that don't exist
    shown_paths = set()  # file paths actually emitted into some themed group

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
            # `hunkIds` = the hunks RELEVANT to this group's concern. We still show the
            # file's WHOLE diff in every group it appears in (never chunk a file across
            # groups), but flag which hunks are the relevant ones so the UI can focus them.
            relevant_ids = af.get("hunkIds")
            # If none given, the whole file is relevant to this (its only) group.
            if not relevant_ids:
                relevant_ids = [h["hunkId"] for h in pf["hunks"]]
            relevant_set = set(relevant_ids)
            for hid in relevant_ids:
                if hid not in hunk_by_id:
                    missing.append(hid)
            # A referenced-but-unknown hunkId is a hard error (caught below); skip building.
            if any(hid not in hunk_by_id for hid in relevant_ids):
                continue
            # Emit the file's FULL diff (all hunks), tagging each with whether it is
            # relevant to this group. Every shown hunk counts as assigned.
            hunks = []
            for h in pf["hunks"]:
                assigned[h["hunkId"]] = assigned.get(h["hunkId"], 0) + 1
                entry = {k: h[k] for k in ("header", "oldStart", "newStart", "lines")}
                entry["relevant"] = h["hunkId"] in relevant_set
                hunks.append(entry)
            # +/- counts over the whole file's diff (matching what is shown).
            add = sum(1 for h in hunks for ln in h["lines"] if ln["type"] == "add")
            dele = sum(1 for h in hunks for ln in h["lines"] if ln["type"] == "del")
            # Focus note: only when the file has BOTH relevant and non-relevant hunks, so a
            # single-concern file shows nothing extra. Lists the relevant hunks' line ranges.
            focus_note = None
            if hunks and any(h["relevant"] for h in hunks) and not all(h["relevant"] for h in hunks):
                spans = []
                for h in hunks:
                    if not h["relevant"]:
                        continue
                    nums = [ln["newLine"] for ln in h["lines"] if ln["newLine"] is not None] \
                        or [ln["oldLine"] for ln in h["lines"] if ln["oldLine"] is not None]
                    if nums:
                        lo, hi = min(nums), max(nums)
                        spans.append(str(lo) if lo == hi else "%d–%d" % (lo, hi))
                focus_note = ", ".join(spans) if spans else None
            # Snap each RIGHT-side insight's endLine to the true end of its block, so a
            # bubble always brackets the whole function/interface/etc even when the
            # analyzer's estimate stopped short. Built from the file's full shown diff;
            # never shrinks a range the analyzer gave.
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
                "focusNote": focus_note,
                "insights": insights,
                "hunks": hunks,
            })
            shown_paths.add(path)
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

    # --- Coverage guarantee: EVERY file/hunk must appear somewhere in the UI. ---
    # The analysis may omit hunks (LLM oversight) or a file may have no hunks at all
    # (binary, pure rename, mode change) and never be placed. Rather than silently
    # dropping those, sweep everything not yet shown into a synthetic "Other changes"
    # group, in the parser's original order. This is deterministic — it does not rely on
    # the model choosing to include a file.
    catch_files = []
    for pf in parsed["files"]:                       # parser order = diff order
        placed = {h["hunkId"] for h in pf["hunks"] if h["hunkId"] in assigned}
        leftover = [h for h in pf["hunks"] if h["hunkId"] not in assigned]
        # A file with hunks, some/all unassigned -> show the leftover hunks here.
        # A file with NO hunks that was never surfaced in any group -> show it too.
        shown_elsewhere = pf["path"] in shown_paths
        if not leftover and (pf["hunks"] or shown_elsewhere):
            continue
        hunks = [dict({k: h[k] for k in ("header", "oldStart", "newStart", "lines")},
                      relevant=True) for h in leftover]
        for h in leftover:
            assigned[h["hunkId"]] = assigned.get(h["hunkId"], 0) + 1
        add = sum(1 for h in hunks for ln in h["lines"] if ln["type"] == "add")
        dele = sum(1 for h in hunks for ln in h["lines"] if ln["type"] == "del")
        catch_files.append({
            "path": pf["path"],
            "previousPath": pf.get("previousPath"),
            "status": pf.get("status", "modified"),
            "language": pf.get("language"),
            "additions": add,
            "deletions": dele,
            "role": None,
            "description": "Not assigned to a themed group above; included here so the "
                           "review always covers every changed file.",
            "focusNote": None,
            "insights": [],
            "hunks": hunks,
        })
    if catch_files:
        out_groups.append({
            "id": "g-other",
            "title": "Other changes",
            "reasoning": "Files and hunks not sorted into a themed group above, collected "
                         "here so nothing in the diff is hidden from review.",
            "thingsToConfirm": [],
            "files": catch_files,
        })

    # Hard invariant: after the sweep, every parsed hunk must be shown somewhere.
    all_ids = set(hunk_by_id)
    still_missing = sorted(all_ids - set(assigned))
    if still_missing:
        die("internal error: %d hunk(s) not shown in any group after catch-all sweep: %s"
            % (len(still_missing), ", ".join(still_missing[:10])))
    # Files with zero hunks (binary/rename) must also each appear at least once.
    shown_now = {f["path"] for g in out_groups for f in g["files"]}
    unshown_files = [pf["path"] for pf in parsed["files"] if pf["path"] not in shown_now]
    if unshown_files:
        die("internal error: %d file(s) not shown in any group: %s"
            % (len(unshown_files), ", ".join(unshown_files[:10])))

    # Report: files shown in more than one group (informational, allowed by design — a
    # file touched by several concerns appears in each, with its full diff every time).
    path_groups = {}
    for g in out_groups:
        for f in g["files"]:
            path_groups[f["path"]] = path_groups.get(f["path"], 0) + 1
    dup = sorted(p for p, c in path_groups.items() if c > 1)

    # Optionally embed full file contents for the expand-context feature.
    if repo and sha:
        statuses = {f["path"]: f.get("status") for f in parsed["files"]}
        cache = fetch_full_content(repo, sha, list(file_by_path), statuses)
        for g in out_groups:
            for f in g["files"]:
                c = cache.get(f["path"])
                if c is not None:
                    f["fullContent"] = c

    out = {"pr": parsed.get("pr", {}),
           "overview": analysis.get("overview", ""),
           "groups": out_groups}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)

    n_files = sum(len(g["files"]) for g in out_groups)
    n_hunks = sum(len(f["hunks"]) for g in out_groups for f in g["files"])
    n_ins = sum(len(f.get("insights", [])) for g in out_groups for f in g["files"])
    msg = "OK groups: %d files: %d hunks: %d insights: %d" % (
        len(out_groups), n_files, n_hunks, n_ins)
    if catch_files:
        msg += " | swept %d unassigned file(s) into 'Other changes': %s" % (
            len(catch_files),
            ", ".join(f["path"] for f in catch_files[:10])
            + (" …" if len(catch_files) > 10 else ""))
    if dup:
        msg += " | note %d file(s) shown in multiple groups (full diff each): %s" % (
            len(dup), ", ".join(dup[:10]) + (" …" if len(dup) > 10 else ""))
    print(msg + " -> " + out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""Assemble per-group analysis fragments into a single analysis.json.

Pipeline: parse_diff.py -> (analyzer writes fragments) -> assemble_analysis.py -> merge_analysis.py.

Stage B (the analysis) is authored by the model. On a large PR, writing the whole
analysis as one response can be dropped mid-stream by the API, losing everything. So
the analyzer instead writes many SMALL, self-contained fragment files into a directory,
one per group (plus one for the overview), each in its own bounded write. This script
stitches them back together deterministically. A dropped response then costs only the
one fragment it was writing — re-write that file and re-run assemble.

Usage:
    python3 assemble_analysis.py <fragment-dir> <out-analysis-json>

Fragment directory (files read in sorted filename order — the on-screen group order):
    00-overview.json   {"overview": "…"}
    01-g1.json         { "id":"g1", "title":"…", "reasoning":"…",
                         "thingsToConfirm":[…], "files":[…] }
    02-g2.json         { …one group object… }
    …

Each fragment is one JSON object. Accepted shapes:
  * {"overview": "…"}                 -> sets the overview
  * {"groups": [ {…}, {…} ]}          -> extends the group list
  * a bare group object (has "files"/"id"/"title", no "groups") -> appended as one group
A file may combine keys (e.g. overview + groups); each recognized key is applied.

Output matches the schema merge_analysis.py consumes:  {"overview": str, "groups": [ … ]}
So merge_analysis.py needs no knowledge of fragments.
"""
import glob
import json
import os
import sys


def die(msg):
    sys.stderr.write("assemble_analysis: " + msg + "\n")
    raise SystemExit(1)


def _looks_like_group(obj):
    return isinstance(obj, dict) and any(k in obj for k in ("files", "id", "title"))


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    frag_dir, out_path = argv[1], argv[2]

    if not os.path.isdir(frag_dir):
        die("fragment directory not found: " + frag_dir)

    frags = sorted(glob.glob(os.path.join(frag_dir, "*.json")))
    if not frags:
        die("no .json fragments in " + frag_dir)

    overview = ""
    has_overview = False
    groups = []
    for path in frags:
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except (ValueError, OSError) as e:
            # A truncated/invalid fragment lands here — name it so only it is re-written.
            die("could not parse fragment %s: %s" % (path, e))

        if not isinstance(obj, dict):
            die("fragment %s is not a JSON object" % path)

        applied = False
        if "overview" in obj:
            overview = obj["overview"] or ""
            has_overview = True
            applied = True
        if "groups" in obj:
            if not isinstance(obj["groups"], list):
                die("fragment %s has a non-list 'groups'" % path)
            groups.extend(obj["groups"])
            applied = True
        if not applied and _looks_like_group(obj):
            groups.append(obj)
            applied = True
        if not applied:
            die("fragment %s has no 'overview', 'groups', or group fields" % path)

    if not groups:
        die("no groups found across %d fragment(s) in %s" % (len(frags), frag_dir))

    out = {"overview": overview, "groups": groups}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)

    msg = "OK assembled: overview=%s groups=%d from %d fragment(s)" % (
        "yes" if has_overview else "no", len(groups), len(frags))
    if not has_overview:
        msg += " | WARNING no overview fragment found"
    print(msg + " -> " + out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

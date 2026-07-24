#!/usr/bin/env python3
"""Parse a unified diff into a canonical, byte-exact JSON structure.

This is the deterministic half of the PR-review pipeline. The diff NEVER passes
through the language model: this script owns it. The analyzer only references the
hunks this script produces (by `hunkId`), and merge_analysis.py joins the two.

Usage:
    python3 parse_diff.py <diff-path> <out-json-path> [--pr-json <pr-meta-json>]

Output schema (parsed.json):
{
  "pr": { ...optional metadata passed via --pr-json... },
  "files": [
    {
      "path": "src/x.ts",
      "previousPath": null,
      "status": "modified",         # added | modified | removed | renamed | binary
      "language": "typescript",     # highlight.js name, or null
      "additions": 3,
      "deletions": 1,
      "hunks": [
        {
          "hunkId": "src/x.ts#0",   # stable: "<path>#<index-within-file>"
          "header": "@@ -12,7 +12,9 @@ ...",
          "oldStart": 12, "newStart": 12,
          "lines": [
            { "type": "context", "oldLine": 12, "newLine": 12, "text": "…" },
            { "type": "add",     "oldLine": null, "newLine": 13, "text": "…" },
            { "type": "del",     "oldLine": 13, "newLine": null, "text": "…" }
          ]
        }
      ]
    }
  ]
}
"""
import json
import re
import sys

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

EXT_LANG = {
    "ts": "typescript", "tsx": "typescript", "js": "javascript", "jsx": "javascript",
    "mjs": "javascript", "cjs": "javascript", "json": "json", "yml": "yaml",
    "yaml": "yaml", "sh": "bash", "bash": "bash", "md": "markdown", "py": "python",
    "rb": "ruby", "go": "go", "rs": "rust", "java": "java", "kt": "kotlin",
    "css": "css", "scss": "scss", "less": "less", "html": "xml", "xml": "xml",
    "sql": "sql", "php": "php", "swift": "swift", "c": "c", "h": "c", "cpp": "cpp",
    "cc": "cpp", "hpp": "cpp", "cs": "csharp", "toml": "ini", "ini": "ini",
    "env": "bash", "example": "bash", "dockerfile": "dockerfile", "gradle": "groovy",
}

# Filenames (basename, lowercased) that map to a language regardless of extension.
NAME_LANG = {
    ".env.example": "bash", "dockerfile": "dockerfile", "makefile": "makefile",
}


def lang_for(path):
    base = (path or "").rsplit("/", 1)[-1].lower()
    if base in NAME_LANG:
        return NAME_LANG[base]
    m = re.search(r"\.([A-Za-z0-9]+)$", path or "")
    return EXT_LANG.get(m.group(1).lower()) if m else None


def new_file():
    return {
        "path": None, "previousPath": None, "status": "modified",
        "language": None, "additions": 0, "deletions": 0, "hunks": [],
    }


def parse(diff_text):
    files = []
    f = None            # current file dict
    hunk = None         # current hunk dict
    old_line = new_line = 0

    def close_hunk():
        nonlocal hunk
        if f is not None and hunk is not None:
            hunk["hunkId"] = "%s#%d" % (f["path"], len(f["hunks"]))
            f["hunks"].append(hunk)
        hunk = None

    def close_file():
        nonlocal f
        close_hunk()
        if f is not None and f["path"] is not None:
            files.append(f)
        f = None

    for line in diff_text.split("\n"):
        # New file section.
        if line.startswith("diff --git "):
            close_file()
            f = new_file()
            # "diff --git a/OLD b/NEW" — capture both; NEW is authoritative.
            m = re.match(r"^diff --git a/(.*) b/(.*)$", line)
            if m:
                f["previousPath"] = m.group(1)
                f["path"] = m.group(2)
            continue
        if f is None:
            continue

        # File-level metadata lines (before hunks).
        if line.startswith("new file mode"):
            f["status"] = "added"; continue
        if line.startswith("deleted file mode"):
            f["status"] = "removed"; continue
        if line.startswith("rename from "):
            f["previousPath"] = line[len("rename from "):]; f["status"] = "renamed"; continue
        if line.startswith("rename to "):
            f["path"] = line[len("rename to "):]; f["status"] = "renamed"; continue
        if line.startswith("copy from ") or line.startswith("copy to "):
            f["status"] = "renamed"; continue
        if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            f["status"] = "binary"; continue
        if line.startswith("--- "):
            # /dev/null on the old side => added
            if line == "--- /dev/null":
                f["status"] = "added"
            continue
        if line.startswith("+++ "):
            if line == "+++ /dev/null":
                f["status"] = "removed"
            else:
                # authoritative new path: "+++ b/NEW"
                p = line[4:]
                if p.startswith("b/"):
                    p = p[2:]
                if p and p != "/dev/null":
                    f["path"] = p
            continue
        if line.startswith("index ") or line.startswith("old mode ") or \
           line.startswith("new mode ") or line.startswith("similarity index") or \
           line.startswith("dissimilarity index"):
            continue

        # Hunk header.
        m = HUNK_RE.match(line)
        if m:
            close_hunk()
            old_start = int(m.group(1))
            new_start = int(m.group(3))
            old_line, new_line = old_start, new_start
            hunk = {
                "hunkId": None, "header": line,
                "oldStart": old_start, "newStart": new_start, "lines": [],
            }
            continue

        # Diff body lines (only meaningful inside a hunk).
        if hunk is None:
            continue
        if line.startswith("\\"):
            # "\ No newline at end of file" — ignore for rendering.
            continue
        tag, text = (line[0], line[1:]) if line else (" ", "")
        if tag == "+":
            hunk["lines"].append({"type": "add", "oldLine": None, "newLine": new_line, "text": text})
            new_line += 1
            f["additions"] += 1
        elif tag == "-":
            hunk["lines"].append({"type": "del", "oldLine": old_line, "newLine": None, "text": text})
            old_line += 1
            f["deletions"] += 1
        else:
            # Context (space) or an empty line in the diff body.
            hunk["lines"].append({"type": "context", "oldLine": old_line, "newLine": new_line, "text": text})
            old_line += 1
            new_line += 1

    close_file()

    for fl in files:
        if fl["language"] is None:
            fl["language"] = lang_for(fl["path"])
        if fl["previousPath"] == fl["path"]:
            fl["previousPath"] = None
    return files


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    diff_path, out_path = argv[1], argv[2]
    pr = {}
    if "--pr-json" in argv:
        pr_path = argv[argv.index("--pr-json") + 1]
        pr = json.load(open(pr_path))

    diff_text = open(diff_path, "r", encoding="utf-8", errors="replace").read()
    files = parse(diff_text)
    out = {"pr": pr, "files": files}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)

    n_hunks = sum(len(f["hunks"]) for f in files)
    n_lines = sum(len(h["lines"]) for f in files for h in f["hunks"])
    print("OK parsed files: %d hunks: %d lines: %d -> %s"
          % (len(files), n_hunks, n_lines, out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

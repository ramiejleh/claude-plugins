#!/usr/bin/env python3
"""Turn a walkthrough.json into a self-contained HTML walkthrough.

Claude authors the JSON: which files, which line ranges, what each bubble says.
This script supplies the code itself, read from disk at render time. That split
is deliberate — transcribed source can drift from the real file or be invented
outright, and the filesystem already holds the authoritative bytes.

Everything is inlined, so the result opens with no server, no toolchain and no
network.
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from validate import validate  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# Gaps shorter than this are shown rather than collapsed.
GAP_MIN = 4

# Extension -> highlight.js language. Anything unmapped renders unhighlighted,
# which is a cosmetic loss rather than a failure.
LANGUAGES = {
    "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
    "ts": "typescript", "tsx": "typescript", "mts": "typescript", "cts": "typescript",
    "py": "python", "rb": "ruby", "go": "go", "rs": "rust", "java": "java",
    "kt": "kotlin", "kts": "kotlin", "swift": "swift", "c": "c", "h": "c",
    "cpp": "cpp", "cc": "cpp", "cxx": "cpp", "hpp": "cpp", "cs": "csharp",
    "php": "php", "pl": "perl", "pm": "perl", "lua": "lua", "r": "r",
    "sh": "bash", "bash": "bash", "zsh": "bash", "fish": "bash",
    "sql": "sql", "css": "css", "scss": "scss", "less": "less",
    "html": "xml", "htm": "xml", "xml": "xml", "svg": "xml", "vue": "xml",
    "json": "json", "jsonc": "json", "yml": "yaml", "yaml": "yaml",
    "toml": "ini", "ini": "ini", "cfg": "ini", "md": "markdown",
    "mm": "objectivec", "m": "objectivec", "diff": "diff", "patch": "diff",
    "graphql": "graphql", "gql": "graphql", "makefile": "makefile",
}
BASENAME_LANGUAGES = {
    "Makefile": "makefile", "Dockerfile": "bash", "Gemfile": "ruby", "Rakefile": "ruby",
}


def language_for(path: Path) -> str:
    if path.name in BASENAME_LANGUAGES:
        return BASENAME_LANGUAGES[path.name]
    return LANGUAGES.get(path.suffix.lstrip(".").lower(), "plaintext")


def git_commit(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def build_file_view(rel_path: str, entries: list[dict], root: Path) -> dict:
    """One continuous view of a file, with every region this step highlights.

    Several regions of the same file belong in one view, not one card each — a
    step that shows the same file twice stops reading like a file and starts
    reading like a slide deck. Regions close together merge; the stretches
    between distant ones collapse into a gap the reader can open.
    """
    path = (root / rel_path).resolve()
    # The validator already rejects paths outside the root, but re-checking here
    # keeps the guarantee inside the function that does the reading rather than
    # resting on a caller in another module having run first.
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"excerpt path escapes the project root: {rel_path}")

    all_lines = path.read_text(errors="ignore").splitlines()
    total = len(all_lines)

    windows = []
    for entry in entries:
        start, end = entry["focus"]
        context = entry.get("context", 5)
        windows.append({
            "start": max(1, start - context),
            "end": min(total, end + context),
            "region": {"focus": [start, end], "bubble": entry["bubble"]},
        })
    windows.sort(key=lambda w: (w["start"], w["end"]))

    merged: list[dict] = []
    for window in windows:
        # A gap shorter than GAP_MIN is not worth hiding — "3 lines hidden"
        # costs the reader more than the three lines would have.
        if merged and window["start"] <= merged[-1]["end"] + GAP_MIN:
            merged[-1]["end"] = max(merged[-1]["end"], window["end"])
            merged[-1]["regions"].append(window["region"])
        else:
            merged.append({
                "start": window["start"],
                "end": window["end"],
                "regions": [window["region"]],
            })

    blocks: list[dict] = []
    previous_end = None
    for block in merged:
        if previous_end is not None and block["start"] > previous_end + 1:
            first, last = previous_end + 1, block["start"] - 1
            blocks.append({
                "kind": "gap",
                "first": first,
                "count": last - first + 1,
                "lines": all_lines[first - 1 : last],
            })
        blocks.append({
            "kind": "code",
            "first": block["start"],
            "lines": all_lines[block["start"] - 1 : block["end"]],
            "regions": block["regions"],
        })
        previous_end = block["end"]

    return {
        "path": rel_path,
        "abs": str(path),
        "lang": language_for(path),
        "span": [merged[0]["start"], merged[-1]["end"]] if merged else [0, 0],
        "blocks": blocks,
    }


def build_step(step: dict, root: Path) -> dict:
    """Group a step's excerpts by file, keeping the author's file order."""
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for excerpt in step["excerpts"]:
        path = excerpt["path"]
        if path not in grouped:
            grouped[path] = []
            order.append(path)
        grouped[path].append(excerpt)

    return {
        "title": step["title"],
        "note": step.get("note", ""),
        "files": [build_file_view(path, grouped[path], root) for path in order],
    }


def build_payload(doc: dict, root: Path) -> dict:
    return {
        "id": doc["id"],
        "title": doc["title"],
        "subtitle": doc.get("subtitle", ""),
        "root": str(root),
        "commit": doc.get("commit") or git_commit(root),
        "generated_at": doc.get("generated_at")
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": [build_step(step, root) for step in doc["steps"]],
    }


def render(payload: dict) -> str:
    template = (ASSETS / "template.html").read_text()
    # `</` inside a script block would close it early; the escape is invisible
    # to JSON.parse but keeps the document well-formed.
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    # The title is the one value substituted into markup rather than into the
    # JSON payload, so it is the one that has to be escaped. Everything else
    # reaches the page through JSON.parse and textContent.
    return (
        template.replace("__TITLE__", html.escape(payload["title"], quote=True))
        .replace("/*__CSS__*/", (ASSETS / "ui.css").read_text())
        .replace("/*__HLJS__*/", (ASSETS / "vendor" / "highlight.min.js").read_text())
        .replace("/*__PAYLOAD__*/", f"window.WALKTHROUGH = {blob};")
        .replace("/*__JS__*/", (ASSETS / "ui.js").read_text())
    )


def ensure_gitignored(root: Path, folder: str) -> None:
    """Keep generated walkthroughs out of version control without a fuss."""
    gitignore = root / ".gitignore"
    entry = f"{folder}/"
    try:
        existing = gitignore.read_text() if gitignore.exists() else ""
        if entry in existing.split():
            return
        prefix = "" if existing.endswith("\n") or not existing else "\n"
        gitignore.write_text(f"{existing}{prefix}\n# walkthrough output\n{entry}\n")
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a walkthrough to HTML")
    parser.add_argument("json_path")
    parser.add_argument("--out", help="output path (default <root>/.walkthroughs/<id>.html)")
    parser.add_argument("--no-gitignore", action="store_true")
    args = parser.parse_args()

    try:
        doc = json.loads(Path(args.json_path).read_text())
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.json_path}: {exc}", file=sys.stderr)
        return 2

    root = Path(doc.get("root") or Path(args.json_path).parent).resolve()
    errors, warnings = validate(doc, root)
    for warning in warnings:
        print(f"warning: {warning}")
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        print(f"\n{len(errors)} error(s) — nothing rendered.", file=sys.stderr)
        return 1

    payload = build_payload(doc, root)

    if args.out:
        out = Path(args.out)
    else:
        folder = (root / ".walkthroughs").resolve()
        out = (folder / f"{payload['id']}.html").resolve()
        try:
            out.relative_to(folder)
        except ValueError:
            print(f"error: id {payload['id']!r} escapes the output directory", file=sys.stderr)
            return 1
        if not args.no_gitignore:
            ensure_gitignored(root, ".walkthroughs")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload))

    steps = len(payload["steps"])
    regions = sum(
        len(b.get("regions", []))
        for s in payload["steps"] for f in s["files"] for b in f["blocks"]
    )
    files = sum(len(s["files"]) for s in payload["steps"])
    print(f"{out}")
    print(f"{steps} steps, {files} file views, {regions} highlighted regions, "
          f"{len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

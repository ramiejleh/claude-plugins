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
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from validate import validate  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"

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


def build_excerpt(excerpt: dict, root: Path) -> dict:
    """Pull the real lines around the focus range, with context either side."""
    path = (root / excerpt["path"]).resolve()
    text = path.read_text(errors="ignore")
    all_lines = text.splitlines()
    start, end = excerpt["focus"]
    context = excerpt.get("context", 5)

    first = max(1, start - context)
    last = min(len(all_lines), end + context)

    return {
        "path": excerpt["path"],
        "abs": str(path),
        "lang": language_for(path),
        "first": first,
        "focus": [start, end],
        "lines": all_lines[first - 1 : last],
        "bubble": excerpt["bubble"],
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
        "steps": [
            {
                "title": step["title"],
                "note": step.get("note", ""),
                "excerpts": [build_excerpt(x, root) for x in step["excerpts"]],
            }
            for step in doc["steps"]
        ],
    }


def render(payload: dict) -> str:
    template = (ASSETS / "template.html").read_text()
    # `</` inside a script block would close it early; the escape is invisible
    # to JSON.parse but keeps the document well-formed.
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return (
        template.replace("__TITLE__", payload["title"])
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
        out = root / ".walkthroughs" / f"{payload['id']}.html"
        if not args.no_gitignore:
            ensure_gitignored(root, ".walkthroughs")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload))

    steps = len(payload["steps"])
    excerpts = sum(len(s["excerpts"]) for s in payload["steps"])
    print(f"{out}")
    print(f"{steps} steps, {excerpts} excerpts, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""The catalogue of walkthroughs in a project, and whether they still hold.

Every render records which files it cited and a hash of each. That manifest is
what makes staleness answerable later: if a cited file has changed, the line
numbers in that walkthrough may no longer point where they did, and a walkthrough
pointing at the wrong lines is worse than none — it teaches a wrong map.

The reader page cannot check this itself. It is a `file://` document with no
network and no filesystem access, so freshness has to be answered here, by a
script that can actually read the tree.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

FOLDER = ".walkthroughs"
ASSETS = Path(__file__).resolve().parent.parent / "assets"


def folder_for(root: Path) -> Path:
    return root / FOLDER


def index_path(root: Path) -> Path:
    return folder_for(root) / "index.json"


def hash_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def load(root: Path) -> dict:
    try:
        data = json.loads(index_path(root).read_text())
        if isinstance(data, dict) and isinstance(data.get("walkthroughs"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"walkthroughs": {}}


def save(root: Path, catalogue: dict) -> None:
    folder_for(root).mkdir(parents=True, exist_ok=True)
    index_path(root).write_text(json.dumps(catalogue, indent=2) + "\n")


def manifest_for(doc: dict, root: Path) -> dict:
    """path -> content hash, for every file the walkthrough cites."""
    paths = {
        excerpt["path"]
        for step in doc.get("steps", [])
        for excerpt in step.get("excerpts", [])
    }
    return {path: hash_file(root / path) for path in sorted(paths)}


def record(root: Path, payload: dict, doc: dict) -> None:
    """Add or replace this walkthrough's catalogue entry."""
    catalogue = load(root)
    catalogue["walkthroughs"][payload["id"]] = {
        "id": payload["id"],
        "title": payload["title"],
        "subtitle": payload.get("subtitle", ""),
        "steps": len(payload["steps"]),
        "files": sum(len(s["files"]) for s in payload["steps"]),
        "regions": sum(
            len(b.get("regions", []))
            for s in payload["steps"] for f in s["files"] for b in f["blocks"]
        ),
        "commit": payload.get("commit", ""),
        "generated_at": payload.get("generated_at", ""),
        "sources": manifest_for(doc, root),
    }
    save(root, catalogue)


def staleness(root: Path, entry: dict) -> list[str]:
    """Which cited files have moved on since this was built."""
    drifted = []
    for path, digest in (entry.get("sources") or {}).items():
        target = root / path
        if not target.exists():
            drifted.append(f"{path} (gone)")
        elif hash_file(target) != digest:
            drifted.append(path)
    return drifted


def survey(root: Path) -> list[tuple[dict, list[str]]]:
    catalogue = load(root)
    rows = []
    for entry in catalogue["walkthroughs"].values():
        rows.append((entry, staleness(root, entry)))
    rows.sort(key=lambda r: r[0].get("generated_at", ""), reverse=True)
    return rows


# --------------------------------------------------------------------------
# index page
# --------------------------------------------------------------------------


def write_index_html(root: Path) -> Path:
    rows = survey(root)
    template = (ASSETS / "index-template.html").read_text()

    if not rows:
        cards = '<p class="empty">No walkthroughs yet. Run <code>/walkthrough &lt;target&gt;</code>.</p>'
    else:
        cards = "\n".join(
            f"""<a class="card{' is-stale' if drift else ''}" href="{html.escape(entry['id'])}.html">
  <h2>{html.escape(entry['title'])}</h2>
  <p class="sub">{html.escape(entry.get('subtitle', ''))}</p>
  <p class="meta">
    <span>{entry['steps']} steps</span>
    <span>{entry['regions']} highlighted</span>
    {f"<span>at {html.escape(entry['commit'])}</span>" if entry.get('commit') else ""}
    <span>{html.escape((entry.get('generated_at') or '')[:10])}</span>
  </p>
  {_drift_markup(drift)}
</a>"""
            for entry, drift in rows
        )

    fresh = sum(1 for _, drift in rows if not drift)
    summary = (
        f"{len(rows)} walkthrough{'' if len(rows) == 1 else 's'} · "
        f"{fresh} still matching the tree"
        if rows else ""
    )

    page = (
        template.replace("/*__TOKENS__*/", (ASSETS / "tokens.css").read_text())
        .replace("__CARDS__", cards)
        .replace("__SUMMARY__", html.escape(summary))
        .replace("__CHECKED__", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    )
    out = folder_for(root) / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out


def _drift_markup(drift: list[str]) -> str:
    if not drift:
        return ""
    shown = ", ".join(html.escape(p) for p in drift[:3])
    more = f" +{len(drift) - 3} more" if len(drift) > 3 else ""
    return f'<p class="drift">changed since built: {shown}{more}</p>'


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def cmd_list(root: Path) -> int:
    rows = survey(root)
    if not rows:
        print(f"No walkthroughs in {folder_for(root)}")
        return 0

    for entry, drift in rows:
        flag = "STALE " if drift else "fresh "
        print(f"{flag} {entry['id']:<20} {entry['title']}")
        print(
            f"        {entry['steps']} steps · {entry['regions']} highlighted · "
            f"{(entry.get('generated_at') or '')[:10]}"
            + (f" · built at {entry['commit']}" if entry.get("commit") else "")
        )
        for path in drift[:5]:
            print(f"        changed: {path}")
        if len(drift) > 5:
            print(f"        ... and {len(drift) - 5} more")

    index = write_index_html(root)
    stale = sum(1 for _, drift in rows if drift)
    print(f"\n{len(rows)} total, {stale} stale")
    print(f"index: {index}")
    if stale:
        print(
            "\nStale means a cited file changed, so its line numbers may no longer point\n"
            "where they did. Regenerate those rather than re-rendering — the excerpts\n"
            "themselves need re-tracing, not just redrawing."
        )
    return 0


def cmd_check(root: Path, wid: str) -> int:
    entry = load(root)["walkthroughs"].get(wid)
    if not entry:
        print(f"no walkthrough with id {wid!r}", file=sys.stderr)
        return 1
    drift = staleness(root, entry)
    if not drift:
        print(f"{wid}: fresh — every cited file matches what it was built from")
        return 0
    print(f"{wid}: STALE — {len(drift)} cited file(s) changed")
    for path in drift:
        print(f"  {path}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Walkthrough catalogue")
    parser.add_argument("command", choices=["list", "check", "index"])
    parser.add_argument("id", nargs="?")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if args.command == "list":
        return cmd_list(root)
    if args.command == "index":
        print(write_index_html(root))
        return 0
    if not args.id:
        print("check needs a walkthrough id", file=sys.stderr)
        return 2
    return cmd_check(root, args.id)


if __name__ == "__main__":
    sys.exit(main())

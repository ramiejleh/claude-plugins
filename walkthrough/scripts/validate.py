#!/usr/bin/env python3
"""Check a walkthrough.json against the contract before it is rendered.

Errors block rendering. Warnings do not, but every one of them is a sign the
walkthrough is drifting toward being a summary rather than a guided read, so
they are worth acting on.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MAX_EXCERPT_LINES = 40
MAX_BUBBLE_WORDS = 60
MAX_TITLE_WORDS = 12

# `id` becomes a filename, so it has to be a plain slug. Dots are excluded
# outright rather than filtered, which removes any question of "..".
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _is_text(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\0" not in handle.read(8192)
    except OSError:
        return False


def validate(doc: dict, root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(doc, dict):
        return ["top level must be an object"], []

    for field in ("id", "title", "steps"):
        if not doc.get(field):
            errors.append(f"missing required field: {field}")
    if errors:
        return errors, warnings

    if not ID_PATTERN.match(str(doc["id"])):
        errors.append(
            f"id {doc['id']!r} is not a plain slug — letters, digits, hyphen and "
            f"underscore only. It is used as a filename."
        )

    if not root.is_dir():
        errors.append(f"root is not a directory: {root}")
        return errors, warnings

    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty array")
        return errors, warnings

    for index, step in enumerate(steps, start=1):
        where = f"step {index}"
        if not isinstance(step, dict):
            errors.append(f"{where}: must be an object")
            continue

        title = step.get("title")
        if not title or not isinstance(title, str):
            errors.append(f"{where}: needs a title")
        elif len(title.split()) > MAX_TITLE_WORDS:
            # A long title is nearly always one that explains instead of
            # orienting, which leaks the answer and kills the hidden bubble.
            warnings.append(
                f"{where}: title is {len(title.split())} words — titles should point at "
                f"the code, not explain it. That is the bubble's job."
            )

        excerpts = step.get("excerpts")
        if not isinstance(excerpts, list) or not excerpts:
            errors.append(f"{where}: needs at least one excerpt — a step with no code is not a step")
            continue

        for spot, excerpt in enumerate(excerpts, start=1):
            tag = f"{where}, excerpt {spot}"
            if not isinstance(excerpt, dict):
                errors.append(f"{tag}: must be an object")
                continue

            raw_path = excerpt.get("path")
            if not raw_path or not isinstance(raw_path, str):
                errors.append(f"{tag}: needs a path")
                continue

            target = (root / raw_path).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{tag}: path escapes the project root: {raw_path}")
                continue
            if not target.is_file():
                errors.append(f"{tag}: file not found: {raw_path}")
                continue
            if not _is_text(target):
                errors.append(f"{tag}: not a text file: {raw_path}")
                continue

            focus = excerpt.get("focus")
            if (
                not isinstance(focus, list)
                or len(focus) != 2
                or not all(isinstance(n, int) for n in focus)
            ):
                errors.append(f"{tag}: focus must be [startLine, endLine]")
                continue
            start, end = focus
            total = len(target.read_text(errors="ignore").splitlines())
            if start < 1 or end < start:
                errors.append(f"{tag}: focus {focus} is not a valid range")
                continue
            if end > total:
                errors.append(f"{tag}: focus {focus} runs past the end of {raw_path} ({total} lines)")
                continue

            context = excerpt.get("context", 5)
            if not isinstance(context, int) or context < 0:
                errors.append(f"{tag}: context must be a non-negative integer")
                continue

            shown = min(total, end + context) - max(1, start - context) + 1
            if shown > MAX_EXCERPT_LINES:
                warnings.append(
                    f"{tag}: {shown} lines shown — over {MAX_EXCERPT_LINES}. Split the step; "
                    f"a page that needs scrolling stops reading like a page."
                )

            bubble = excerpt.get("bubble")
            if not bubble or not isinstance(bubble, str):
                errors.append(f"{tag}: needs a bubble — the hidden explanation is the point")
            elif len(bubble.split()) > MAX_BUBBLE_WORDS:
                warnings.append(
                    f"{tag}: bubble is {len(bubble.split())} words — over {MAX_BUBBLE_WORDS}. "
                    f"A bubble that long is carrying a whole step; split it."
                )

    return errors, warnings


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: validate.py <walkthrough.json>", file=sys.stderr)
        return 2
    doc_path = Path(sys.argv[1])
    try:
        doc = json.loads(doc_path.read_text())
    except (OSError, ValueError) as exc:
        print(f"cannot read {doc_path}: {exc}", file=sys.stderr)
        return 2

    root = Path(doc.get("root") or doc_path.parent)
    errors, warnings = validate(doc, root)

    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}", file=sys.stderr)

    if errors:
        print(f"\n{len(errors)} error(s) — not renderable.", file=sys.stderr)
        return 1
    print(f"valid: {len(doc['steps'])} steps, {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

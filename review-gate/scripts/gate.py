#!/usr/bin/env python3
"""review-gate — force periodic human review of Claude-generated code.

Claude Code runs this as a hook. It measures how much unreviewed code has piled
up in the current project and, once that crosses the configured threshold, denies
every further write until a human has actually looked at it.

Two things have to happen before writing resumes, every time:

  1. markers — a comment line carrying a random token is planted in each of the
               files that changed most. They clear when every token is gone from
               disk. The script verifies this by reading the files, and while a
               gate is armed Claude's write tools and shell are blocked, so
               Claude cannot remove them itself.
  2. quiz    — Claude poses a comprehension question about the changes and the
               human answers it. The question must be registered before the
               markers clear, so it cannot be retro-fitted to something the user
               already said.

Counting is per *project*, not per session, and survives across conversations:
unreviewed code accumulates the way the risk does, and starting a fresh session
does not wipe the slate. Where the project is a git repo the authoritative number
comes from the working tree rather than from a tally of tool calls, so code
written by a subagent or a shell heredoc counts too.

Subcommands split into hook entry points (fed JSON on stdin by Claude Code) and a
small CLI used by the slash commands and by Claude during a gate.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("REVIEW_GATE_HOME") or (Path.home() / ".claude" / "review-gate"))
STATE_DIR = HOME / "state"
CONFIG_PATH = HOME / "config.json"
LOG_PATH = HOME / "log.jsonl"

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Marking a plan item done is a finer completion boundary than turn end, and a
# much more useful one: a single turn can run a whole multi-step plan, so waiting
# for the turn to finish would let thousands of lines through before the gate
# arms. Tool names differ across Claude Code versions; matching several costs
# nothing, since a name that does not exist simply never fires.
TASK_TOOLS = {"TaskUpdate", "TodoWrite", "TaskCreate"}
MAX_MARKERS = 5
MAX_ATTEMPTS = 3

DEFAULT_CONFIG = {
    "level": "medium",
    "thresholds": {"strict": 150, "medium": 400, "loose": 1000},
    # Past the threshold the gate waits for a finished piece of work rather than
    # cutting in mid-implementation. This is how much extra code it will accept
    # while waiting for that boundary — an absolute allowance, not a multiple,
    # because the work in flight is roughly a fixed size regardless of level.
    "boundary_grace_lines": 150,
    "ignore_globs": [
        "**/node_modules/**",
        "**/.git/**",
        "**/dist/**",
        "**/build/**",
        "**/.next/**",
        "**/vendor/**",
        "**/__pycache__/**",
        "**/*.lock",
        "**/*.min.*",
        "**/package-lock.json",
        "**/yarn.lock",
        "**/pnpm-lock.yaml",
        "**/Podfile.lock",
        "**/*.snap",
    ],
}

# Line-comment syntax by extension. Files whose type is not here are not used as
# marker hosts, because a comment in the wrong syntax could break the file.
LINE_COMMENT = {
    "//": [
        "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts", "c", "h", "cpp", "hpp",
        "cc", "cxx", "java", "kt", "kts", "swift", "go", "rs", "scala", "dart", "php",
        "cs", "m", "mm", "gradle", "proto", "sol", "zig", "groovy", "jsonc",
    ],
    "#": [
        "py", "rb", "sh", "bash", "zsh", "fish", "yml", "yaml", "toml", "r", "pl", "pm",
        "tf", "tfvars", "ex", "exs", "nim", "cmake", "mk", "rake", "gemspec", "cfg",
        "conf", "env", "ps1",
    ],
    "--": ["sql", "hs", "lua", "elm", "adb", "ads"],
    ";": ["lisp", "clj", "cljs", "cljc", "el", "scm", "asm", "ini"],
    "%": ["tex", "erl"],
}
BLOCK_COMMENT = {
    ("/*", "*/"): ["css", "scss", "less", "sass"],
    ("<!--", "-->"): ["html", "htm", "xml", "svg", "vue", "svelte", "astro"],
}
BASENAME_COMMENT = {
    "Makefile": "#",
    "Dockerfile": "#",
    "Rakefile": "#",
    "Gemfile": "#",
    "Procfile": "#",
    ".gitignore": "#",
    ".dockerignore": "#",
}

FALLBACK_MARKER_NAME = "REVIEW-GATE.txt"


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        stored = json.loads(CONFIG_PATH.read_text())
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key == "thresholds" and isinstance(value, dict):
                    cfg["thresholds"].update(value)
                else:
                    cfg[key] = value
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def threshold_for(cfg: dict) -> int:
    level = cfg.get("level", "medium")
    thresholds = cfg.get("thresholds", {})
    try:
        return int(thresholds.get(level, DEFAULT_CONFIG["thresholds"]["medium"]))
    except (TypeError, ValueError):
        return DEFAULT_CONFIG["thresholds"]["medium"]


def ceiling_for(cfg: dict) -> int:
    """Where the gate stops waiting for a clean boundary and just arms."""
    try:
        grace = max(0, int(cfg.get("boundary_grace_lines", 150)))
    except (TypeError, ValueError):
        grace = 150
    return threshold_for(cfg) + grace


def dwell_for(lines: int) -> int:
    """Minimum seconds a gate stays shut, scaled to how much there is to read.

    Not a security control — it only rules out the clear that happens so fast
    nobody could have opened the file.
    """
    return min(300, max(30, lines // 4))


# --------------------------------------------------------------------------
# project identity + git measurement
# --------------------------------------------------------------------------


def run_git(root: str, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def git_root(cwd: str) -> str | None:
    if not cwd or not Path(cwd).is_dir():
        return None
    out = run_git(cwd, "rev-parse", "--show-toplevel")
    return out.strip() if out else None


def project_for(cwd: str) -> str:
    """The unit the counter belongs to: a git repo, else the directory."""
    return git_root(cwd) or (cwd or os.getcwd())


def git_head(root: str) -> str:
    out = run_git(root, "rev-parse", "HEAD")
    return out.strip() if out else ""


def git_churn(root: str, cfg: dict) -> int | None:
    """Added plus deleted lines in the working tree, versus HEAD.

    This is what makes the count hard to sidestep: it measures what is actually
    on disk, so code written through a shell heredoc or by a subagent lands in it
    exactly like an Edit does.
    """
    total = 0
    tracked = run_git(root, "diff", "--numstat", "HEAD")
    if tracked is None:
        # No HEAD yet (fresh repo) — everything is untracked, counted below.
        tracked = run_git(root, "diff", "--numstat") or ""
    for line in tracked.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            if is_ignored(str(Path(root) / parts[2]), cfg):
                continue
            total += int(parts[0]) + int(parts[1])

    untracked = run_git(root, "ls-files", "--others", "--exclude-standard")
    if untracked is None:
        return None
    for rel in untracked.splitlines():
        path = Path(root) / rel
        if is_ignored(str(path), cfg):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            total += len(path.read_text(errors="ignore").splitlines())
        except (OSError, UnicodeDecodeError):
            continue
    return total


def measured_lines(state: dict, cfg: dict) -> int:
    """Authoritative unreviewed-line count for this project.

    The larger of the two available signals. Git catches writes that never went
    through a tool call; the tool tally catches projects that are not git repos,
    and code that was generated and then reverted before anyone saw it.
    """
    tallied = state.get("lines_tallied", 0)
    root = state.get("git_root")
    if not root or not Path(root).is_dir():
        return tallied

    head = git_head(root)
    churn = git_churn(root, cfg)
    if churn is None:
        return tallied

    # A commit is a natural reset point, and it moves churn back to ~0. Rebase
    # the baseline onto the new HEAD rather than letting the count go negative.
    if state.get("baseline_head") != head:
        state["baseline_head"] = head
        state["baseline_churn"] = churn
        save_state(state)
        return tallied

    return max(tallied, max(0, churn - state.get("baseline_churn", 0)))


def rebase_baseline(state: dict, cfg: dict) -> None:
    """Treat everything currently on disk as reviewed."""
    state["lines_tallied"] = 0
    state["files"] = {}
    root = state.get("git_root")
    if root and Path(root).is_dir():
        churn = git_churn(root, cfg)
        state["baseline_head"] = git_head(root)
        state["baseline_churn"] = churn if churn is not None else 0


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------


def state_path(project: str) -> Path:
    """One state file per project, keyed by a readable name plus a path digest."""
    import hashlib as _hashlib

    digest = _hashlib.sha256((project or "unknown").encode()).hexdigest()[:10]
    label = re.sub(r"[^A-Za-z0-9_.-]", "-", Path(project or "unknown").name)[:40] or "root"
    return STATE_DIR / f"{label}-{digest}.json"


def load_state(project: str) -> dict:
    path = state_path(project)
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data.setdefault("project", project)
            data.setdefault("lines_tallied", 0)
            data.setdefault("lines_total", 0)
            data.setdefault("gates_passed", 0)
            data.setdefault("files", {})
            data.setdefault("gate", None)
            data.setdefault("git_root", git_root(project))
            return data
    except (OSError, ValueError):
        pass

    # A brand-new project starts with whatever is already on disk treated as
    # reviewed. The baseline has to be taken here rather than lazily at first
    # measurement, or any code written in between reads as pre-existing.
    root = git_root(project)
    fresh = {
        "project": project,
        "git_root": root,
        "lines_tallied": 0,
        "lines_total": 0,
        "gates_passed": 0,
        "files": {},
        "gate": None,
        "created_at": now_iso(),
    }
    if root:
        churn = git_churn(root, load_config())
        fresh["baseline_head"] = git_head(root)
        fresh["baseline_churn"] = churn if churn is not None else 0
    return fresh


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now_iso()
    path = state_path(state["project"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)


def log_event(event: str, **fields) -> None:
    """Append-only audit trail, so the human can see every gate after the fact."""
    HOME.mkdir(parents=True, exist_ok=True)
    record = {"at": now_iso(), "event": event}
    record.update(fields)
    try:
        with LOG_PATH.open("a") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------
# tool-call tallying (the fallback signal)
# --------------------------------------------------------------------------


def is_ignored(file_path: str, cfg: dict) -> bool:
    if not file_path:
        return True
    for pattern in cfg.get("ignore_globs", []):
        if fnmatch.fnmatch(file_path, pattern):
            return True
    return False


def count_lines(text: str) -> int:
    return len(text.splitlines()) if text else 0


def diff_size(old: str, new: str) -> int:
    changed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), n=0, lineterm=""):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            changed += 1
    return changed


def measure(tool_name: str, tool_input: dict) -> int:
    """How many lines of code one tool call put into the world."""
    if tool_name == "Write":
        # A Write is the whole file. Even when it overwrites something, every
        # line in it is a line Claude just authored and the human has not read.
        return count_lines(tool_input.get("content", ""))
    if tool_name == "Edit":
        return diff_size(tool_input.get("old_string", ""), tool_input.get("new_string", ""))
    if tool_name == "MultiEdit":
        return sum(
            diff_size(edit.get("old_string", ""), edit.get("new_string", ""))
            for edit in tool_input.get("edits", [])
            if isinstance(edit, dict)
        )
    if tool_name == "NotebookEdit":
        return count_lines(tool_input.get("new_source", ""))
    return 0


# --------------------------------------------------------------------------
# markers
# --------------------------------------------------------------------------


def comment_wrap(path: Path) -> tuple[str, str] | None:
    if path.name in BASENAME_COMMENT:
        return BASENAME_COMMENT[path.name], ""
    ext = path.suffix.lstrip(".").lower()
    if not ext:
        return None
    for prefix, exts in LINE_COMMENT.items():
        if ext in exts:
            return prefix, ""
    for (prefix, suffix), exts in BLOCK_COMMENT.items():
        if ext in exts:
            return prefix, " " + suffix
    return None


def insert_index(lines: list[str]) -> int:
    """Shebangs, XML declarations and PHP open tags have to stay on line 1."""
    if not lines:
        return 0
    first = lines[0].lstrip()
    return 1 if first.startswith(("#!", "<?")) else 0


def changed_files(state: dict, cfg: dict) -> list[str]:
    """Files with unreviewed changes, most-changed first.

    Prefers git so that files written outside a tool call are included; falls
    back to the tally when there is no repo.
    """
    root = state.get("git_root")
    ranked = [p for p, _ in sorted(state.get("files", {}).items(), key=lambda kv: -kv[1])]
    if not root or not Path(root).is_dir():
        return ranked

    ordered: list[tuple[int, str]] = []
    tracked = run_git(root, "diff", "--numstat", "HEAD") or ""
    for line in tracked.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            ordered.append((int(parts[0]) + int(parts[1]), str(Path(root) / parts[2])))
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard") or ""
    for rel in untracked.splitlines():
        path = Path(root) / rel
        try:
            ordered.append((len(path.read_text(errors="ignore").splitlines()), str(path)))
        except (OSError, UnicodeDecodeError):
            continue

    ordered.sort(reverse=True)
    merged = [p for _, p in ordered if not is_ignored(p, cfg)]
    for path in ranked:  # keep any tally-only files git did not report
        if path not in merged:
            merged.append(path)
    return merged


def plant_markers(state: dict, cfg: dict, lines: int, token: str) -> list[dict]:
    """Drop a marker into each of the top changed files.

    One marker per file rather than one overall, because a single marker only
    ever proves that one file was opened.
    """
    body = (
        f"[REVIEW-GATE {token}] {lines} lines of unreviewed changes in this project. "
        f"Read them, then delete this line. Every marker must go before Claude can write again."
    )
    planted: list[dict] = []

    for raw in changed_files(state, cfg):
        if len(planted) >= MAX_MARKERS:
            break
        path = Path(raw)
        wrap = comment_wrap(path)
        if wrap is None or not path.is_file():
            continue
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        prefix, suffix = wrap
        marker = f"{prefix} {body}{suffix}"
        file_lines = text.splitlines(keepends=True)
        index = insert_index(file_lines)
        if file_lines and not file_lines[-1].endswith("\n"):
            file_lines[-1] += "\n"
        file_lines.insert(index, marker + "\n")
        try:
            path.write_text("".join(file_lines))
        except OSError:
            continue
        planted.append({"file": str(path), "line": index + 1})

    if not planted:
        # Nothing suitable to annotate — drop a standalone file to delete.
        fallback = Path(state.get("project") or ".") / FALLBACK_MARKER_NAME
        try:
            fallback.write_text(f"[REVIEW-GATE {token}] {body}\n")
            planted.append({"file": str(fallback), "line": 1})
        except OSError:
            pass
    return planted


def outstanding_markers(gate: dict) -> list[dict]:
    """Markers still on disk. Read from the files, never taken on trust."""
    token = gate.get("token")
    if not token:
        return []
    remaining = []
    for marker in gate.get("markers", []):
        path = Path(marker.get("file", ""))
        try:
            if path.exists() and token in path.read_text():
                remaining.append(marker)
        except (OSError, UnicodeDecodeError):
            continue
    return remaining


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------


def file_summary(state: dict, cfg: dict, limit: int = 12) -> str:
    ranked = sorted(state.get("files", {}).items(), key=lambda kv: -kv[1])
    if ranked:
        rows = [f"  {n:>5} lines  {p}" for p, n in ranked[:limit]]
        if len(ranked) > limit:
            rows.append(f"  ... and {len(ranked) - limit} more")
        return "\n".join(rows)
    return "\n".join(f"  {p}" for p in changed_files(state, cfg)[:limit]) or "  (none recorded)"


def gate_cmd() -> str:
    return f'python3 "{Path(__file__).resolve()}"'


def marker_list(markers: list[dict]) -> str:
    return "\n".join(f"    {m['file']}:{m['line']}" for m in markers)


def dwell_remaining(gate: dict) -> int:
    try:
        armed = datetime.fromisoformat(gate["armed_at"]).timestamp()
    except (KeyError, ValueError):
        return 0
    return max(0, int(gate.get("dwell_seconds", 0) - (time.time() - armed)))


def armed_message(state: dict, gate: dict, cfg: dict, lines: int) -> str:
    project = state["project"]
    cmd = gate_cmd()
    return f"""REVIEW GATE ARMED — writes and shell are blocked.

{lines} lines of unreviewed code have built up in this project, over the
{cfg.get('level', 'medium')} threshold of {threshold_for(cfg)}. Nothing else gets written until a
human has looked at it.

This count is per project and carries across conversations, so it is not
reset by starting a new session.

Changed:
{file_summary(state, cfg)}

STAGE 1 — {len(gate['markers'])} marker(s) planted
{marker_list(gate['markers'])}
  The user deletes every one of them. You cannot: your write tools and shell
  are blocked while this gate is armed.

STAGE 2 — register your question NOW, before they start deleting
  It has to be registered while the markers are still in place, so it cannot be
  shaped around something the user has already told you:
    {cmd} arm-quiz --project '{project}' \\
      --question 'your question' --answer 'the answer you expect'
  Then ask them, and submit their reply verbatim:
    {cmd} answer --project '{project}' 'what the user said'

What to do now: register the question, tell the user the gate tripped, then walk
them through what changed (Read/Grep/Glob still work). Do not route around this."""


def stage_message(state: dict, gate: dict, cfg: dict) -> str:
    project = state["project"]
    cmd = gate_cmd()
    remaining = outstanding_markers(gate)
    parts = ["REVIEW GATE STILL ARMED — writes and shell are blocked.\n"]

    if remaining:
        parts.append(
            f"STAGE 1 — {len(remaining)} of {len(gate.get('markers', []))} marker(s) still on disk\n"
            f"{marker_list(remaining)}\n"
            f"  The user deletes these, not you. Help them read the changes meanwhile.\n"
        )
    else:
        parts.append("STAGE 1 — cleared. Every marker is gone.\n")

    if gate.get("quiz_passed"):
        parts.append("STAGE 2 — cleared.\n")
    elif gate.get("question"):
        parts.append(
            f"STAGE 2 — not cleared\n"
            f"  Question asked: {gate['question']}\n"
            f"  Attempts so far: {gate.get('attempts', 0)}. Submit their reply verbatim:\n"
            f"    {cmd} answer --project '{project}' 'what the user said'\n"
        )
    else:
        parts.append(
            f"STAGE 2 — no question registered\n"
            f"  Register one, then ask the user:\n"
            f"    {cmd} arm-quiz --project '{project}' \\\n"
            f"      --question 'your question' --answer 'the answer you expect'\n"
        )

    wait = dwell_remaining(gate)
    if wait and not remaining:
        parts.append(
            f"\nA {gate.get('dwell_seconds')}s minimum applies to this gate ({wait}s left) — "
            f"there is more code here than anyone reads that fast."
        )
    parts.append("\nThe gate lifts on its own once both stages clear — retry your edit then.")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# hook plumbing
# --------------------------------------------------------------------------


def read_hook_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return {}


def allow() -> int:
    return 0


def deny(reason: str) -> int:
    """Block the tool call and hand the reason back to Claude.

    Both channels on purpose: the JSON on stdout carries the structured
    decision, and exit code 2 guarantees the text on stderr reaches Claude.
    """
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                },
                "systemMessage": reason,
            }
        )
    )
    print(reason, file=sys.stderr)
    return 2


def clear_gate(state: dict, cfg: dict) -> None:
    gate = state.get("gate") or {}
    log_event(
        "gate_passed",
        project=state["project"],
        lines=gate.get("lines_at_arm", 0),
        markers=len(gate.get("markers", [])),
        question=gate.get("question"),
        attempts=gate.get("attempts", 0),
        question_committed_early=gate.get("committed_early", False),
    )
    state["gate"] = None
    state["gates_passed"] = state.get("gates_passed", 0) + 1
    rebase_baseline(state, cfg)
    save_state(state)


def evaluate_gate(state: dict, cfg: dict) -> bool:
    """Re-check both stages plus dwell. True when the gate has fully cleared."""
    gate = state.get("gate")
    if not gate:
        return True
    if outstanding_markers(gate):
        return False
    if not gate.get("marker_cleared"):
        gate["marker_cleared"] = True
        gate["marker_cleared_at"] = now_iso()
        save_state(state)
        log_event("markers_cleared", project=state["project"], count=len(gate.get("markers", [])))
    if not gate.get("quiz_passed"):
        return False
    return dwell_remaining(gate) == 0


def arm_gate(state: dict, cfg: dict, lines: int, trigger: str = "threshold") -> str:
    token = secrets.token_hex(3)
    markers = plant_markers(state, cfg, lines, token)
    state.pop("pending_since", None)
    state["gate"] = {
        "trigger": trigger,
        "token": token,
        "markers": markers,
        "marker_cleared": False,
        "question": None,
        "answer_plain": None,
        "attempts": 0,
        "quiz_passed": False,
        "armed_at": now_iso(),
        "dwell_seconds": dwell_for(lines),
        "lines_at_arm": lines,
    }
    save_state(state)
    log_event(
        "gate_armed",
        project=state["project"],
        lines=lines,
        level=cfg.get("level"),
        trigger=trigger,
        markers=[m["file"] for m in markers],
    )
    return armed_message(state, state["gate"], cfg, lines)


# --------------------------------------------------------------------------
# bash allowlist
# --------------------------------------------------------------------------

ALLOWED_SUBCOMMANDS = {"arm-quiz", "answer", "status"}


def has_unquoted_metachar(command: str) -> bool:
    """Scan for shell operators that sit outside quotes.

    Quoting is what decides, not the raw characters: a user's answer is very
    likely to contain a semicolon or an ampersand, and `answer 'it fails & then
    retries'` has to be allowed through. `status ;rm -rf /` must not be.
    Unbalanced quotes count as unsafe, since intent cannot be read off them.
    """
    quote = None
    i = 0
    while i < len(command):
        char = command[i]
        if quote:
            if char == "\\" and quote == '"':
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in ";|&<>\n`":
            return True
        elif char == "$" and command[i + 1 : i + 2] == "(":
            return True
        elif char == "\\":
            i += 2
            continue
        i += 1
    return quote is not None


def is_gate_command(command: str) -> bool:
    """True only for a bare call to this script's own subcommands."""
    if has_unquoted_metachar(command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if len(tokens) < 3:
        return False
    if not re.fullmatch(r"python3?(\.\d+)?", Path(tokens[0]).name):
        return False
    if Path(tokens[1]).name != "gate.py":
        return False
    return tokens[2] in ALLOWED_SUBCOMMANDS


# --------------------------------------------------------------------------
# hook entry points
# --------------------------------------------------------------------------


def hook_post_tool(data: dict) -> int:
    """Tally lines after every successful write."""
    cfg = load_config()
    tool_name = data.get("tool_name", "")
    if tool_name not in WRITE_TOOLS:
        return allow()

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if is_ignored(file_path, cfg):
        return allow()

    lines = measure(tool_name, tool_input)
    if lines <= 0:
        return allow()

    state = load_state(project_for(data.get("cwd", "")))
    state["lines_tallied"] = state.get("lines_tallied", 0) + lines
    state["lines_total"] = state.get("lines_total", 0) + lines
    state["files"][file_path] = state["files"].get(file_path, 0) + lines
    save_state(state)

    threshold = threshold_for(cfg)
    current = state["lines_tallied"]
    note = None
    if state.get("gate"):
        pass
    elif state.get("pending_since"):
        # The gate is queued and will arm the moment this turn ends. Saying so
        # is what makes the deferral work as intended: finish the piece of work
        # that is open, and do not open another one.
        note = (
            "review-gate: a gate is queued and arms as soon as this turn ends. Finish "
            "the piece of work already in flight and stop there — do not start anything "
            "new, and do not pad this turn to defer the gate."
        )
    elif threshold * 0.8 <= current < threshold:
        note = (
            f"review-gate: {current} of {threshold} lines. The gate queues shortly and "
            f"arms at the end of whichever turn crosses it — aim to finish a coherent "
            f"unit of work rather than starting anything large."
        )
    if note:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": note,
                    }
                }
            )
        )
    return allow()


def hook_pre_write(data: dict) -> int:
    """Block writes when a gate is armed, and arm one when the threshold is hit."""
    cfg = load_config()
    state = load_state(project_for(data.get("cwd", "")))

    if state.get("gate"):
        if evaluate_gate(state, cfg):
            clear_gate(state, cfg)
            return allow()
        return deny(stage_message(state, state["gate"], cfg))

    threshold = threshold_for(cfg)
    lines = measured_lines(state, cfg)
    if lines < threshold:
        return allow()

    # Past the threshold, but stopping here would hand the user half an
    # implementation to review — which teaches them to wave gates through. Go
    # pending instead and let the Stop hook arm it at the end of the turn, when
    # the work is at a natural boundary.
    if lines >= ceiling_for(cfg):
        return deny(arm_gate(state, cfg, lines, trigger="ceiling"))

    if not state.get("pending_since"):
        state["pending_since"] = now_iso()
        save_state(state)
        log_event("gate_pending", project=state["project"], lines=lines, ceiling=ceiling_for(cfg))
    return allow()


def marks_completion(tool_input: dict) -> bool:
    """Whether this task-tool call reports something finished.

    Deliberately permissive: the payload shape varies by version, and the two
    failure directions are not equal. Arming a little early costs the user a
    slightly smaller review; missing the boundary costs them a 5-step plan's
    worth of unreviewed code, which is the thing this exists to prevent.
    """
    try:
        return "completed" in json.dumps(tool_input).lower()
    except (TypeError, ValueError):
        return False


def arm_at_boundary(state: dict, cfg: dict, trigger: str) -> str | None:
    """Convert a queued gate into an armed one. None when nothing was queued."""
    if state.get("gate") or not state.get("pending_since"):
        return None
    lines = measured_lines(state, cfg)
    if lines < threshold_for(cfg):
        # Fell back under (a revert, or a commit rebaselined the count).
        state.pop("pending_since", None)
        save_state(state)
        return None
    return arm_gate(state, cfg, lines, trigger=trigger)


def hook_task_boundary(data: dict) -> int:
    """A plan item was marked done — arm a queued gate here rather than waiting."""
    if data.get("tool_name") not in TASK_TOOLS:
        return allow()
    if not marks_completion(data.get("tool_input") or {}):
        return allow()

    cfg = load_config()
    state = load_state(project_for(data.get("cwd", "")))
    if arm_at_boundary(state, cfg, "task-complete") is None:
        return allow()

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        "review-gate: that finished a step of the plan and a gate was "
                        "queued, so it has armed now rather than waiting for the rest of "
                        "the plan. Markers are planted and your writes are blocked. Stop "
                        "here, load the review-gate skill and run the gate — do not "
                        "continue to the next step."
                    ),
                }
            }
        )
    )
    return allow()


def hook_stop(data: dict) -> int:
    """Turn end — the natural boundary. Arm a pending gate here.

    Never blocks the stop itself; it only converts pending into armed, so the
    user is handed a finished unit of work to review rather than a fragment.
    """
    cfg = load_config()
    state = load_state(project_for(data.get("cwd", "")))
    if arm_at_boundary(state, cfg, "turn-end") is None:
        return allow()

    lines = state["gate"]["lines_at_arm"]
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "additionalContext": (
                        f"review-gate: the gate has armed now that this piece of work is "
                        f"finished ({lines} unreviewed lines). Markers are planted and your "
                        f"writes are blocked until the user reviews. Load the review-gate "
                        f"skill and run the gate before doing anything else."
                    ),
                }
            }
        )
    )
    return allow()


def hook_pre_bash(data: dict) -> int:
    """Block the shell during a gate, except the gate script itself.

    Without this, removing a planted marker would be one `sed` away, and stage 1
    would mean nothing.
    """
    cfg = load_config()
    state = load_state(project_for(data.get("cwd", "")))
    gate = state.get("gate")
    if not gate:
        return allow()
    if evaluate_gate(state, cfg):
        clear_gate(state, cfg)
        return allow()

    command = (data.get("tool_input") or {}).get("command", "")
    # Only a bare call to this script gets through, so a permitted call cannot
    # smuggle a second one along with it.
    if is_gate_command(command):
        return allow()

    return deny(
        "REVIEW GATE ARMED — the shell is blocked so the markers cannot be removed by "
        "anything but the user.\n\n" + stage_message(state, gate, cfg)
    )


def hook_session_start(data: dict) -> int:
    cfg = load_config()
    cwd = data.get("cwd", "") or os.getcwd()
    project = project_for(cwd)

    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        try:
            with open(env_file, "a") as handle:
                handle.write(f"export REVIEW_GATE_PROJECT={shlex.quote(project)}\n")
        except OSError:
            pass

    state = load_state(project)
    # Persist immediately: this is the earliest safe moment to take the git
    # baseline, before anything in the session has written to the tree.
    save_state(state)
    lines = measured_lines(state, cfg)
    threshold = threshold_for(cfg)
    context = (
        f"review-gate active: {cfg.get('level')} level, gate trips at {threshold} lines of "
        f"unreviewed code in this project. The count is per project and carries across "
        f"sessions — it currently stands at {lines}. When the gate trips your writes and "
        f"shell are blocked until the user has reviewed."
    )
    if state.get("gate"):
        context += (
            " A gate is ARMED right now and still unmet — resolve it before writing "
            "anything."
        )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            }
        )
    )
    return allow()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def cli_project(args) -> str:
    return getattr(args, "project", None) or os.environ.get("REVIEW_GATE_PROJECT") or project_for(
        os.getcwd()
    )


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def answer_matches(given: str, expected: str) -> bool:
    """Lenient match — the point is comprehension, not exact wording.

    A short expected answer must appear in what the user said; a longer one is
    compared on its distinctive words so paraphrases still pass.
    """
    got, want = normalise(given), normalise(expected)
    if not got or not want:
        return False
    if want in got or got in want:
        return True
    want_words = [w for w in want.split() if len(w) > 3]
    if not want_words:
        return False
    return sum(1 for w in want_words if w in got) / len(want_words) >= 0.6


def cmd_arm_quiz(args) -> int:
    state = load_state(cli_project(args))
    gate = state.get("gate")
    if not gate:
        print("review-gate: no gate is armed, nothing to attach a question to.")
        return 0
    if gate.get("question") and not gate.get("quiz_passed"):
        print(
            f"review-gate: a question is already registered — {gate['question']}\n"
            f"Ask that one. It is only replaced after three wrong answers.",
            file=sys.stderr,
        )
        return 1

    # Commit-before-reveal: the first question has to be locked in while the
    # markers are still up, so it cannot be shaped around something the user
    # said while reviewing. A replacement after three misses cannot meet that.
    early = not gate.get("marker_cleared")
    if not early and not gate.get("voided_once"):
        print(
            "review-gate: too late to register the first question — the markers are "
            "already cleared. Register it when the gate arms, before the user starts "
            "reviewing. This gate now needs a fresh arm.",
            file=sys.stderr,
        )
        return 1

    gate["question"] = args.question
    gate["answer_plain"] = args.answer
    gate["attempts"] = 0
    gate["committed_early"] = early
    save_state(state)
    log_event(
        "quiz_armed", project=state["project"], question=args.question, committed_early=early
    )
    print(
        "Question registered. Ask the user now, then submit their reply verbatim with "
        "`answer`. Do not answer on their behalf — the whole point is that a human read "
        "the code."
    )
    return 0


def cmd_answer(args) -> int:
    cfg = load_config()
    state = load_state(cli_project(args))
    gate = state.get("gate")
    if not gate:
        print("review-gate: no gate is armed.")
        return 0
    if not gate.get("question"):
        print("review-gate: register a question with `arm-quiz` first.", file=sys.stderr)
        return 1

    remaining = outstanding_markers(gate)
    if remaining:
        print(
            f"review-gate: stage 1 is still outstanding — {len(remaining)} marker(s) left:\n"
            f"{marker_list(remaining)}\n"
            f"The user deletes those before the question counts.",
            file=sys.stderr,
        )
        return 1

    gate["attempts"] = gate.get("attempts", 0) + 1
    if answer_matches(args.text, gate.get("answer_plain", "")):
        gate["quiz_passed"] = True
        save_state(state)
        log_event(
            "quiz_passed", project=state["project"], attempts=gate["attempts"], given=args.text
        )
        if evaluate_gate(state, cfg):
            clear_gate(state, cfg)
            print("Gate cleared. Counter reset — carry on.")
        else:
            print(
                f"Answer accepted. The gate still has {dwell_remaining(gate)}s of its minimum "
                f"review time left — use it to finish walking the user through the changes."
            )
        return 0

    log_event("quiz_failed", project=state["project"], attempts=gate["attempts"], given=args.text)
    if gate["attempts"] >= MAX_ATTEMPTS:
        # Three misses usually means the question was the problem, not the human.
        # Voiding it keeps the gate from turning into a guessing game.
        gate.update({"question": None, "answer_plain": None, "attempts": 0, "voided_once": True})
        save_state(state)
        print(
            "Three misses — the question is not landing. Walk the user through that part "
            "of the code properly, then register a different question with `arm-quiz`. "
            "The gate stays armed.",
            file=sys.stderr,
        )
        return 1

    save_state(state)
    print(
        f"Not a match (attempt {gate['attempts']}). Tell the user what the answer was and "
        f"why, point them at the specific code, and let them try again. The gate stays armed.",
        file=sys.stderr,
    )
    return 1


def cmd_checkpoint(args) -> int:
    """Arm a queued gate now, because a piece of work just finished.

    Only ever makes the gate stricter — it can bring an already-pending gate
    forward, never defer one. Deferral is the failure mode, and turn-end already
    covers it.
    """
    cfg = load_config()
    state = load_state(cli_project(args))
    if state.get("gate"):
        print(stage_message(state, state["gate"], cfg))
        return 0
    if not state.get("pending_since"):
        print("review-gate: nothing queued — carry on.")
        return 0
    print(arm_gate(state, cfg, measured_lines(state, cfg), trigger="checkpoint"))
    return 0


def cmd_status(args) -> int:
    cfg = load_config()
    project = cli_project(args)
    state = load_state(project)
    lines = measured_lines(state, cfg)
    threshold = threshold_for(cfg)

    print(f"review-gate — {cfg.get('level')} level, gate trips at {threshold} lines")
    print(f"  project        : {project}")
    print(f"  counting via   : {'git working tree' if state.get('git_root') else 'tool calls'}")
    print(f"  unreviewed     : {lines} lines ({max(0, threshold - lines)} to go)")
    print(f"  gates passed   : {state.get('gates_passed', 0)}")
    if state.get("pending_since") and not state.get("gate"):
        ceiling = ceiling_for(cfg)
        print(
            f"  QUEUED         : over threshold, waiting for this piece of work to "
            f"finish (forced at {ceiling})"
        )
    if state.get("files") or state.get("git_root"):
        print("  changed:")
        print(file_summary(state, cfg))

    gate = state.get("gate")
    if gate:
        remaining = outstanding_markers(gate)
        print("\n  GATE ARMED")
        print(f"    markers : {len(remaining)} of {len(gate.get('markers', []))} still on disk")
        for marker in remaining:
            print(f"      {marker['file']}:{marker['line']}")
        state_word = (
            "passed" if gate.get("quiz_passed")
            else ("awaiting answer" if gate.get("question") else "no question set")
        )
        print(f"    question: {state_word}")
        if dwell_remaining(gate):
            print(f"    minimum : {dwell_remaining(gate)}s remaining")
    return 0


def cmd_level(args) -> int:
    cfg = load_config()
    if not args.level:
        print(f"review-gate level: {cfg.get('level')} ({threshold_for(cfg)} lines)")
        for name, value in cfg.get("thresholds", {}).items():
            print(f"  {name:<7} {value} lines")
        return 0
    if args.level not in cfg.get("thresholds", {}):
        print(f"review-gate: unknown level '{args.level}'.", file=sys.stderr)
        return 1
    cfg["level"] = args.level
    save_config(cfg)
    log_event("level_changed", level=args.level)
    print(f"review-gate level set to {args.level} — gate now trips at {threshold_for(cfg)} lines.")
    return 0


def cmd_review(args) -> int:
    """Arm a gate on demand, before the threshold is reached."""
    cfg = load_config()
    state = load_state(cli_project(args))
    if state.get("gate"):
        print(stage_message(state, state["gate"], cfg))
        return 0
    lines = measured_lines(state, cfg)
    if lines <= 0:
        print("review-gate: no unreviewed changes in this project.")
        return 0
    print(arm_gate(state, cfg, lines))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="gate.py", description="review-gate")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in (
        "hook-post-tool",
        "hook-pre-write",
        "hook-pre-bash",
        "hook-session-start",
        "hook-stop",
        "hook-task-boundary",
    ):
        sub.add_parser(name)

    quiz = sub.add_parser("arm-quiz")
    quiz.add_argument("--project")
    quiz.add_argument("--question", required=True)
    quiz.add_argument("--answer", required=True)

    ans = sub.add_parser("answer")
    ans.add_argument("--project")
    ans.add_argument("text")

    for name in ("status", "review", "checkpoint"):
        node = sub.add_parser(name)
        node.add_argument("--project")

    lvl = sub.add_parser("level")
    lvl.add_argument("level", nargs="?", choices=["strict", "medium", "loose"])

    args = parser.parse_args()

    hooks = {
        "hook-post-tool": hook_post_tool,
        "hook-pre-write": hook_pre_write,
        "hook-pre-bash": hook_pre_bash,
        "hook-session-start": hook_session_start,
        "hook-stop": hook_stop,
        "hook-task-boundary": hook_task_boundary,
    }
    if args.command in hooks:
        try:
            return hooks[args.command](read_hook_input())
        except Exception as exc:  # never wedge the session on a bug in here
            print(f"review-gate hook error: {exc}", file=sys.stderr)
            return 0

    return {
        "arm-quiz": cmd_arm_quiz,
        "answer": cmd_answer,
        "status": cmd_status,
        "level": cmd_level,
        "review": cmd_review,
        "checkpoint": cmd_checkpoint,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

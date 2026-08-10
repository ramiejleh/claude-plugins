#!/usr/bin/env python3
"""review-gate — force periodic human review of Claude-generated code.

Claude Code runs this as a hook. It tallies how many lines Claude has written
in the current session and, once that crosses the configured threshold, denies
every further write until a human has actually looked at the code.

The gate has two stages:

  1. marker  — a comment line carrying a random token is planted in one of the
               changed files. It clears when that token is gone from disk.
               This stage is the load-bearing one: the script verifies it by
               reading the file, and while a gate is armed Claude's write tools
               and shell are blocked, so Claude cannot remove the marker itself.
  2. quiz    — Claude poses a comprehension question about the changes and the
               human answers it. The answer is checked against a stored hash.

Subcommands are split into hook entry points (fed JSON on stdin by Claude Code)
and a small CLI used by the slash commands and by Claude during a gate.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("REVIEW_GATE_HOME") or (Path.home() / ".claude" / "review-gate"))
STATE_DIR = HOME / "state"
CONFIG_PATH = HOME / "config.json"
LOG_PATH = HOME / "log.jsonl"

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

DEFAULT_CONFIG = {
    "level": "medium",
    "thresholds": {"strict": 150, "medium": 400, "loose": 1000},
    "quiz_enabled": True,
    "block_bash_during_gate": True,
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
# config + state
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


def state_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    return STATE_DIR / f"{safe}.json"


def resolve_session(explicit: str | None) -> str | None:
    """Find which session's state to operate on.

    Hooks always know the session id. The CLI subcommands Claude runs during a
    gate do not, so they fall back to an env var exported at SessionStart and
    finally to the most recently touched state file.
    """
    if explicit:
        return explicit
    env = os.environ.get("REVIEW_GATE_SESSION")
    if env:
        return env
    try:
        files = sorted(STATE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return files[0].stem if files else None


def load_state(session_id: str) -> dict:
    path = state_path(session_id)
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data.setdefault("session_id", session_id)
            data.setdefault("lines_since_review", 0)
            data.setdefault("lines_total", 0)
            data.setdefault("gates_passed", 0)
            data.setdefault("files", {})
            data.setdefault("gate", None)
            return data
    except (OSError, ValueError):
        pass
    return {
        "session_id": session_id,
        "lines_since_review": 0,
        "lines_total": 0,
        "gates_passed": 0,
        "files": {},
        "gate": None,
        "created_at": now_iso(),
    }


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now_iso()
    path = state_path(state["session_id"])
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


def prune_state(max_age_days: int = 14) -> None:
    cutoff = time.time() - max_age_days * 86400
    try:
        for path in STATE_DIR.glob("*.json"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------
# counting
# --------------------------------------------------------------------------


def is_ignored(file_path: str, cfg: dict) -> bool:
    if not file_path:
        return True
    for pattern in cfg.get("ignore_globs", []):
        if fnmatch.fnmatch(file_path, pattern):
            return True
    return False


def count_lines(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def diff_size(old: str, new: str) -> int:
    """Lines added plus lines removed between two strings."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    changed = 0
    for line in difflib.unified_diff(old_lines, new_lines, n=0, lineterm=""):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+") or line.startswith("-"):
            changed += 1
    return changed


def measure(tool_name: str, tool_input: dict) -> int:
    """How many lines of code this tool call put into the world."""
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
# marker planting + verification
# --------------------------------------------------------------------------


def comment_wrap(path: Path) -> tuple[str, str] | None:
    """Return (prefix, suffix) for a comment in this file's language."""
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


def insert_index(lines: list[str], path: Path) -> int:
    """Where to put the marker so it does not break the file.

    Shebangs, XML declarations and PHP open tags have to stay on line 1, so the
    marker goes just below them.
    """
    if not lines:
        return 0
    first = lines[0].lstrip()
    if first.startswith("#!") or first.startswith("<?"):
        return 1
    return 0


def choose_marker_file(state: dict, cfg: dict) -> Path | None:
    """Pick the changed file with the most new code that can host a comment."""
    ranked = sorted(state.get("files", {}).items(), key=lambda item: item[1], reverse=True)
    for raw_path, _lines in ranked:
        path = Path(raw_path)
        if is_ignored(raw_path, cfg) or not path.is_file():
            continue
        if comment_wrap(path) is None:
            continue
        try:
            path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        return path
    return None


def plant_marker(state: dict, cfg: dict, cwd: str, token: str) -> tuple[Path, int, str]:
    """Write the marker into a changed file. Returns (path, line_no, text)."""
    target = choose_marker_file(state, cfg)
    body = (
        f"[REVIEW-GATE {token}] {state['lines_since_review']} lines written since your "
        f"last review. Read the changes above, then delete this line to unblock Claude."
    )

    if target is not None:
        prefix, suffix = comment_wrap(target)
        marker = f"{prefix} {body}{suffix}"
        text = target.read_text()
        lines = text.splitlines(keepends=True)
        index = insert_index(lines, target)
        newline = "\n"
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + newline
        lines.insert(index, marker + newline)
        target.write_text("".join(lines))
        return target, index + 1, marker

    # Nothing suitable to annotate — drop a standalone file the human deletes.
    fallback = Path(cwd or ".") / FALLBACK_MARKER_NAME
    marker = f"[REVIEW-GATE {token}] {body}"
    fallback.write_text(marker + "\n")
    return fallback, 1, marker


def marker_present(gate: dict) -> bool:
    """True while the token is still on disk.

    Checked by reading the file rather than by trusting anything Claude says,
    which is what makes this stage of the gate real.
    """
    token = gate.get("token")
    path = Path(gate.get("marker_file", ""))
    if not token or not path.exists():
        return False
    try:
        return token in path.read_text()
    except (OSError, UnicodeDecodeError):
        return False


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------


def file_summary(state: dict, limit: int = 12) -> str:
    ranked = sorted(state.get("files", {}).items(), key=lambda item: item[1], reverse=True)
    rows = [f"  {lines:>5} lines  {path}" for path, lines in ranked[:limit]]
    if len(ranked) > limit:
        rows.append(f"  ... and {len(ranked) - limit} more files")
    return "\n".join(rows)


def gate_script_cmd() -> str:
    return f'python3 "{Path(__file__).resolve()}"'


def armed_message(state: dict, gate: dict, cfg: dict) -> str:
    session = state["session_id"]
    cmd = gate_script_cmd()
    quiz_line = ""
    if cfg.get("quiz_enabled", True):
        quiz_line = (
            f"\nSTAGE 2 — comprehension question (not yet set)\n"
            f"  Write one question about a decision or consequence in these changes,\n"
            f"  ask the user, then register it before they answer:\n"
            f"    {cmd} arm-quiz --session {session} \\\n"
            f"      --question 'your question' --answer 'the answer you expect'\n"
            f"  Submit their reply verbatim with:\n"
            f"    {cmd} answer --session {session} 'what the user said'\n"
        )
    return f"""REVIEW GATE ARMED — writes and shell are blocked.

{state['lines_since_review']} lines have been written since the last review, over the
{cfg.get('level', 'medium')} threshold of {threshold_for(cfg)}. Nothing else gets written until a
human has looked at this code.

Changed in this stretch:
{file_summary(state)}

STAGE 1 — planted marker
  {gate['marker_file']}:{gate['marker_line']}
  The user must read the changes and delete that line themselves. You cannot:
  your write tools and shell are blocked while this gate is armed.
{quiz_line}
What to do now: tell the user the gate tripped, walk them through what changed
(Read/Grep/Glob still work), and wait. Do not try to route around this."""


def stage_message(state: dict, gate: dict, cfg: dict) -> str:
    """What is still outstanding on an already-armed gate."""
    session = state["session_id"]
    cmd = gate_script_cmd()
    still_marked = marker_present(gate)
    quiz_on = cfg.get("quiz_enabled", True)
    quiz_done = gate.get("quiz_passed") or not quiz_on

    parts = ["REVIEW GATE STILL ARMED — writes and shell are blocked.\n"]

    if still_marked:
        parts.append(
            f"STAGE 1 — not cleared\n"
            f"  The marker is still in {gate['marker_file']} (line {gate['marker_line']}).\n"
            f"  The user deletes it, not you. Help them review the changes while they do.\n"
        )
    else:
        parts.append("STAGE 1 — cleared. The marker is gone.\n")

    if quiz_on and not quiz_done:
        if gate.get("question"):
            attempts = gate.get("attempts", 0)
            parts.append(
                f"STAGE 2 — not cleared\n"
                f"  Question asked: {gate['question']}\n"
                f"  Attempts so far: {attempts}. Submit the user's reply verbatim:\n"
                f"    {cmd} answer --session {session} 'what the user said'\n"
            )
        else:
            parts.append(
                f"STAGE 2 — no question registered yet\n"
                f"  Ask the user one question about a decision or consequence in these\n"
                f"  changes, then register it:\n"
                f"    {cmd} arm-quiz --session {session} \\\n"
                f"      --question 'your question' --answer 'the answer you expect'\n"
            )

    parts.append(
        "\nThe gate lifts on its own once both stages clear — just retry your edit then."
    )
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

    Both channels are used on purpose: the JSON on stdout carries the structured
    decision, and exit code 2 guarantees the text on stderr reaches Claude.
    """
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "systemMessage": reason,
    }
    print(json.dumps(payload))
    print(reason, file=sys.stderr)
    return 2


def clear_gate(state: dict, cfg: dict) -> None:
    gate = state.get("gate") or {}
    log_event(
        "gate_passed",
        session=state["session_id"],
        lines=state.get("lines_since_review", 0),
        marker_file=gate.get("marker_file"),
        question=gate.get("question"),
        attempts=gate.get("attempts", 0),
    )
    state["gate"] = None
    state["lines_since_review"] = 0
    state["files"] = {}
    state["gates_passed"] = state.get("gates_passed", 0) + 1
    save_state(state)


def evaluate_gate(state: dict, cfg: dict) -> bool:
    """Re-check both stages. Returns True when the gate has fully cleared."""
    gate = state.get("gate")
    if not gate:
        return True
    if marker_present(gate):
        return False
    if not gate.get("marker_cleared"):
        gate["marker_cleared"] = True
        gate["marker_cleared_at"] = now_iso()
        save_state(state)
        log_event("marker_cleared", session=state["session_id"], file=gate.get("marker_file"))
    if cfg.get("quiz_enabled", True) and not gate.get("quiz_passed"):
        return False
    return True


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

    session = data.get("session_id") or resolve_session(None) or "unknown"
    state = load_state(session)
    state["lines_since_review"] = state.get("lines_since_review", 0) + lines
    state["lines_total"] = state.get("lines_total", 0) + lines
    state["files"][file_path] = state["files"].get(file_path, 0) + lines
    state["cwd"] = data.get("cwd", state.get("cwd", ""))
    save_state(state)

    threshold = threshold_for(cfg)
    current = state["lines_since_review"]
    # A heads-up before the hard stop, so Claude can finish the unit of work it
    # is in rather than getting cut off mid-refactor.
    if not state.get("gate") and threshold * 0.8 <= current < threshold:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": (
                            f"review-gate: {current} of {threshold} lines. The review gate "
                            f"trips shortly — reach a coherent stopping point rather than "
                            f"starting anything large."
                        ),
                    }
                }
            )
        )
    return allow()


def hook_pre_write(data: dict) -> int:
    """Block writes when a gate is armed, and arm one when the threshold is hit."""
    cfg = load_config()
    session = data.get("session_id") or resolve_session(None) or "unknown"
    state = load_state(session)

    if state.get("gate"):
        if evaluate_gate(state, cfg):
            clear_gate(state, cfg)
            return allow()
        return deny(stage_message(state, state["gate"], cfg))

    if state.get("lines_since_review", 0) < threshold_for(cfg):
        return allow()

    return deny(arm_gate(state, cfg, data.get("cwd", "")))


def arm_gate(state: dict, cfg: dict, cwd: str) -> str:
    token = secrets.token_hex(3)
    path, line_no, marker_text = plant_marker(state, cfg, cwd or state.get("cwd", ""), token)
    state["gate"] = {
        "token": token,
        "marker_file": str(path),
        "marker_line": line_no,
        "marker_text": marker_text,
        "marker_cleared": False,
        "quiz_passed": False,
        "attempts": 0,
        "armed_at": now_iso(),
        "lines_at_arm": state.get("lines_since_review", 0),
    }
    save_state(state)
    log_event(
        "gate_armed",
        session=state["session_id"],
        lines=state.get("lines_since_review", 0),
        level=cfg.get("level"),
        marker_file=str(path),
        files=state.get("files", {}),
    )
    return armed_message(state, state["gate"], cfg)


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


def hook_pre_bash(data: dict) -> int:
    """Block the shell during a gate, except the gate script itself.

    Without this, removing the planted marker would be one `sed` away, and the
    marker stage would mean nothing.
    """
    cfg = load_config()
    if not cfg.get("block_bash_during_gate", True):
        return allow()

    session = data.get("session_id") or resolve_session(None) or "unknown"
    state = load_state(session)
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
        "REVIEW GATE ARMED — the shell is blocked so the marker cannot be removed "
        "by anything but the user.\n\n"
        + stage_message(state, gate, cfg)
    )


def hook_session_start(data: dict) -> int:
    """Export the session id and report where things stand."""
    prune_state()
    cfg = load_config()
    session = data.get("session_id") or ""

    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file and session:
        try:
            with open(env_file, "a") as handle:
                handle.write(f"export REVIEW_GATE_SESSION={shlex.quote(session)}\n")
        except OSError:
            pass

    state = load_state(session) if session else None
    level = cfg.get("level", "medium")
    threshold = threshold_for(cfg)
    context = (
        f"review-gate active: {level} level, gate trips at {threshold} lines written. "
        f"When it trips your writes and shell are blocked until the user reviews the code."
    )
    if state and state.get("gate"):
        context += " A gate is currently ARMED and still unmet — resolve it before writing."
    elif state and state.get("lines_since_review"):
        context += f" {state['lines_since_review']} lines counted so far this session."

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


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def answer_matches(given: str, expected: str) -> bool:
    """Lenient match — the point is comprehension, not exact wording.

    A short expected answer must appear in what the user said; a long one is
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
    hits = sum(1 for word in want_words if word in got)
    return hits / len(want_words) >= 0.6


def cmd_arm_quiz(args) -> int:
    session = resolve_session(args.session)
    if not session:
        print("review-gate: no active session state found.", file=sys.stderr)
        return 1
    state = load_state(session)
    gate = state.get("gate")
    if not gate:
        print("review-gate: no gate is armed, nothing to attach a question to.")
        return 0
    gate["question"] = args.question
    gate["answer_hash"] = hashlib.sha256(normalise(args.answer).encode()).hexdigest()
    gate["answer_plain"] = args.answer
    gate["attempts"] = 0
    save_state(state)
    log_event("quiz_armed", session=session, question=args.question)
    print(
        "Question registered. Ask the user now, then submit their reply verbatim with "
        "`answer`. Do not answer on their behalf — the whole point is that a human read "
        "the code."
    )
    return 0


def cmd_answer(args) -> int:
    session = resolve_session(args.session)
    if not session:
        print("review-gate: no active session state found.", file=sys.stderr)
        return 1
    cfg = load_config()
    state = load_state(session)
    gate = state.get("gate")
    if not gate:
        print("review-gate: no gate is armed.")
        return 0
    if not gate.get("question"):
        print("review-gate: register a question with `arm-quiz` first.", file=sys.stderr)
        return 1
    if marker_present(gate):
        print(
            f"review-gate: stage 1 is still outstanding — the marker is in "
            f"{gate['marker_file']} (line {gate['marker_line']}). The user deletes it "
            f"before the question counts.",
            file=sys.stderr,
        )
        return 1

    gate["attempts"] = gate.get("attempts", 0) + 1
    if answer_matches(args.text, gate.get("answer_plain", "")):
        gate["quiz_passed"] = True
        save_state(state)
        log_event("quiz_passed", session=session, attempts=gate["attempts"], given=args.text)
        if evaluate_gate(state, cfg):
            clear_gate(state, cfg)
            print("Gate cleared. Counter reset — carry on.")
        return 0

    log_event("quiz_failed", session=session, attempts=gate["attempts"], given=args.text)

    if gate["attempts"] >= 3:
        # Three misses usually means the question was the problem, not the human.
        # Voiding it keeps the gate from turning into a guessing game.
        gate["question"] = None
        gate["answer_plain"] = None
        gate["answer_hash"] = None
        gate["attempts"] = 0
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
        f"why, point them at the specific code, and let them try again. The gate stays "
        f"armed.",
        file=sys.stderr,
    )
    return 1


def cmd_status(args) -> int:
    cfg = load_config()
    session = resolve_session(args.session)
    threshold = threshold_for(cfg)
    if not session:
        print(f"review-gate: {cfg.get('level')} level, trips at {threshold} lines. No session yet.")
        return 0
    state = load_state(session)
    current = state.get("lines_since_review", 0)
    print(f"review-gate — {cfg.get('level')} level, gate trips at {threshold} lines")
    print(f"  since last review : {current} lines ({max(0, threshold - current)} to go)")
    print(f"  session total     : {state.get('lines_total', 0)} lines")
    print(f"  gates passed      : {state.get('gates_passed', 0)}")
    if state.get("files"):
        print("  files touched:")
        print(file_summary(state))
    gate = state.get("gate")
    if gate:
        print("\n  GATE ARMED")
        print(f"    marker  : {gate['marker_file']}:{gate['marker_line']}"
              f" — {'still present' if marker_present(gate) else 'cleared'}")
        if cfg.get("quiz_enabled", True):
            state_word = "passed" if gate.get("quiz_passed") else (
                "awaiting answer" if gate.get("question") else "no question set"
            )
            print(f"    question: {state_word}")
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
    session = resolve_session(args.session)
    if not session:
        print("review-gate: no active session state found.", file=sys.stderr)
        return 1
    state = load_state(session)
    if state.get("gate"):
        print(stage_message(state, state["gate"], cfg))
        return 0
    if not state.get("files"):
        print("review-gate: nothing has been written yet this session.")
        return 0
    print(arm_gate(state, cfg, state.get("cwd", "")))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="gate.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("hook-post-tool", "hook-pre-write", "hook-pre-bash", "hook-session-start"):
        sub.add_parser(name)

    quiz = sub.add_parser("arm-quiz")
    quiz.add_argument("--session")
    quiz.add_argument("--question", required=True)
    quiz.add_argument("--answer", required=True)

    ans = sub.add_parser("answer")
    ans.add_argument("--session")
    ans.add_argument("text")

    for name in ("status", "review"):
        node = sub.add_parser(name)
        node.add_argument("--session")

    lvl = sub.add_parser("level")
    lvl.add_argument("level", nargs="?", choices=["strict", "medium", "loose"])

    args = parser.parse_args()

    hooks = {
        "hook-post-tool": hook_post_tool,
        "hook-pre-write": hook_pre_write,
        "hook-pre-bash": hook_pre_bash,
        "hook-session-start": hook_session_start,
    }
    if args.command in hooks:
        try:
            return hooks[args.command](read_hook_input())
        except Exception as exc:  # never wedge the session on a bug in here
            print(f"review-gate hook error: {exc}", file=sys.stderr)
            return 0

    cli = {
        "arm-quiz": cmd_arm_quiz,
        "answer": cmd_answer,
        "status": cmd_status,
        "level": cmd_level,
        "review": cmd_review,
    }
    return cli[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

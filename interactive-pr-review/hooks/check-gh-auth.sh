#!/usr/bin/env bash
# SessionStart guardrail for the interactive-pr-review plugin.
#
# Verifies the GitHub CLI is installed and authenticated so that /interactive-pr-review
# can fetch PRs and post review comments. This is a non-blocking, informational check:
# it only emits a warning (via additionalContext) when something is off, and stays
# completely silent when everything is fine — it never blocks the session.
#
# Claude Code passes the hook event JSON on stdin; we don't need it here.

set -uo pipefail

warn() {
  # Emit structured JSON so Claude Code surfaces the message as extra context.
  # Using a heredoc keeps the message readable and safely quoted.
  local msg="$1"
  cat <<JSON
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "[interactive-pr-review] ${msg}"
  }
}
JSON
  exit 0
}

# 1. Is gh installed?
if ! command -v gh >/dev/null 2>&1; then
  warn "GitHub CLI (gh) was not found on PATH. The /interactive-pr-review:review command needs it to fetch PRs and post comments. Install it from https://cli.github.com and run 'gh auth login'."
fi

# 2. Is gh authenticated? (gh auth status exits non-zero when not logged in.)
if ! gh auth status >/dev/null 2>&1; then
  warn "GitHub CLI is installed but not authenticated. Run 'gh auth login' before using /interactive-pr-review:review, or it will be unable to fetch PRs or post comments."
fi

# All good — say nothing, add nothing.
exit 0

#!/usr/bin/env python3
"""SessionStart hook — auto-restore the just-cleared thread on /clear.

The UserPromptSubmit guard keeps a bookmark of the current session in the stash.
When you run /clear, Claude Code ends that session and starts a NEW one, firing
SessionStart with source=="clear". This hook then reads the bookmark and injects
the slimmed previous session as fresh context — so /clear alone picks up exactly
where you left off, cheaply (it runs in the new, empty session).

Gating notes (all verified against CLI v2.1.170, see PROTOCOL.md):
  - We gate on source=="clear": the terminal CLI reports it correctly. This is
    what stops the bookmark from leaking into an ordinary new session ("startup")
    or a --resume. (The native VS Code/Cursor extension mis-reports source as
    "startup" — anthropics/claude-code#49937 — and doesn't fire UserPromptSubmit
    to write a stash anyway, so auto-restore is CLI-only; use /pickup there.)
  - It only acts when a fresh stash exists, so an ordinary /clear with no prior
    activity is untouched.
  - auto_restore_on_clear (config) lets a user opt out and get a plain /clear;
    the stash is still written, so manual /pickup keeps working.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slim_history import consume_pending, load_config  # noqa: E402


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("source") != "clear":
        sys.exit(0)

    if not load_config()["auto_restore_on_clear"]:
        sys.exit(0)

    out = consume_pending()
    if not out:
        sys.exit(0)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": out,
        }
    }))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""SessionStart hook — auto-pickup on /clear.

When the stale-guard hook intercepts a message in an idle chat it stashes that
session. If the user then runs /clear, this hook fires with source == "clear",
reads the stash, and injects the slimmed previous session as fresh context — so
/clear ALONE restores the conversation, with no second command. It only acts when
a stash exists, so ordinary /clear (fresh start) is untouched.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slim_history import consume_pending  # noqa: E402


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("source") != "clear":
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

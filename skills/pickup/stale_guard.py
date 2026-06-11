#!/usr/bin/env python3
"""UserPromptSubmit hook for the `pickup` skill — always-write stash + stale guard.

This fires on every prompt (terminal CLI only; it does not run in the native VS
Code/Cursor extension panel — anthropics/claude-code#15021). It does two things:

  1. ALWAYS bookmarks the current session to PENDING_FILE (transcript_path +
     session_id). Because /clear starts a brand-new session, this is the only
     moment the pre-clear session identity is known. Keeping the bookmark current
     on every prompt is what lets /clear (or a no-arg /pickup) restore the exact
     thread you were just in — deterministically, no guessing among open chats.

  2. If the chat has been idle longer than `stale_seconds`, it ALSO blocks the
     prompt (exit 2 + stderr — the supported UserPromptSubmit block mechanism)
     and records the typed prompt so it can resurface after /clear. Continuing in
     a stale chat re-sends the full context and burns cache/quota; /clear instead.

Slash commands ('/') and the explicit escape hatch ('!') pass through untouched
and do NOT rewrite the stash, so /clear preserves a pending prompt from a block.
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slim_history import write_pending, load_config  # noqa: E402


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # malformed input: never interfere

    prompt = (data.get("prompt") or "").strip()
    transcript_path = data.get("transcript_path") or ""
    session_id = data.get("session_id") or ""

    # Let slash commands and the explicit escape hatch through WITHOUT touching the
    # stash (so /clear keeps any pending prompt a prior block recorded).
    if prompt.startswith("/") or prompt.startswith("!"):
        sys.exit(0)

    if not transcript_path or not os.path.exists(transcript_path):
        sys.exit(0)

    try:
        idle = time.time() - os.path.getmtime(transcript_path)
    except OSError:
        sys.exit(0)

    stale_seconds = load_config()["stale_seconds"]

    if idle < stale_seconds:
        # Fresh chat: just keep the bookmark current and let the prompt through.
        write_pending(transcript_path, session_id)
        sys.exit(0)

    # Stale chat: bookmark + record the pending prompt, then block.
    write_pending(transcript_path, session_id, prompt)

    mins = int(idle // 60)
    reason = (
        f"⚠️ This chat has been idle ~{mins} min. Continuing here re-sends the "
        f"full stale context and burns cache/quota.\n\n"
        f"Just run  /clear  — this session is restored automatically as a slimmed "
        f"transcript in the fresh context, and your message below resurfaces.\n\n"
        f"Pending message:\n  \"{prompt}\"\n\n"
        f"(To continue here anyway, resend prefixed with '!'.)"
    )
    # Supported block mechanism for UserPromptSubmit: exit code 2 + stderr.
    sys.stderr.write(reason + "\n")
    sys.exit(2)


if __name__ == "__main__":
    main()

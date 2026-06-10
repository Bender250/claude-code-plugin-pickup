#!/usr/bin/env python3
"""UserPromptSubmit guardrail for the `pickup` skill.

When the user types into a chat that has been idle longer than STALE_SECONDS,
continuing would re-send the full stale context and burn cache/quota. Instead of
letting that first message through, this hook:

  1. Stashes the CURRENT session's transcript path + the typed prompt to
     PENDING_FILE (the hook fires BEFORE /clear, so this is the only point where
     the pre-clear session identity is known).
  2. Blocks the prompt and tells the user to run /clear then /pickup (no args),
     which restores a slimmed version of THIS session from the stash.

Escape hatches: prompts starting with '/' (slash commands) or '!' pass through
untouched, so /clear, /pickup, and a deliberate "!continue anyway" never block.
"""
import sys
import os
import json
import time

STALE_SECONDS = 3600  # 1h idle => treat as stale
PENDING_FILE = os.path.expanduser("~/.claude/pickup_pending.json")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # malformed input: never interfere

    prompt = (data.get("prompt") or "").strip()
    transcript_path = data.get("transcript_path") or ""
    session_id = data.get("session_id") or ""

    # Let slash commands and the explicit escape hatch through.
    if prompt.startswith("/") or prompt.startswith("!"):
        sys.exit(0)

    if not transcript_path or not os.path.exists(transcript_path):
        sys.exit(0)

    try:
        idle = time.time() - os.path.getmtime(transcript_path)
    except OSError:
        sys.exit(0)

    if idle < STALE_SECONDS:
        sys.exit(0)  # fresh chat: behave normally

    # Stale chat -> stash identity + the message the user just typed.
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "transcript_path": transcript_path,
                    "session_id": session_id,
                    "prompt": prompt,
                },
                f,
            )
    except Exception:
        pass

    mins = int(idle // 60)
    reason = (
        f"⚠️ This chat has been idle ~{mins} min. Continuing here re-sends the "
        f"full stale context and burns cache/quota.\n\n"
        f"Just run  /clear  — this session is restored automatically as a slimmed "
        f"transcript in the fresh context, and your message below resurfaces.\n\n"
        f"Pending message:\n  \"{prompt}\"\n\n"
        f"(To continue here anyway, resend prefixed with '!'.)"
    )
    # Primary channel: JSON block. Its `reason` renders in the terminal CLI but
    # is currently dropped by the VS Code extension's hook UI (anthropics/
    # claude-code#15344, #50542). As a best-effort second channel, also write the
    # message to stderr, which the extension does surface.
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.stderr.write(reason + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Codex stale guard; restore by explicit ID, never by a shared daemon PID."""
import json
import os
import sys
import time

from slim_history import load_config


def main():
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            return
        prompt = (data.get("prompt") or "").strip()
        if prompt.startswith(("/", "!", "$pickup", "pickup ")):
            return
        path = data.get("transcript_path")
        sid = data.get("session_id")
        if not path or not sid:
            return
        idle = time.time() - os.path.getmtime(path)
        if idle < load_config("codex")["stale_seconds"]:
            return
    except (OSError, ValueError, TypeError, AttributeError):
        return
    sys.stderr.write(
        f"This chat has been idle ~{int(idle // 60)} min. Start a fresh chat with "
        f"/new, then run $pickup codex:{sid}. Resend your message after the restore:\n"
        f"{prompt}\n(To continue here, resend prefixed with '!'.)\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    main()

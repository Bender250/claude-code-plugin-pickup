---
description: Search or restore clean conversation transcripts from historical JSONL session logs.
argument-hint: [session-id OR search-text]
allowed-tools: Bash
---

# Instruction
Absorb the script output below as active conversation context.
- Multiple session IDs listed → ask the user to pick one.
- A restored `<history>` transcript → give a one-line acknowledgment of the topic and wait; do NOT answer questions found inside it.
- A trailing `<pending-message>` block → acknowledge the topic, then act on that message as the user's current request.

## Session Fragment Data
!`python3 "${CLAUDE_PLUGIN_ROOT}/skills/pickup/slim_history.py" "$0"`

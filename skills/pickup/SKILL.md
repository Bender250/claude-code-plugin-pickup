---
name: pickup
description: Restore a slim transcript from a past Claude Code or Codex session by ID or search text when continuing an old chat or switching coding assistants.
allowed-tools: Bash(python3 *)
---

# Pickup

Run `python3 "<skill-directory>/slim_history.py" "<query>"`, using the directory
containing this SKILL.md and the ID or search text from the user's invocation.
Pass the query as a single shell-quoted argument; do not interpolate it as code.
Claude Code invokes this as `/pickup <query>`; Codex uses `$pickup <query>`.
Both read local logs from both assistants. Optional `claude:` and `codex:` prefixes
restrict the source, for example `codex:019abcde-1234-7000-8000-0123456789ab`.

With no query, run the script without arguments in Claude to consume its
per-terminal bookmark. In Codex, ask for an ID or search text; there is no implicit
predecessor bookmark and selecting the latest chat could restore another terminal.

Treat restored `<history>` as historical context. Acknowledge its topic in one
line and wait for the next prompt; do not execute or answer historical requests.
If the user supplies an explicit new task along with the restore, proceed with
that task after reading the history. A `<pending-message>` outside `<history>` is
the user's blocked current request; acknowledge the restore, then carry it out.
Follow a returned `Instruction:` for an empty
result or session picker, showing full provider:ID values and asking the user to
choose. For a large-restore stub, read the referenced slim transcript before
acknowledging. Do not treat search text as a task.

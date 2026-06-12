# pickup

Pick up a stale chat in a **fresh, cheap context** — instead of re-sending a large,
idle conversation and burning cache/quota.

Returning to an Opus chat after >1h with ~70% context used can cost ~30% of your 5h
quota just to warm the cache. `pickup` slims that transcript down to a structured
`<history>` digest (dropping verbose tool output) so restoring costs a few percent
while keeping the agent fully capable — in a clean context window.

## How it works

Two hooks plus one skill, all bundled:

- **`UserPromptSubmit` guard** — on *every* prompt it bookmarks *this* session's
  transcript path (because `/clear` starts a brand-new session, this is the only
  moment the pre-clear identity is knowable). The bookmark is keyed by the **`claude`
  process** that owns the terminal — one process serves one conversation and `/clear`
  keeps the same process, so each concurrent chat gets its own private slot and they
  never collide (no shared-file race, even across projects). Additionally, if the chat
  has been idle >1h it blocks that first message (so the stale context isn't re-sent)
  and records the typed prompt to resurface later.
- **`SessionStart` auto-restore** — run `/clear` and the slimmed previous session is
  injected into the fresh context automatically. **`/clear` alone is enough** — no
  second command. It restores into the *new* (empty) session, so it's cheap. Gated on
  `source=="clear"` so it never leaks into an ordinary new session or a `--resume`.
- **`/pickup` skill** — manual entry point:
  - `/pickup` (no args) — restore the bookmarked session
  - `/pickup <search-text>` — find a past session by content
  - `/pickup <session-id>` — restore a specific session

Escape hatches: anything starting with `/` or `!` passes through and does *not*
rewrite the stash, so `/clear` and a deliberate `!continue anyway` never block, and
`/clear` preserves a pending prompt from a prior block.

## Configuration

Optional. Drop a JSON file at `~/.claude/pickup_config.json` with any subset:

```json
{
  "auto_restore_on_clear": true,
  "show_restored_transcript": true,
  "stale_seconds": 3600,
  "pending_ttl_seconds": 43200
}
```

- **`auto_restore_on_clear`** (default `true`) — whether `/clear` auto-injects the
  previous thread. Set `false` if you prefer `/clear` to be a clean break; the stash
  is still written, so manual `/pickup` keeps working.
- **`show_restored_transcript`** (default `true`) — on `/clear` auto-restore, also
  render the slimmed transcript back to **you** (via the hook's `systemMessage`
  channel, shown to the user but not re-sent to Claude) so you can read what was
  picked up. Set `false` for a silent restore.
- **`stale_seconds`** (default `3600`) — idle threshold before the guard blocks.
- **`pending_ttl_seconds`** (default `43200` = 12h) — a stash older than this is a
  leftover and is never replayed.

See [`PROTOCOL.md`](PROTOCOL.md) for the verified hook/skill protocol this relies on.

## Editor support

The automatic stale-guard relies on the `UserPromptSubmit` hook, which **only fires
in the terminal CLI**. The VS Code / Cursor native extensions do not fire
`UserPromptSubmit` hooks at all ([claude-code#15021](https://github.com/anthropics/claude-code/issues/15021),
"not planned"), so in those editors the guard is silently skipped — a stale chat is
*not* auto-blocked. The `/pickup` skill still works there for manual restore; just run
`/pickup <session-id>` or `/pickup <search-text>` yourself.

## Install

```
/plugin marketplace add Bender250/claude-code-plugin-pickup
/plugin install pickup@kubicek-plugins
```

Update
```
/plugin marketplace update kubicek-plugins
```

Requires `python3` on PATH. Per-terminal bookmarks live under `~/.claude/pickup/<pid>.json`
(one slot per `claude` process; dead-process slots are swept automatically).

## Layout

```
.claude-plugin/plugin.json        # plugin manifest
.claude-plugin/marketplace.json   # makes this repo its own marketplace
hooks/hooks.json                  # wires both hooks (auto-registered on install)
skills/pickup/SKILL.md            # the /pickup skill
skills/pickup/slim_history.py     # transcript slimmer + skill entry point
skills/pickup/stale_guard.py      # UserPromptSubmit guard
skills/pickup/session_pickup.py   # SessionStart auto-restore
```

# pickup

Pick up a stale chat in a **fresh, cheap context** — instead of re-sending a large,
idle conversation and burning cache/quota.

Returning to an Opus chat after >1h with ~70% context used can cost ~30% of your 5h
quota just to warm the cache. `pickup` slims that transcript down to a structured
`<history>` digest (dropping verbose tool output) so restoring costs a few percent
while keeping the agent fully capable — in a clean context window.

## How it works

Two hooks plus one skill, all bundled:

- **`UserPromptSubmit` guard** — when you type into a chat idle >1h, it blocks that
  first message (so the stale context isn't re-sent) and stashes *this* session's
  transcript path. It fires *before* `/clear`, the only moment the pre-clear session
  identity is knowable.
- **`SessionStart` auto-restore** — run `/clear` and the slimmed previous session is
  injected into the fresh context automatically, with your pending message resurfaced.
  **`/clear` alone is enough** — no second command.
- **`/pickup` skill** — manual entry point:
  - `/pickup` (no args) — restore the stashed session
  - `/pickup <search-text>` — find a past session by content
  - `/pickup <session-id>` — restore a specific session

Escape hatches: anything starting with `/` or `!` passes through, so `/clear` and a
deliberate `!continue anyway` never block.

The 1h staleness threshold is hardcoded as `STALE_SECONDS = 3600` in
`skills/pickup/stale_guard.py`.

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

Requires `python3` on PATH. The per-user stash lives at `~/.claude/pickup_pending.json`.

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

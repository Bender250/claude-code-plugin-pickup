# pickup

Pick up a stale chat in a **fresh, cheap context** — instead of re-sending a large,
idle conversation and burning cache/quota.

Works with **Claude Code and Codex**, including switching between them when one
assistant runs out of quota. The transfer reads local logs; the source assistant
does not need to be running or have quota left.

Returning to an Opus chat after >1h with ~70% context used can cost ~30% of your 5h
quota just to warm the cache. `pickup` slims that transcript down to a structured
`<history>` digest (dropping verbose tool output) so restoring costs a few percent
while keeping the agent fully capable — in a clean context window.

The digest drops verbose tool output and pure slash-command / local-command echoes
(`/model`, `/clear`, their stdout — the agent never sees these in a live chat anyway),
and collapses a restored `/pickup` wrapper to just its payload, so a pickup-of-a-pickup
nests cleanly as `<history>…<history>oldest</history>…</history>` instead of repeating
boilerplate. If the digest is still too large to inline safely, `pickup` injects a
topic + description stub and points the agent at the full slim transcript on disk to
read on demand (see `max_inline_chars`).

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

## Switch between Claude Code and Codex

Start a **fresh chat in the same working directory**, then:

| Destination | Command | Source |
| --- | --- | --- |
| Codex | `$pickup <claude-session-id>` | Claude Code |
| Claude Code | `/pickup <codex-session-id>` | Codex |
| Either | `pickup <search-text>` using its skill syntax above | Both assistants |

IDs and unique ID prefixes resolve across both assistants. Use `claude:<id>` or
`codex:<id>` to select a provider explicitly. Prefixes also work with search text,
for example `$pickup claude:database migration`. Multiple matches show a picker
with full `provider:ID` values; pickup never chooses another open chat for you.

The shared skill restores user and assistant text, tool names, compaction summaries,
and nested pickup history, dropping ordinary tool output and reasoning records.
It acknowledges the restored topic and waits for your next message. Files stay in
your working tree; this transfers conversation context, not repositories, credentials,
permissions, or model state. Both sets of logs must be accessible locally.

Sources: `~/.claude/projects`, `~/.codex/sessions`, and `~/.codex/archived_sessions`.
Custom `CLAUDE_CONFIG_DIR` and `CODEX_HOME` locations are supported. Codex IDs come
from session metadata, rather than its `rollout-<timestamp>-<id>.jsonl` filename.
The parser handles legacy response records and paginated-history `item_completed`
events. These log formats are internal and may need updates for future releases.

In Codex, the bundled `UserPromptSubmit` stale guard blocks an idle prompt and
prints the exact recovery command. Run `/new`, then `$pickup codex:<id>`, then
resend your message. It passes through `/`, `!`, and pickup invocations. It does
not use Claude's per-process bookmark: Codex can share a daemon across chats.
Bare `$pickup` asks for an ID or search text. Claude's `/clear` auto-restore and
bare `/pickup` bookmark behavior remain available.

The Codex guard requires trusted hooks and a non-null `transcript_path`. It uses
transcript modification time, so resuming or a runtime that touches the log before
the hook may prevent idle detection. Manual pickup works independently of hooks.

## Configuration

Optional. Drop a JSON file at `~/.claude/pickup_config.json` with any subset:

```json
{
  "auto_restore_on_clear": true,
  "show_restored_transcript": true,
  "stale_seconds": 3600,
  "pending_ttl_seconds": 43200,
  "max_inline_chars": 9000
}
```

Codex's stale guard reads `stale_seconds` from `$CODEX_HOME/pickup_config.json`
(default `~/.codex/pickup_config.json`). Other automatic restore settings apply to
Claude. Manual restores use the same 9000-character inline limit; in a Codex shell
with `CODEX_THREAD_ID`, `max_inline_chars` is read from the Codex config. Large
digests are staged under `$CLAUDE_CONFIG_DIR/pickup` (default `~/.claude/pickup`)
for either assistant to read; if that directory is unwritable, the stub points at
the source log instead.

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
- **`max_inline_chars`** (default `9000`) — if the slimmed transcript is larger than
  this, it is **not** inlined (Claude Code silently spills oversized hook output to a
  file and shows Claude only a ~2KB preview — the useless header). Instead a compact
  **topic + description stub** is injected, pointing Claude at the full slim transcript
  (written to `~/.claude/pickup/restore-<id>.txt`) to `Read` on demand. The empirical
  inline ceiling is ~9.2KB, so the default leaves a safety margin.

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

Update from your terminal:
```sh
claude plugin marketplace update kubicek-plugins
claude plugin update pickup@kubicek-plugins
```

Start a new Claude Code session to load the update, or run `/reload-plugins` in
versions that support it. Confirm pickup is version **0.9.0** with
`claude plugin list`. See [Claude's plugin update instructions](https://code.claude.com/docs/en/discover-plugins#update-plugins-now).

Requires `python3` on PATH. Per-terminal bookmarks live under `~/.claude/pickup/<pid>.json`
(one slot per `claude` process; dead-process slots are swept automatically).

### Codex

For the local checkout (tested with Codex CLI 0.160.0):

```sh
codex plugin marketplace add /absolute/path/to/pickup-plugin
codex plugin add pickup@kubicek-plugins
```

Open `/hooks` in Codex to review and trust pickup's stale guard. Codex skips
untrusted plugin hooks; the skill still works. See the official
[plugin packaging](https://developers.openai.com/plugins/build/plugins) and
[hook documentation](https://learn.chatgpt.com/docs/hooks).

For skills-only installation, without the guard:

```sh
mkdir -p ~/.agents/skills
ln -s /absolute/path/to/pickup-plugin/skills/pickup ~/.agents/skills/pickup
```

Codex supports symlinked skills and `$pickup` invocation; see
[local skills](https://learn.chatgpt.com/docs/build-skills). Reload Codex if the
skill does not appear. No MCP server or additional Python dependencies are needed.

### Verify

```sh
python3 -m unittest discover -s tests -v
```

## Layout

```
.claude-plugin/plugin.json        # plugin manifest
.claude-plugin/marketplace.json   # makes this repo its own marketplace
.codex-plugin/plugin.json        # Codex manifest with its own hook configuration
hooks/hooks.json                  # Claude hooks (auto-registered on install)
hooks/codex.json                  # Codex stale guard
skills/pickup/SKILL.md            # shared /pickup / $pickup skill
skills/pickup/slim_history.py     # transcript slimmer + skill entry point
skills/pickup/session_sources.py  # discovery + Codex log adapters
skills/pickup/codex_guard.py      # Codex UserPromptSubmit guard
skills/pickup/stale_guard.py      # UserPromptSubmit guard
skills/pickup/session_pickup.py   # SessionStart auto-restore
tests/test_sessions.py           # cross-assistant restore and hook regressions
```

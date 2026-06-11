# Pickup Protocol — Verified Reference

This document records the **Claude Code hook/skill protocol facts** that the
`pickup` plugin depends on, each marked with how it was verified:

- **[DOC]** — official docs (https://code.claude.com/docs/en/hooks.md etc.)
- **[BIN]** — string/behaviour evidence from the installed CLI bundle
  (`/opt/homebrew/Caskroom/claude-code@latest/2.1.170/claude`)
- **[OBS]** — observed directly in real session transcripts on this machine
- **[ISSUE]** — corroborated by a tracked anthropics/claude-code GitHub issue

Last verified: 2026-06-11, CLI v2.1.170.

---

## 1. Execution environment matters: CLI vs native extension

There are **two different runtimes** and they behave differently:

| Runtime | `CLAUDE_CODE_ENTRYPOINT` | Hooks fire? |
|---|---|---|
| `claude` in a terminal (incl. **VS Code / Cursor integrated terminal**) | `cli` | **Yes** |
| Native IDE extension chat panel (VS Code / Cursor) | (extension) | **No** for `UserPromptSubmit`; `SessionStart` fires but with wrong `source` |

- **[OBS]** This machine runs the **CLI inside Cursor's terminal**
  (`CLAUDE_CODE_ENTRYPOINT=cli`, `VSCODE_GIT_ASKPASS_NODE=.../Cursor.app`).
  Hooks **do** fire here. "VS Code extension" in earlier discussion conflated
  the integrated terminal (CLI, hooks work) with the native panel (hooks don't).
- **[ISSUE #15021]** "UserPromptSubmit hooks not working in VS Code/Cursor
  extensions" — confirmed, status *not planned*. Applies to the **native panel**.
- **[ISSUE #49937]** "VSCode extension: SessionStart hook receives
  `source='startup'` after `/clear` instead of `'clear'`" — the wrong-source bug
  is **extension-specific**; the **terminal CLI sends `source="clear"` correctly**.

> Correction to earlier claims: the CLI does **not** suffer the wrong-`source`
> bug. A previous diagnosis that "the CLI source-gate was the bug" was wrong.

---

## 2. Hook payload (stdin JSON)

- **[DOC]** All hooks receive `session_id`, `transcript_path`, `cwd`,
  `permission_mode`, `hook_event_name`. **[BIN]** binary contains
  `"transcript_path"`, `"session_id"`, `"agent_transcript_path"`.
- **[DOC]** Only `SessionStart` carries `source`.
- **[DOC/BIN]** `SessionStart.source` ∈ `{startup, resume, clear, compact}`.

---

## 3. `/clear` behaviour (the linchpin) — [OBS], decisive

`/clear` **ends the current session and starts a NEW one** (new UUID, new
`.jsonl`). The pre-clear transcript is left static.

Evidence from a real run in `…/law_collector` on 2026-06-11:

1. Session `0dcfbbee…` last line (line 238) is the stale-guard block:
   `"UserPromptSubmit operation blocked by hook: ⚠️ This chat has been idle ~241 min…"`,
   `entrypoint:"cli"`, timestamp **14:03:14Z**. The file ends there.
2. Session `79a05e28…` **first** timestamp is **14:03:36Z** (22s later) — created
   by the `/clear`.
3. `79a05e28…` lines 2–3 contain
   `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":
   "Restored session: …/0dcfbbee….jsonl …"}}`
   injected as a `hook_additional_context` attachment.

**Conclusion:** the full chain (block → stash → `/clear` → SessionStart restore)
**worked end-to-end in the CLI.** `/clear` does not keep the session id; restore
lands in the *new* post-clear session, which is why it's cheap.

---

## 4. Blocking a `UserPromptSubmit`

- **[DOC/BIN]** Two forms exist: top-level `{"decision":"block","reason":…}` on
  exit 0, **or** exit code 2 with the reason on **stderr**.
- **[OBS]** The current `stale_guard.py` uses **exit 2 + stderr** and it is
  honoured (recorded verbatim as a `system/informational` block message).
- `hookSpecificOutput.permissionDecision` is **PreToolUse-only** — do not use it
  to block a prompt.

---

## 5. `additionalContext` injection

- **[DOC]** `{"hookSpecificOutput":{"hookEventName":"SessionStart",
  "additionalContext":"…"}}` on exit 0 is the documented way to inject context.
- **[OBS]** Confirmed working — see §3, stored as a `hook_additional_context`
  attachment in the new session.

---

## 6. Environment variables available to a *skill* (bash command)

- **[OBS]** `CLAUDE_CODE_SESSION_ID` **is present** in the shell env of a
  Bash/skill invocation (observed value matched the active session's transcript
  filename). Also present: `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_EXECPATH`,
  `CLAUDECODE=1`, `CLAUDE_EFFORT`.
- **[DOC]** *silent* — these are not documented for skills, only hook vars
  (`CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`,
  `CLAUDE_ENV_FILE`, `CLAUDE_CODE_REMOTE`, `CLAUDE_EFFORT`) are. Treat
  `CLAUDE_CODE_SESSION_ID` as **observed-but-undocumented** (may change).
- **[BIN]** binary defines `CLAUDE_CODE_SESSION_ID` (+ `_LOG`, `_NAME`, `_KIND`).

**Implication:** a skill *can* learn the current session id without a hook — but
after `/clear` the current id is the *new* session, so the skill still needs the
**pre-clear** id, which only a hook (or a previously-written stash) can supply.

---

## 7. Other known platform flakiness (CLI)

- **[ISSUE #10997]** SessionStart hooks may not run on the **first** invocation
  for GitHub-marketplace plugins (async marketplace fetch races the hook).
  *Status: completed.*
- **[ISSUE #11939]** Local plugins (`isLocal:true`) match SessionStart hooks but
  never execute them. *Status: completed.* (Develop via the published repo, not a
  local install.)

---

## 8. Why "it doesn't work" in practice — the real failure mode

The mechanism is sound; the **trigger preconditions** are the friction:

1. The stash is only written when the stale-guard **blocks** a prompt, which
   needs the chat to be **>1h idle by transcript mtime**.
2. `--resume` / `--continue` **touch the transcript**, refreshing mtime → the
   idle test never trips on a resumed session.
3. Typing into a *fresh* chat (e.g. right after a `/clear`) is never >1h idle →
   no block → no stash → next `/clear` is an ordinary clear (correct by design).

So most "it did nothing" reports are **no-stash** situations, not restore bugs.

---

## 9. Design implications (verified-safe)

- A hook that **always** records the current `transcript_path` to the stash on
  every `UserPromptSubmit` makes restore **deterministic and instantly testable**
  (type → `/clear` → restore), with **no 1h-idle requirement**. **CLI-only** (the
  writer hook is dead in the native extension).
- **Concurrency (the race fix):** a single global stash file collides when several
  chats are open — whichever typed last wins, so `/clear` in chat A can restore
  chat B. **[OBS]** confirmed: a `law_collector` `/clear` restored an unrelated
  `pickup-plugin` chat because they shared `~/.claude/pickup_pending.json`.
  The fix is **per-process keying**: one `claude` process serves one conversation,
  and `/clear` does **not** fork (same process, same PID), so the bookmark is keyed
  by the ancestor `claude` PID (`~/.claude/pickup/<pid>.json`, found by walking the
  hook's process ancestry — **[OBS]** verified `python3 → zsh → claude`). Each
  terminal gets a private slot; two chats can never collide, even in the same
  project. Dead-PID slots are swept on read.
- **No deterministic predecessor link exists.** **[OBS]** the post-`/clear`
  session has `parentUuid: null` and records no cleared-session id — only `cwd`.
  So the new session cannot *ask CC* which session it replaced; the per-process
  bookmark (written before the clear, read after, same process) is what supplies it.
- Bare `/pickup` reading that stash works as a manual restore. Because it runs in
  the **post-`/clear` (fresh, cheap) session**, it does **not** re-send the stale
  context — the cache concern is unfounded **as long as you `/pickup` after
  `/clear`, never before.**
- The native extension cannot auto-restore (no `UserPromptSubmit`, wrong
  `SessionStart.source`); there, `/pickup <id>` / `/pickup <search>` is the path.

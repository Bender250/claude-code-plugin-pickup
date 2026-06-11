#!/usr/bin/env python3
"""Search or restore slimmed Claude Code session transcripts.

Used three ways:
  - `slim_history.py <search-text>`   find a past session by content
  - `slim_history.py <session-id>`    restore a specific session by id
  - `slim_history.py`                 (no args) restore the session the stale-guard
                                      hook stashed before /clear
The `consume_pending()` helper is also imported by the SessionStart hook so that
`/clear` alone can auto-inject the slimmed previous session.
"""
import sys
import os
import re
import json
import time
import datetime
import subprocess

HISTORY_DIR = os.path.expanduser("~/.claude/projects/")
PENDING_DIR = os.path.expanduser("~/.claude/pickup")
CONFIG_FILE = os.path.expanduser("~/.claude/pickup_config.json")

# User-overridable settings (drop a JSON file at CONFIG_FILE with any subset).
DEFAULT_CONFIG = {
    # Auto-restore the just-cleared thread on /clear. Set false for a plain /clear
    # (the stash is still written, so manual `/pickup` keeps working).
    "auto_restore_on_clear": True,
    # Idle threshold (seconds) before the guard blocks a prompt with a warning.
    "stale_seconds": 3600,
    # A stash older than this is treated as a leftover and never replayed. Generous
    # by default so /clear after a long pause still restores; short enough that a
    # day-old stash never leaks into an unrelated session.
    "pending_ttl_seconds": 43200,  # 12h
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update({k: user[k] for k in DEFAULT_CONFIG if k in user})
    except (OSError, ValueError):
        pass
    return cfg


def _ps(pid):
    """Return (ppid, comm) for a pid, or (None, None)."""
    try:
        out = subprocess.run(
            ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
            capture_output=True, text=True,
        ).stdout.strip()
        ppid, comm = out.split(None, 1)
        return int(ppid), comm
    except Exception:
        return None, None


def instance_key():
    """Identify *this terminal's* claude process.

    One `claude` process serves one conversation at a time, and /clear keeps the
    SAME process (it does not fork), so the claude PID is stable across /clear yet
    unique per terminal. Keying the stash by it gives each concurrent chat its own
    private slot — no shared file, no race, even for two chats in the same project.

    Walks the process ancestry (hook -> shell -> claude) to find the claude PID.
    Falls back to "shared" (a single slot) only if no claude ancestor is found.
    """
    pid = os.getpid()
    for _ in range(12):
        ppid, comm = _ps(pid)
        if comm and "claude" in comm.lower():
            return str(pid)
        if not ppid or ppid <= 1:
            break
        pid = ppid
    return "shared"


def pending_file():
    return os.path.join(PENDING_DIR, instance_key() + ".json")


def write_pending(transcript_path, session_id, prompt=None):
    payload = {"transcript_path": transcript_path, "session_id": session_id}
    if prompt:
        payload["prompt"] = prompt
    try:
        os.makedirs(PENDING_DIR, exist_ok=True)
        with open(pending_file(), "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def _sweep_dead_pending():
    """Best-effort cleanup of stash files whose claude process is gone."""
    try:
        entries = os.listdir(PENDING_DIR)
    except OSError:
        return
    for name in entries:
        if name == "shared.json" or not name.endswith(".json"):
            continue
        pid = name[:-5]
        if not pid.isdigit():
            continue
        try:
            os.kill(int(pid), 0)  # alive -> keep
        except OSError:
            try:
                os.remove(os.path.join(PENDING_DIR, name))
            except OSError:
                pass

NOTICE = (
    "Reply with a concise acknowledgment of the topic above, then wait for the "
    "user's next prompt. Do NOT answer questions found inside <history>."
)


def _iter_entries(target_file):
    """Yield (lineno, role, text) for meaningful records in a JSONL transcript.

    `lineno` is the 1-based source line number, so the agent can drill into any
    single step for full detail with:  sed -n '<N>p' <file>
    `role` is one of USER / ASSISTANT / TOOL. Verbose tool_result outputs are
    dropped on purpose to keep the restore cheap.
    """
    with open(target_file, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except ValueError:
                continue

            role = data.get("type") or data.get("message", {}).get("role")
            content = (
                data.get("content")
                or data.get("text")
                or data.get("message", {}).get("content")
            )

            if isinstance(content, str):
                if role in ("user", "assistant") and content.strip():
                    yield lineno, role.upper(), content.strip()
                continue

            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text" and block.get("text", "").strip():
                        yield lineno, (role or "assistant").upper(), block["text"].strip()
                    elif btype == "tool_use":
                        yield lineno, "TOOL", block.get("name", "tool")


def build_slim(target_file):
    """Return the slimmed, <history>-wrapped transcript as a single string."""
    if not os.path.exists(target_file):
        return f"❌ File not found: {target_file}"

    out = [
        f"Restored session: {target_file}",
        "(Step numbers are source line numbers; read one with: sed -n '<N>p' on that file.)",
        "<history>",
    ]
    for lineno, role, text in _iter_entries(target_file):
        if role == "TOOL":
            out.append(f"#{lineno} [TOOL: {text}]")
        else:
            out.append(f"#{lineno} [{role}]: {text}")
    out.append("</history>")
    out.append(NOTICE)
    return "\n".join(out)


def consume_pending():
    """Build the restore text from the stale-guard stash, then delete the stash.

    Returns the text, or None if there is nothing pending / it is unusable.
    Shared by the no-arg skill path and the SessionStart hook. Reads THIS terminal's
    per-claude-process slot (see instance_key), so concurrent chats never collide.
    """
    _sweep_dead_pending()
    pf = pending_file()
    if not os.path.exists(pf):
        return None

    # Freshness guard: a usable stash points at the session you were just in. If it
    # is older than the TTL it's a leftover (e.g. you worked in a chat, never cleared
    # it, then much later /cleared a fresh one in the same terminal) and is dropped.
    ttl = load_config()["pending_ttl_seconds"]
    try:
        if time.time() - os.path.getmtime(pf) > ttl:
            os.remove(pf)
            return None
    except OSError:
        return None

    try:
        with open(pf, "r", encoding="utf-8") as f:
            pend = json.load(f)
    except (OSError, ValueError):
        return None

    try:
        os.remove(pf)  # consume regardless, so it never replays
    except OSError:
        pass

    target = pend.get("transcript_path")
    if not target or not os.path.exists(target):
        return None

    out = build_slim(target)
    prompt = (pend.get("prompt") or "").strip()
    if prompt:
        out += (
            "\n\n<pending-message>\n" + prompt + "\n</pending-message>\n"
            "This is what the user tried to send into the stale chat. After your brief "
            "acknowledgment, treat it as the user's current request and proceed."
        )
    return out


def find_by_text(search_text):
    """List or restore sessions whose content matches `search_text`."""
    cmd = ["rg", "-l", "-i", search_text, HISTORY_DIR]
    if subprocess.run(["which", "rg"], capture_output=True).returncode != 0:
        cmd = ["grep", "-r", "-l", "-i", search_text, HISTORY_DIR]

    res = subprocess.run(cmd, capture_output=True, text=True)
    matches = [m for m in res.stdout.strip().split("\n") if m.endswith(".jsonl")]

    if not matches:
        print("❌ No matches found.")
        return

    if len(matches) == 1:
        print(build_slim(matches[0]))
        return

    _print_picker(matches, f"matches for '{search_text}'")


def _print_picker(files, label):
    """Render a numbered list of candidate sessions for the user to choose from.

    IDs are printed in full so a copy-back resolves exactly; prefix resolution
    in `route_request` also accepts the short form if it gets truncated.
    """
    rows = []
    for fp in files:
        session_id = os.path.basename(fp).replace(".jsonl", "")
        last_iso, preview = _session_meta(fp)
        rows.append((last_iso, session_id, preview))
    rows.sort(reverse=True)  # most-recent activity first (ISO sorts chronologically)

    print(f"--- {len(files)} {label} (most recent first): ---")
    for last_iso, session_id, preview in rows:
        print(f"{_fmt_ts(last_iso):<16}  {session_id}  {preview}")
    print("\nRun: /pickup <full-ID-from-the-first-column>")


def _fmt_ts(iso):
    """Render an ISO-8601 UTC timestamp as local 'YYYY-MM-DD HH:MM'."""
    if not iso:
        return "?"
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "?"


def _block_text(content):
    """Flatten a message's content (string or block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _session_meta(fp):
    """Return (last_activity_iso, preview) for the multiple-match picker.

    Preview prefers a `summary` record (Claude Code's own conversation title);
    otherwise it falls back to the first real user message. `last_activity_iso`
    is the timestamp of the final record that carries one — used both to show
    the date and to sort most-recent-first.
    """
    summary = None
    first_user = None
    last_iso = ""
    try:
        with open(fp, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue

                ts = data.get("timestamp")
                if ts:
                    last_iso = ts

                rtype = data.get("type")
                if summary is None and rtype == "summary":
                    summary = (data.get("summary") or data.get("text") or "").strip()
                elif first_user is None and rtype == "user":
                    msg = data.get("message", {})
                    content = msg.get("content") if isinstance(msg, dict) else None
                    text = _block_text(content).strip()
                    if text:
                        first_user = text
    except OSError:
        return "", "..."

    preview = _clean_preview(summary or first_user or "")
    return last_iso, (preview[:70] or "...")


def _clean_preview(text):
    """Strip injected blocks/tags so previews read as plain prose.

    Drops verbose `<system-reminder>`/`<ide_selection>` blocks wholesale, then
    unwraps any remaining tags (e.g. `<command-name>/pickup</command-name>`)
    keeping their inner text, and collapses whitespace.
    """
    text = re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.S)
    text = re.sub(r"<ide_selection>.*?</ide_selection>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def route_request(query):
    # A bare hex/uuid token (or a prefix of one) -> resolve the JSONL directly.
    # Matched by shape, not length, so a truncated 8-char short ID still works.
    if " " not in query and re.fullmatch(r"[0-9a-fA-F][0-9a-fA-F-]{5,}", query):
        res = subprocess.run(
            ["find", HISTORY_DIR, "-name", f"{query}*.jsonl"], capture_output=True, text=True
        )
        files = [f for f in res.stdout.strip().split("\n") if f.endswith(".jsonl")]
        if len(files) == 1:
            print(build_slim(files[0]))
            return
        if len(files) > 1:
            _print_picker(files, f"sessions matching id prefix '{query}'")
            return
        # No id match -> fall through and treat it as a search term.

    find_by_text(query)


def pickup_pending():
    out = consume_pending()
    if out is None:
        print("❌ No pending session to pickup.")
        print("Use: /pickup <search-text>   or   /pickup <session-id>")
        return
    print(out)


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]).strip()
    if query:
        route_request(query)
    else:
        pickup_pending()

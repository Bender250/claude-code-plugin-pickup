"""Local Claude and Codex transcript discovery and Codex record adapters.

Codex rollout JSONL is an internal format. Support both response_item records
and the item_completed events used by paginated history, without reading its DB.
"""
import json
import os
import re
from pathlib import Path

UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def roots():
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
    codex = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    return {"claude": [claude / "projects"],
            "codex": [codex / "sessions", codex / "archived_sessions"]}


def records(path):
    with open(path, encoding="utf-8") as stream:
        for lineno, line in enumerate(stream, 1):
            try:
                data = json.loads(line)
            except ValueError:
                continue  # includes an unfinished final record in a live session
            if isinstance(data, dict):
                yield lineno, data


def identity(path):
    """Return (provider, actual session ID), never a rollout timestamp prefix."""
    for _, data in records(path):
        if data.get("type") == "session_meta":
            payload = data.get("payload", {})
            sid = payload.get("id") or payload.get("session_id")
            if sid:
                return "codex", sid
        if data.get("type") in ("user", "assistant"):
            return "claude", Path(path).stem
    name = Path(path).stem
    match = UUID_RE.search(name)
    return ("codex", match.group()) if name.startswith("rollout-") and match else ("claude", name)


def sessions(provider=None):
    found = []
    for source, dirs in roots().items():
        if provider and source != provider:
            continue
        for root in dirs:
            if root.is_dir():
                found.extend(str(p) for p in root.rglob("*.jsonl"))
    return sorted(set(found))


def block_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") in
                         ("text", "input_text", "output_text", "Text"))
    return ""


def restored_payload(value):
    """Keep pickup's own context inside tool output, dropping other tool output.

Unified exec can wrap a script's stdout in JSON objects or text content blocks.
Decode those wrappers so a pickup-of-a-pickup retains its earlier history.
"""
    if isinstance(value, str):
        if "Restored session:" in value and "\n<history>" in value:
            start = value.index("Restored session:")
            end = value.rfind("\n</history>")
            if end >= start:
                return value[start:end + len("\n</history>")]
        if "Restored session (large — NOT inlined):" in value:
            return value[value.index("Restored session (large — NOT inlined):"):]
        try:
            decoded = json.loads(value)
        except ValueError:
            return ""
        if isinstance(decoded, (dict, list)):
            return restored_payload(decoded)
    elif isinstance(value, dict):
        for key in ("output", "text", "content"):
            result = restored_payload(value.get(key))
            if result:
                return result
    elif isinstance(value, list):
        return "\n".join(filter(None, (restored_payload(x) for x in value)))
    return ""


def codex_entries(path):
    """Yield source line, role, text, suppressing mirrored event representations."""
    seen = {}
    for lineno, data in records(path):
        kind = data.get("type")
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        if kind == "event_msg" and ptype == "task_started":
            seen.clear()
        role = text = None
        representation = kind
        if kind == "response_item":
            if ptype == "message" and payload.get("role") in ("user", "assistant"):
                role = payload["role"].upper()
                text = block_text(payload.get("content"))
            elif ptype in ("function_call", "custom_tool_call"):
                role, text = "TOOL", payload.get("name", "tool")
            elif ptype == "web_search_call":
                role, text = "TOOL", "web_search"
            elif ptype in ("function_call_output", "custom_tool_call_output"):
                role, text = "USER", restored_payload(payload.get("output"))
        elif kind == "event_msg":
            if ptype in ("user_message", "agent_message"):
                role = "USER" if ptype == "user_message" else "ASSISTANT"
                text = payload.get("message", "")
            elif ptype == "item_completed":
                representation = "item_completed"
                item = payload.get("item", {})
                itype = item.get("type")
                if itype in ("UserMessage", "AgentMessage", "userMessage", "agentMessage"):
                    role = "USER" if itype.lower() == "usermessage" else "ASSISTANT"
                    text = block_text(item.get("content")) or item.get("text", "")
                elif itype in ("CommandExecution", "commandExecution", "FileChange", "fileChange"):
                    role, text = "TOOL", "exec_command" if "command" in itype.lower() else "apply_patch"
        elif kind == "compacted" and payload.get("message"):
            role, text = "ASSISTANT", "[Compaction summary]\n" + payload["message"]
        if not role or not isinstance(text, str) or not text.strip():
            continue
        text = text.strip()
        # These are harness instructions rather than the user's conversation.
        if role == "USER" and text.startswith(("<environment_context>", "# AGENTS.md instructions for ")):
            continue
        if role != "TOOL":
            key = (role, text)
            prior = seen.setdefault(key, representation)
            if prior != representation:
                continue
        yield lineno, role, text

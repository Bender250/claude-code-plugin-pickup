"""Cross-assistant restores and hook regressions, using isolated local logs."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1] / "skills" / "pickup"
sys.path.insert(0, str(SKILL))
import slim_history as slim
import session_sources as sources

SID = "019abcde-1234-7000-8000-0123456789ab"


class SessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
            "CODEX_HOME": str(self.root / "codex"),
        })
        self.env.start()
        self.pending = patch.object(slim, "PENDING_DIR", str(self.root / "pending"))
        self.pending.start()
        self.config = patch.object(slim, "CONFIG_FILE", str(self.root / "config.json"))
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.pending.stop()
        self.env.stop()
        self.tmp.cleanup()

    def log(self, provider, sid=SID, rows=None, archived=False):
        root = sources.roots()[provider][1 if archived else 0]
        name = f"rollout-2026-10-02T10-00-00-{sid}.jsonl" if provider == "codex" else f"{sid}.jsonl"
        path = root / "project" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        records = []
        if provider == "codex":
            records.append({"type": "session_meta", "payload": {"id": sid, "base_instructions": "BASE ONLY"}})
        records.extend(rows or [])
        path.write_text("\n".join(json.dumps(x) for x in records) + "\n")
        return path

    def message(self, role, text):
        return {"type": "response_item", "payload": {"type": "message", "role": role,
                "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}}

    def route(self, query):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            slim.route_request(query)
        return out.getvalue()

    def test_both_directions_and_ambiguous_id(self):
        self.log("claude", rows=[{"type": "user", "message": {"content": "Claude task"}}])
        self.log("codex", rows=[self.message("user", "Codex task")])
        self.assertIn("Claude task", self.route("claude:" + SID))
        self.assertIn("Codex task", self.route("codex:" + SID))
        out = self.route(SID)
        self.assertIn("claude:" + SID, out)
        self.assertIn("codex:" + SID, out)
        self.assertNotIn("<history>", out)

    def test_short_codex_id_and_archived(self):
        self.log("codex", archived=True, rows=[self.message("user", "Archived task")])
        self.assertIn("Archived task", self.route("019abcde"))
        self.assertNotIn("rollout-2026", sources.identity(sources.sessions()[0])[1])

    def test_exact_id_wins_over_longer_prefix(self):
        self.log("claude", sid="abcdef", rows=[{"type": "user", "content": "Exact task"}])
        self.log("claude", sid="abcdef12", rows=[{"type": "user", "content": "Prefix task"}])
        self.assertIn("Exact task", self.route("abcdef"))
        self.assertNotIn("Prefix task", self.route("abcdef"))

    def test_legacy_codex_filters_mirrors_tools_and_instructions(self):
        path = self.log("codex", rows=[
            self.message("system", "SYSTEM ONLY"),
            self.message("user", "<environment_context>SETUP ONLY</environment_context>"),
            self.message("user", "Real request"),
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Real request"}},
            self.message("assistant", "Real answer"),
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "Real answer"}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}},
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "OUTPUT ONLY"}},
            {"type": "response_item", "payload": {"type": "reasoning", "summary": "REASONING ONLY"}},
        ])
        out = slim.build_slim(str(path))
        self.assertEqual(out.count("Real request"), 1)
        self.assertEqual(out.count("Real answer"), 1)
        self.assertIn("[TOOL: exec_command]", out)
        for omitted in ("SYSTEM ONLY", "SETUP ONLY", "OUTPUT ONLY", "REASONING ONLY", "BASE ONLY"):
            self.assertNotIn(omitted, out)

    def test_paginated_and_mixed_history(self):
        path = self.log("codex", rows=[
            self.message("user", "Earlier request"),
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "UserMessage", "content": [{"type": "Text", "text": "New request"}]}}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "AgentMessage", "content": [{"type": "Text", "text": "New answer"}]}}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "CommandExecution", "stdout": "OUTPUT ONLY"}}},
        ])
        out = slim.build_slim(str(path))
        for text in ("Earlier request", "New request", "New answer", "exec_command"):
            self.assertIn(text, out)
        self.assertNotIn("OUTPUT ONLY", out)

    def test_repeated_message_in_new_turn_survives(self):
        path = self.log("codex", rows=[self.message("user", "continue"),
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "continue"}}])
        self.assertEqual(slim.build_slim(str(path)).count("continue"), 2)

    def test_literal_search_and_provider_filter(self):
        self.log("claude", rows=[{"type": "user", "content": "Find --[task] literally"}])
        self.log("codex", rows=[self.message("user", "Find --[task] literally")])
        self.assertIn("<history>", self.route("codex:--[task]"))
        self.assertNotIn("<history>", self.route("--[task]"))
        self.assertIn("No saved session", self.route("BASE ONLY"))

    def test_large_search_uses_stub_and_stages_full_digest(self):
        self.log("codex", rows=[self.message("user", "Big task " + "x" * 10000)])
        out = self.route("Big task")
        self.assertIn("NOT inlined", out)
        staged = list((self.root / "pending").glob("restore-codex-*.txt"))
        self.assertEqual(len(staged), 1)
        self.assertIn("x" * 10000, staged[0].read_text())

    def test_malformed_and_partial_records_keep_source_numbers(self):
        path = self.log("codex", rows=[[], self.message("user", "Task")])
        with path.open("a") as f:
            f.write('{"partial":')
        self.assertIn("#3 [USER]: Task", slim.build_slim(str(path)))

    def test_nested_pickup_payload_retained(self):
        text = "Skill boilerplate\nRestored session: old.jsonl\n<history>\nOld task\n</history>\nIgnore wrapper"
        path = self.log("codex", rows=[self.message("user", text)])
        out = slim.build_slim(str(path))
        self.assertIn("Old task", out)
        self.assertNotIn("Skill boilerplate", out)
        self.assertNotIn("Ignore wrapper", out)

    def test_restored_tool_output_survives_repeated_transfers(self):
        history = "Restored session: original.jsonl\n<history>\nOriginal task\n</history>"
        wrapped = json.dumps({"output": "Tool metadata\n" + history})
        codex = self.log("codex", rows=[{"type": "response_item", "payload": {
            "type": "function_call_output", "output": wrapped}}])
        claude = self.log("claude", rows=[{"type": "user", "message": {"content": [
            {"type": "tool_result", "content": [{"type": "text", "text": history}]}]}}])
        for path in (codex, claude):
            out = slim.build_slim(str(path))
            self.assertIn("Original task", out)
            self.assertNotIn("Tool metadata", out)

    def test_claude_pending_still_restores_and_consumes(self):
        path = self.log("claude", rows=[{"type": "user", "content": "Claude task"}])
        with patch.object(slim, "instance_key", return_value="test"):
            slim.write_pending(str(path), SID, "Next request")
            out = slim.consume_pending()
            self.assertIn("Claude task", out)
            self.assertIn("<pending-message>\nNext request", out)
            self.assertIsNone(slim.consume_pending())

    def hook(self, name, payload):
        return subprocess.run([sys.executable, str(SKILL / name)], input=json.dumps(payload),
                              text=True, capture_output=True)

    def test_codex_guard_stale_fresh_escape_and_malformed(self):
        path = self.log("codex", rows=[self.message("user", "Task")])
        data = {"prompt": "Next request", "session_id": SID, "transcript_path": str(path)}
        self.assertEqual(self.hook("codex_guard.py", data).returncode, 0)
        os.utime(path, (time.time() - 7200, time.time() - 7200))
        blocked = self.hook("codex_guard.py", data)
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("$pickup codex:" + SID, blocked.stderr)
        for prompt in ("!continue", "/new", "$pickup abcdef", "pickup abcdef"):
            self.assertEqual(self.hook("codex_guard.py", dict(data, prompt=prompt)).returncode, 0)
        self.assertEqual(self.hook("codex_guard.py", []).returncode, 0)
        self.assertEqual(self.hook("codex_guard.py", {}).returncode, 0)

    def test_codex_guard_config_and_claude_source_gate(self):
        path = self.log("codex", rows=[self.message("user", "Task")])
        os.utime(path, (time.time() - 7200, time.time() - 7200))
        (self.root / "codex" / "pickup_config.json").write_text('{"stale_seconds": 999999}')
        self.assertEqual(self.hook("codex_guard.py", {
            "prompt": "Next", "session_id": SID, "transcript_path": str(path)}).returncode, 0)
        self.assertEqual(self.hook("session_pickup.py", {"source": "startup"}).stdout, "")
        self.assertEqual(self.hook("session_pickup.py", {"source": "resume"}).stdout, "")


if __name__ == "__main__":
    unittest.main()

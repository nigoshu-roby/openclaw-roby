#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


def _load_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby-self-growth.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_self_growth_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRobySelfGrowth(TestCase):
    def setUp(self):
        self.mod = _load_module()

    def run_main_with(self, env_override, fake_run_cmd):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            runs_log = state_dir / "self_growth_runs.jsonl"
            stdout = io.StringIO()
            audit_events = []
            slack_messages = []

            def fake_append_audit_event(event_name, payload, **kwargs):
                audit_events.append({"event_name": event_name, "payload": payload, **kwargs})

            def fake_send_slack(webhook_url, text):
                slack_messages.append({"webhook_url": webhook_url, "text": text})

            env = {"ROBY_IMMUTABLE_AUDIT": "1", **env_override}
            with (
                patch.object(self.mod, "STATE_DIR", state_dir),
                patch.object(self.mod, "RUNS_LOG", runs_log),
                patch.object(self.mod, "load_env", return_value=env),
                patch.object(self.mod, "run_cmd", side_effect=fake_run_cmd),
                patch.object(self.mod, "append_audit_event", side_effect=fake_append_audit_event),
                patch.object(self.mod, "send_slack", side_effect=fake_send_slack),
                redirect_stdout(stdout),
            ):
                rc = self.mod.main()

            entry = json.loads(runs_log.read_text(encoding="utf-8").strip())
            return {
                "rc": rc,
                "stdout": stdout.getvalue(),
                "entry": entry,
                "audit_events": audit_events,
                "slack_messages": slack_messages,
                "state_dir": state_dir,
            }

    def test_extract_patch_with_diff_header(self):
        text = "prefix\n\ndiff --git a/a.txt b/a.txt\nindex 1..2 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n"
        patch_text = self.mod.extract_patch(text)
        self.assertTrue(patch_text.startswith("diff --git a/a.txt b/a.txt"))
        self.assertIn("@@ -1 +1 @@", patch_text)

    def test_extract_patch_with_fenced_diff(self):
        text = "```diff\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n```"
        patch_text = self.mod.extract_patch(text)
        self.assertTrue(patch_text.startswith("--- a/a.txt"))
        self.assertIn("\n+++ b/a.txt", patch_text)

    def test_extract_patch_no_change(self):
        self.assertEqual(self.mod.extract_patch("NO_CHANGE"), "NO_CHANGE")
        self.assertEqual(self.mod.extract_patch("```NO_CHANGE```"), "NO_CHANGE")

    def test_build_agent_cmd_includes_agent_flag(self):
        cmd = self.mod.build_agent_cmd("main", "hello")
        self.assertIn("--agent", cmd)
        idx = cmd.index("--agent")
        self.assertEqual(cmd[idx + 1], "main")

    def test_build_agent_cmd_fallback_to_main(self):
        cmd = self.mod.build_agent_cmd("", "hello")
        idx = cmd.index("--agent")
        self.assertEqual(cmd[idx + 1], "main")

    def test_format_self_growth_slack_marks_failure(self):
        text = self.mod.format_self_growth_slack(
            timestamp="2026-03-08 01:23:45",
            patch_status="applied",
            test_status="failed",
            rollback_status="ok",
            commit_status="skipped",
            restart_status="skipped",
            report="TEST: failed\nROLLBACK: ok",
        )
        self.assertIn("・実行結果: 失敗あり", text)
        self.assertIn("・テスト: failed", text)
        self.assertIn("■実行ログ（抜粋）", text)

    def test_build_run_entry_uses_fixed_schema(self):
        entry = self.mod.build_run_entry(
            timestamp="2026-03-08 15:00:00",
            git_status="## main",
            patch_status="applied",
            test_status="passed",
            rollback_status="skipped",
            commit_status="ok",
            restart_status="ok",
            slack_status="ok",
            report="TEST: passed",
        )
        self.assertEqual(entry["schema_version"], 2)
        self.assertEqual(entry["patch_status"], "applied")
        self.assertEqual(entry["slack_status"], "ok")
        self.assertIn("ts", entry)

    def test_main_skips_when_git_tree_dirty(self):
        def fake_run_cmd(cmd, env, timeout=60):
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "status"]:
                if "--porcelain" in cmd:
                    return " M scripts/example.py"
                return "## main"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "log"]:
                return "abc123 test"
            raise AssertionError(f"Unexpected command: {cmd}")

        result = self.run_main_with({}, fake_run_cmd)
        self.assertEqual(result["rc"], 0)
        self.assertIn("SKIP: working tree is dirty", result["stdout"])
        self.assertEqual(result["entry"]["patch_status"], "skipped")
        self.assertEqual(result["entry"]["test_status"], "skipped")

    def test_main_records_no_change_without_applying_patch(self):
        def fake_run_cmd(cmd, env, timeout=60):
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "status"]:
                if "--porcelain" in cmd:
                    return ""
                return "## main"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "log"]:
                return "abc123 test"
            if cmd[:2] == ["node", str(self.mod.REPO_DIR / "openclaw.mjs")]:
                return "NO_CHANGE"
            raise AssertionError(f"Unexpected command: {cmd}")

        result = self.run_main_with({}, fake_run_cmd)
        self.assertEqual(result["rc"], 0)
        self.assertIn("PATCH: no_change", result["stdout"])
        self.assertEqual(result["entry"]["patch_status"], "no_change")
        self.assertEqual(result["entry"]["test_status"], "skipped")

    def test_main_records_invalid_patch_and_audits_error(self):
        patch_text = "diff --git a/a.txt b/a.txt\nindex 1..2 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n"

        def fake_run_cmd(cmd, env, timeout=60):
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "status"]:
                if "--porcelain" in cmd:
                    return ""
                return "## main"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "log"]:
                return "abc123 test"
            if cmd[:2] == ["node", str(self.mod.REPO_DIR / "openclaw.mjs")]:
                return patch_text
            if cmd[:5] == ["git", "-C", str(self.mod.REPO_DIR), "apply", "--check", str(Path(env.get("_patch_path", "")))]:
                return "[error] exit=1\npatch does not apply"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "apply"] and "--check" in cmd:
                return "[error] exit=1\npatch does not apply"
            raise AssertionError(f"Unexpected command: {cmd}")

        result = self.run_main_with({"SLACK_WEBHOOK_URL": "https://example.invalid/webhook"}, fake_run_cmd)
        self.assertEqual(result["entry"]["patch_status"], "invalid")
        self.assertEqual(result["audit_events"][0]["severity"], "error")
        self.assertIn("・実行結果: 失敗あり", result["slack_messages"][0]["text"])

    def test_main_rolls_back_on_test_failure_and_audits_error(self):
        patch_text = "diff --git a/a.txt b/a.txt\nindex 1..2 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n"

        def fake_run_cmd(cmd, env, timeout=60):
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "status"]:
                if "--porcelain" in cmd:
                    return ""
                return "## main"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "log"]:
                return "abc123 test"
            if cmd[:2] == ["node", str(self.mod.REPO_DIR / "openclaw.mjs")]:
                return patch_text
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "apply"] and "--check" in cmd:
                return ""
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "apply"] and "-R" not in cmd:
                return ""
            if cmd[:3] == ["bash", "-lc", "pnpm -s test:fast"]:
                return "[error] exit=1\nFAIL example"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "apply"] and "-R" in cmd:
                return ""
            raise AssertionError(f"Unexpected command: {cmd}")

        result = self.run_main_with({}, fake_run_cmd)
        self.assertEqual(result["entry"]["patch_status"], "applied")
        self.assertEqual(result["entry"]["test_status"], "failed")
        self.assertEqual(result["entry"]["rollback_status"], "ok")
        self.assertIn("ROLLBACK: ok", result["stdout"])
        self.assertEqual(result["audit_events"][0]["severity"], "error")

    def test_main_reports_restart_failure_after_successful_run(self):
        patch_text = "diff --git a/a.txt b/a.txt\nindex 1..2 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n"

        def fake_run_cmd(cmd, env, timeout=60):
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "status"]:
                if "--porcelain" in cmd:
                    return ""
                return "## main"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "log"]:
                return "abc123 test"
            if cmd[:2] == ["node", str(self.mod.REPO_DIR / "openclaw.mjs")]:
                return patch_text
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "apply"] and "--check" in cmd:
                return ""
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "apply"] and "-R" not in cmd:
                return ""
            if cmd[:3] == ["bash", "-lc", "pnpm -s test:fast"]:
                return "ok"
            if cmd[:5] == ["git", "-C", str(self.mod.REPO_DIR), "add", "-A"]:
                return ""
            if cmd[:5] == ["git", "-C", str(self.mod.REPO_DIR), "commit", "-m"]:
                return "[main abc123] test"
            if cmd[:3] == ["bash", "-lc", f"node {self.mod.REPO_DIR / 'openclaw.mjs'} gateway restart"]:
                return "[error] exit=1\nrestart failed"
            raise AssertionError(f"Unexpected command: {cmd}")

        result = self.run_main_with({"SLACK_WEBHOOK_URL": "https://example.invalid/webhook"}, fake_run_cmd)
        self.assertEqual(result["entry"]["test_status"], "passed")
        self.assertEqual(result["entry"]["commit_status"], "ok")
        self.assertEqual(result["entry"]["restart_status"], "failed")
        self.assertEqual(result["audit_events"][0]["severity"], "error")
        self.assertIn("・再起動: failed", result["slack_messages"][0]["text"])

    def test_main_records_commit_failure_and_still_marks_audit_error(self):
        patch_text = "diff --git a/a.txt b/a.txt\nindex 1..2 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n"

        def fake_run_cmd(cmd, env, timeout=60):
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "status"]:
                if "--porcelain" in cmd:
                    return ""
                return "## main"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "log"]:
                return "abc123 test"
            if cmd[:2] == ["node", str(self.mod.REPO_DIR / "openclaw.mjs")]:
                return patch_text
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "apply"] and "--check" in cmd:
                return ""
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "apply"] and "-R" not in cmd:
                return ""
            if cmd[:3] == ["bash", "-lc", "pnpm -s test:fast"]:
                return "ok"
            if cmd[:5] == ["git", "-C", str(self.mod.REPO_DIR), "add", "-A"]:
                return ""
            if cmd[:5] == ["git", "-C", str(self.mod.REPO_DIR), "commit", "-m"]:
                return "[error] exit=1\ncommit failed"
            if cmd[:3] == ["bash", "-lc", f"node {self.mod.REPO_DIR / 'openclaw.mjs'} gateway restart"]:
                return "restart ok"
            raise AssertionError(f"Unexpected command: {cmd}")

        result = self.run_main_with({}, fake_run_cmd)
        self.assertEqual(result["entry"]["patch_status"], "applied")
        self.assertEqual(result["entry"]["test_status"], "passed")
        self.assertEqual(result["entry"]["commit_status"], "failed")
        self.assertEqual(result["entry"]["restart_status"], "ok")
        self.assertEqual(result["audit_events"][0]["severity"], "error")

    def test_main_records_slack_failure_and_emits_audit_event(self):
        def fake_run_cmd(cmd, env, timeout=60):
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "status"]:
                if "--porcelain" in cmd:
                    return " M scripts/example.py"
                return "## main"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "log"]:
                return "abc123 test"
            raise AssertionError(f"Unexpected command: {cmd}")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            runs_log = state_dir / "self_growth_runs.jsonl"
            stdout = io.StringIO()
            audit_events = []

            def fake_append_audit_event(event_name, payload, **kwargs):
                audit_events.append({"event_name": event_name, "payload": payload, **kwargs})

            def failing_send_slack(webhook_url, text):
                raise RuntimeError("slack unavailable")

            env = {"ROBY_IMMUTABLE_AUDIT": "1", "SLACK_WEBHOOK_URL": "https://example.invalid/webhook"}
            with (
                patch.object(self.mod, "STATE_DIR", state_dir),
                patch.object(self.mod, "RUNS_LOG", runs_log),
                patch.object(self.mod, "load_env", return_value=env),
                patch.object(self.mod, "run_cmd", side_effect=fake_run_cmd),
                patch.object(self.mod, "append_audit_event", side_effect=fake_append_audit_event),
                patch.object(self.mod, "send_slack", side_effect=failing_send_slack),
                redirect_stdout(stdout),
            ):
                rc = self.mod.main()

            self.assertEqual(rc, 0)
            entry = json.loads(runs_log.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["slack_status"], "failed")
            self.assertIn("[slack_error] slack unavailable", entry["report"])
            slack_error_events = [event for event in audit_events if event["event_name"] == "self_growth.slack_error"]
            self.assertEqual(len(slack_error_events), 1)
            self.assertEqual(slack_error_events[0]["severity"], "error")


if __name__ == "__main__":
    main()

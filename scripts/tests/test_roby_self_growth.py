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
            patch_scope_status="ok",
            test_status="passed",
            rollback_status="skipped",
            commit_status="ok",
            restart_status="ok",
            post_eval_status="ok",
            post_memory_sync_status="ok",
            slack_status="ok",
            report="TEST: passed",
            growth_focus={"summary_text": "GROWTH FOCUS\n- no current focus", "suggested_files": []},
            touched_files=["scripts/roby-self-growth.py"],
            pre_quality={"evaluation_failed": 1, "unresolved_count": 2},
            post_quality={"evaluation_failed": 0, "unresolved_count": 1},
            quality_delta={"evaluation_failed_delta": -1, "unresolved_delta": -1},
        )
        self.assertEqual(entry["schema_version"], 2)
        self.assertEqual(entry["patch_status"], "applied")
        self.assertEqual(entry["patch_scope_status"], "ok")
        self.assertEqual(entry["slack_status"], "ok")
        self.assertIn("ts", entry)
        self.assertIn("growth_focus", entry)
        self.assertEqual(entry["touched_files"], ["scripts/roby-self-growth.py"])

    def test_collect_growth_focus_suggests_candidate_files(self):
        focus = self.mod.collect_growth_focus(
            memory_latest={"unresolved": ["stale component: gmail_triage / notion_sync"]},
            feedback_latest={
                "summary": {
                    "improvement_targets": [
                        {
                            "target": "gmail_finance_contract_detection",
                            "label": "契約・請求判定",
                            "count": 3,
                            "recommendation": "請求・見積・契約更新を review 優先にする。",
                        }
                    ],
                    "actionable_reason_counts": {"billing_contract": 3},
                }
            },
            eval_latest={"failed": 1, "total": 7, "routes": {"gmail_pipeline": {"failed": 1}}},
            drill_latest={"failed": 0, "total": 13},
            weekly_latest={},
        )
        self.assertIn("Candidate files:", focus["summary_text"])
        self.assertIn("skills/roby-mail/scripts/gmail_triage.py", focus["suggested_files"])
        self.assertIn("scripts/roby-notion-sync.py", focus["suggested_files"])

    def test_summarize_growth_focus_prefers_targets_and_quality_signals(self):
        text = self.mod.summarize_growth_focus(
            memory_latest={"unresolved": ["stale component: gmail_triage"]},
            feedback_latest={
                "summary": {
                    "improvement_targets": [
                        {
                            "label": "タスク抽出閾値",
                            "count": 2,
                            "recommendation": "依頼・期限・担当の弱い文を除外する。",
                        }
                    ],
                    "actionable_reason_counts": {"not_actionable": 2},
                    "recent_reviewed": [
                        {
                            "title": "メール確認: 自動支払いが完了しました",
                            "feedback_state": "bad",
                            "feedback_reason_code": "not_actionable",
                        }
                    ],
                }
            },
            eval_latest={"failed": 1, "total": 7, "routes": {"qa_gemini": {"failed": 1}}},
            drill_latest={"failed": 0, "total": 13},
            weekly_latest={},
        )
        self.assertIn("GROWTH FOCUS", text)
        self.assertIn("Priority targets:", text)
        self.assertIn("タスク抽出閾値", text)
        self.assertIn("Unresolved heartbeat:", text)
        self.assertIn("Evaluation: 1/7 failed", text)
        self.assertIn("Runbook drill: 0/13 failed", text)
        self.assertIn("Top feedback reasons:", text)

    def test_collect_growth_focus_prioritizes_targets_with_worse_history(self):
        focus = self.mod.collect_growth_focus(
            memory_latest={},
            feedback_latest={
                "summary": {
                    "improvement_targets": [
                        {
                            "target": "gmail_promo_filtering",
                            "label": "メルマガ判定",
                            "count": 2,
                            "recommendation": "archive 閾値を見直す。",
                        },
                        {
                            "target": "gmail_review_vs_task",
                            "label": "確認タスク判定",
                            "count": 2,
                            "recommendation": "review/task 境界を見直す。",
                        },
                    ]
                }
            },
            eval_latest={},
            drill_latest={},
            weekly_latest={
                "self_growth": {
                    "target_stats": [
                        {
                            "label": "メルマガ判定",
                            "runs": 2,
                            "success_runs": 2,
                            "success_rate": 1.0,
                            "measured_runs": 1,
                            "improved_runs": 1,
                            "improved_rate": 1.0,
                            "latest_patch_status": "no_change",
                        },
                        {
                            "label": "確認タスク判定",
                            "runs": 2,
                            "success_runs": 0,
                            "success_rate": 0.0,
                            "measured_runs": 1,
                            "improved_runs": 0,
                            "improved_rate": 0.0,
                            "latest_patch_status": "failed",
                        },
                    ]
                }
            },
        )
        ranked = focus["ranked_targets"]
        self.assertEqual(ranked[0]["label"], "確認タスク判定")
        self.assertIn("確認タスク判定", focus["summary_text"])
        self.assertIn("latest failed", focus["summary_text"])

    def test_summarize_growth_focus_handles_missing_state(self):
        text = self.mod.summarize_growth_focus({}, {}, {}, {}, {})
        self.assertEqual(text, "GROWTH FOCUS\n- no current focus")

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

    def test_main_includes_growth_focus_in_agent_prompt(self):
        captured = {"prompt": ""}

        def fake_run_cmd(cmd, env, timeout=60):
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "status"]:
                if "--porcelain" in cmd:
                    return ""
                return "## main"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "log"]:
                return "abc123 test"
            if cmd[:2] == ["node", str(self.mod.REPO_DIR / "openclaw.mjs")]:
                captured["prompt"] = cmd[cmd.index("--message") + 1]
                return "NO_CHANGE"
            raise AssertionError(f"Unexpected command: {cmd}")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "evals").mkdir(parents=True)
            (state_dir / "drills").mkdir(parents=True)
            (state_dir / "feedback_sync_state.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "improvement_targets": [
                                {"label": "案件判定", "count": 3, "recommendation": "案件名推定を見直す。"}
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (state_dir / "memory_sync_state.json").write_text(
                json.dumps({"unresolved": ["stale component: gmail_triage"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (state_dir / "evals" / "latest.json").write_text(
                json.dumps({"failed": 1, "total": 7, "routes": {"auto": {"failed": 1}}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (state_dir / "drills" / "latest.json").write_text(
                json.dumps({"failed": 0, "total": 13}, ensure_ascii=False),
                encoding="utf-8",
            )
            runs_log = state_dir / "self_growth_runs.jsonl"
            with (
                patch.object(self.mod, "STATE_DIR", state_dir),
                patch.object(self.mod, "RUNS_LOG", runs_log),
                patch.object(self.mod, "load_env", return_value={"ROBY_IMMUTABLE_AUDIT": "0"}),
                patch.object(self.mod, "run_cmd", side_effect=fake_run_cmd),
                patch.object(self.mod, "append_audit_event"),
                patch.object(self.mod, "send_slack"),
                redirect_stdout(io.StringIO()),
            ):
                rc = self.mod.main()

        self.assertEqual(rc, 0)
        self.assertIn("GROWTH FOCUS", captured["prompt"])
        self.assertIn("案件判定", captured["prompt"])
        self.assertIn("stale component: gmail_triage", captured["prompt"])
        self.assertIn("Candidate files:", captured["prompt"])
        self.assertIn("scripts/roby-orchestrator.py", captured["prompt"])

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

    def test_main_blocks_patch_when_out_of_scope(self):
        patch_text = (
            "diff --git a/ui/src/ui/views/chat.ts b/ui/src/ui/views/chat.ts\n"
            "index 1..2 100644\n"
            "--- a/ui/src/ui/views/chat.ts\n"
            "+++ b/ui/src/ui/views/chat.ts\n"
            "@@ -1 +1 @@\n-a\n+b\n"
        )

        def fake_run_cmd(cmd, env, timeout=60):
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "status"]:
                if "--porcelain" in cmd:
                    return ""
                return "## main"
            if cmd[:4] == ["git", "-C", str(self.mod.REPO_DIR), "log"]:
                return "abc123 test"
            if cmd[:2] == ["node", str(self.mod.REPO_DIR / "openclaw.mjs")]:
                return patch_text
            raise AssertionError(f"Unexpected command: {cmd}")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "feedback_sync_state.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "improvement_targets": [
                                {
                                    "target": "gmail_finance_contract_detection",
                                    "label": "契約・請求判定",
                                    "count": 2,
                                    "recommendation": "請求・見積語を優先する。",
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            runs_log = state_dir / "self_growth_runs.jsonl"
            stdout = io.StringIO()
            with (
                patch.object(self.mod, "STATE_DIR", state_dir),
                patch.object(self.mod, "RUNS_LOG", runs_log),
                patch.object(self.mod, "load_env", return_value={"ROBY_IMMUTABLE_AUDIT": "0"}),
                patch.object(self.mod, "run_cmd", side_effect=fake_run_cmd),
                patch.object(self.mod, "append_audit_event"),
                patch.object(self.mod, "send_slack"),
                redirect_stdout(stdout),
            ):
                rc = self.mod.main()

            self.assertEqual(rc, 0)
            entry = json.loads(runs_log.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["patch_status"], "out_of_scope")
            self.assertEqual(entry["patch_scope_status"], "blocked")
            self.assertEqual(entry["touched_files"], ["ui/src/ui/views/chat.ts"])
            self.assertIn("PATCH: out_of_scope", stdout.getvalue())

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
            if cmd[:3] == ["bash", "-lc", f"python3 {self.mod.REPO_DIR / 'scripts' / 'roby-eval-harness.py'} --json --soft-fail"]:
                return json.dumps({"failed": 0, "total": 7}, ensure_ascii=False)
            if cmd[:3] == ["bash", "-lc", f"python3 {self.mod.REPO_DIR / 'scripts' / 'roby-memory-sync.py'} --json"]:
                return json.dumps({"heartbeat_status": "HEARTBEAT_OK", "unresolved_count": 0}, ensure_ascii=False)
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
            if cmd[:3] == ["bash", "-lc", f"python3 {self.mod.REPO_DIR / 'scripts' / 'roby-eval-harness.py'} --json --soft-fail"]:
                return json.dumps({"failed": 0, "total": 7}, ensure_ascii=False)
            if cmd[:3] == ["bash", "-lc", f"python3 {self.mod.REPO_DIR / 'scripts' / 'roby-memory-sync.py'} --json"]:
                return json.dumps({"heartbeat_status": "HEARTBEAT_OK", "unresolved_count": 0}, ensure_ascii=False)
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

    def test_main_records_quality_delta_after_successful_run(self):
        patch_text = (
            "diff --git a/skills/roby-mail/scripts/gmail_triage.py b/skills/roby-mail/scripts/gmail_triage.py\n"
            "index 1..2 100644\n"
            "--- a/skills/roby-mail/scripts/gmail_triage.py\n"
            "+++ b/skills/roby-mail/scripts/gmail_triage.py\n"
            "@@ -1 +1 @@\n-a\n+b\n"
        )

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
                return "restart ok"
            if cmd[:3] == ["bash", "-lc", f"python3 {self.mod.REPO_DIR / 'scripts' / 'roby-eval-harness.py'} --json --soft-fail"]:
                return json.dumps({"failed": 0, "total": 7}, ensure_ascii=False)
            if cmd[:3] == ["bash", "-lc", f"python3 {self.mod.REPO_DIR / 'scripts' / 'roby-memory-sync.py'} --json"]:
                return json.dumps({"heartbeat_status": "HEARTBEAT_OK", "unresolved_count": 1}, ensure_ascii=False)
            raise AssertionError(f"Unexpected command: {cmd}")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "evals").mkdir(parents=True)
            (state_dir / "drills").mkdir(parents=True)
            (state_dir / "feedback_sync_state.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "improvement_targets": [
                                {
                                    "target": "gmail_finance_contract_detection",
                                    "label": "契約・請求判定",
                                    "count": 2,
                                    "recommendation": "請求・見積語を優先する。",
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (state_dir / "memory_sync_state.json").write_text(
                json.dumps(
                    {"heartbeat_status": "HEARTBEAT_ATTENTION", "unresolved_count": 3, "unresolved": ["stale component: gmail_triage"]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (state_dir / "evals" / "latest.json").write_text(
                json.dumps({"failed": 1, "total": 7}, ensure_ascii=False),
                encoding="utf-8",
            )
            (state_dir / "drills" / "latest.json").write_text(
                json.dumps({"failed": 0, "total": 13}, ensure_ascii=False),
                encoding="utf-8",
            )
            runs_log = state_dir / "self_growth_runs.jsonl"
            stdout = io.StringIO()

            def fake_read_json(path):
                if path.name == "memory_sync_state.json":
                    if getattr(fake_read_json, "post_memory", False):
                        return {"heartbeat_status": "HEARTBEAT_OK", "unresolved_count": 1, "unresolved": []}
                    return {"heartbeat_status": "HEARTBEAT_ATTENTION", "unresolved_count": 3, "unresolved": ["stale component: gmail_triage"]}
                if path.name == "latest.json" and path.parent.name == "evals":
                    if getattr(fake_read_json, "post_eval", False):
                        return {"failed": 0, "total": 7}
                    return {"failed": 1, "total": 7}
                if path.name == "latest.json" and path.parent.name == "drills":
                    return {"failed": 0, "total": 13}
                if path.name == "feedback_sync_state.json":
                    return {
                        "summary": {
                            "improvement_targets": [
                                {
                                    "target": "gmail_finance_contract_detection",
                                    "label": "契約・請求判定",
                                    "count": 2,
                                    "recommendation": "請求・見積語を優先する。",
                                }
                            ]
                        }
                    }
                return {}

            def side_effect(cmd, env, timeout=60):
                result = fake_run_cmd(cmd, env, timeout)
                joined = " ".join(cmd)
                if "roby-eval-harness.py" in joined:
                    fake_read_json.post_eval = True
                if "roby-memory-sync.py" in joined:
                    fake_read_json.post_memory = True
                return result

            with (
                patch.object(self.mod, "STATE_DIR", state_dir),
                patch.object(self.mod, "RUNS_LOG", runs_log),
                patch.object(self.mod, "load_env", return_value={"ROBY_IMMUTABLE_AUDIT": "0"}),
                patch.object(self.mod, "run_cmd", side_effect=side_effect),
                patch.object(self.mod, "read_json", side_effect=fake_read_json),
                patch.object(self.mod, "append_audit_event"),
                patch.object(self.mod, "send_slack"),
                redirect_stdout(stdout),
            ):
                rc = self.mod.main()

            self.assertEqual(rc, 0)
            entry = json.loads(runs_log.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["post_eval_status"], "ok")
            self.assertEqual(entry["post_memory_sync_status"], "ok")
            self.assertEqual(entry["quality_delta"]["evaluation_failed_before"], 1)
            self.assertEqual(entry["quality_delta"]["evaluation_failed_after"], 0)
            self.assertEqual(entry["quality_delta"]["evaluation_failed_delta"], -1)
            self.assertEqual(entry["quality_delta"]["unresolved_before"], 3)
            self.assertEqual(entry["quality_delta"]["unresolved_after"], 1)
            self.assertEqual(entry["quality_delta"]["unresolved_delta"], -2)
            self.assertIn("QUALITY_DELTA:", stdout.getvalue())


if __name__ == "__main__":
    main()

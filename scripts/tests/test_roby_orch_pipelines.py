#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main


def _load_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script_path = scripts_dir / "roby_orch_pipelines.py"
    spec = importlib.util.spec_from_file_location("roby_orch_pipelines_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRobyOrchPipelines(TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_minutes_cron_plan_uses_ops_defaults(self):
        plan = self.mod.build_minutes_pipeline_plan(
            "TOKIWAGIの議事録からタスク抽出して実行して",
            {"ROBY_ORCH_CRON_CONTEXT": "1"},
            minutes_script=Path("/tmp/roby-minutes.py"),
            verbose=False,
            route="minutes_pipeline",
        )

        self.assertEqual(plan["result"]["mode"], "run")
        self.assertIn("--policy", plan["cmd"])
        self.assertIn("ops_default", plan["cmd"])
        self.assertIn("--max", plan["cmd"])
        self.assertIn("4", plan["cmd"])
        self.assertEqual(plan["child_env"].get("MINUTES_LOCAL_PREPROCESS_ENABLE"), "0")
        self.assertEqual(plan["child_env"].get("MINUTES_DOC_TIMEOUT_SEC"), "45")

    def test_minutes_select_plan_keeps_list_mode(self):
        plan = self.mod.build_minutes_pipeline_plan(
            '議事録を確認 --select "doc-123"',
            {},
            minutes_script=Path("/tmp/roby-minutes.py"),
            verbose=True,
            route="minutes_pipeline",
        )

        self.assertEqual(plan["result"]["mode"], "list")
        self.assertIn("--list", plan["cmd"])
        self.assertIn("--select", plan["cmd"])
        self.assertIn("doc-123", plan["cmd"])
        self.assertIn("--debug", plan["cmd"])
        self.assertNotIn("--run", plan["cmd"])

    def test_gmail_plan_uses_account_query_and_fast_profile(self):
        plan = self.mod.build_gmail_pipeline_plan(
            "Gmail整理\nnewer_than:2d in:inbox",
            {
                "ROBY_GMAIL_ACCOUNT": "s.nigo@example.com",
                "ROBY_ORCH_LOCAL_FIRST_SCHEDULE": "0",
                "ROBY_ORCH_GMAIL_PROFILE": "fast",
            },
            gmail_triage_script=Path("/tmp/gmail_triage.py"),
            verbose=False,
            route="gmail_pipeline",
        )

        result = plan["result"]
        self.assertEqual(result["account"], "s.nigo@example.com")
        self.assertEqual(result["query"], "newer_than:2d in:inbox")
        self.assertEqual(result["max"], 20)
        self.assertEqual(result["llm_profile"], "fast")
        self.assertEqual(plan["child_env"].get("GMAIL_TRIAGE_LLM_ENABLE"), "0")
        self.assertIn("--query", plan["cmd"])
        self.assertIn("newer_than:2d in:inbox", plan["cmd"])

    def test_gmail_plan_honors_max_and_dry_run(self):
        plan = self.mod.build_gmail_pipeline_plan(
            "Gmailを5件だけ確認だけ",
            {"GOG_ACCOUNT": "fallback@example.com"},
            gmail_triage_script=Path("/tmp/gmail_triage.py"),
            verbose=True,
            route="gmail_pipeline",
        )

        self.assertEqual(plan["result"]["account"], "fallback@example.com")
        self.assertEqual(plan["result"]["max"], 5)
        self.assertIn("--max", plan["cmd"])
        self.assertIn("5", plan["cmd"])
        self.assertIn("--verbose", plan["cmd"])
        self.assertIn("--dry-run", plan["cmd"])

    def test_notion_sync_plan_includes_project_and_page(self):
        plan = self.mod.build_notion_sync_plan(
            {
                "ROBY_GH_OWNER": "owner-a",
                "ROBY_GH_PROJECT_NUMBER": "7",
                "ROBY_NOTION_SYNC_PAGE_ID": "page-1",
            },
            notion_sync_script=Path("/tmp/roby-notion-sync.py"),
            route="notion_sync",
            dry_run=True,
        )

        self.assertEqual(plan["result"]["route"], "notion_sync")
        self.assertIn("--owner", plan["cmd"])
        self.assertIn("owner-a", plan["cmd"])
        self.assertIn("--project-number", plan["cmd"])
        self.assertIn("7", plan["cmd"])
        self.assertIn("--page-id", plan["cmd"])
        self.assertIn("page-1", plan["cmd"])
        self.assertIn("--dry-run", plan["cmd"])

    def test_json_job_plans_keep_expected_flags(self):
        cases = [
            (
                self.mod.build_feedback_sync_plan,
                {"feedback_sync_script": Path("/tmp/roby-feedback-sync.py"), "route": "feedback_sync", "dry_run": True},
                "feedback_sync",
                ["--json", "--dry-run"],
            ),
            (
                self.mod.build_memory_sync_plan,
                {"memory_sync_script": Path("/tmp/roby-memory-sync.py"), "route": "memory_sync", "dry_run": True},
                "memory_sync",
                ["--json", "--dry-run"],
            ),
            (
                self.mod.build_eval_harness_plan,
                {"eval_harness_script": Path("/tmp/roby-eval-harness.py"), "route": "evaluation_harness", "verbose": True},
                "evaluation_harness",
                ["--json", "--verbose"],
            ),
            (
                self.mod.build_runbook_drill_plan,
                {"drill_script": Path("/tmp/roby-drill.py"), "route": "runbook_drill"},
                "runbook_drill",
                ["--json"],
            ),
            (
                self.mod.build_weekly_report_plan,
                {"weekly_report_script": Path("/tmp/roby-weekly-report.py"), "route": "weekly_report"},
                "weekly_report",
                ["--json"],
            ),
        ]

        for builder, kwargs, route, expected_flags in cases:
            with self.subTest(route=route):
                plan = builder({}, **kwargs)
                self.assertEqual(plan["result"]["route"], route)
                self.assertFalse(plan["result"]["executed"])
                for flag in expected_flags:
                    self.assertIn(flag, plan["cmd"])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main


def _load_module(module_name: str, filename: str):
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / filename
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRobyWeeklyReport(TestCase):
    def setUp(self):
        self.mod = _load_module("roby_weekly_report_module", "roby-weekly-report.py")
        self.notify_mod = _load_module("roby_ops_notifications_module", "roby_ops_notifications.py")

    def test_summarize_self_growth_computes_feedback_delta(self):
        items = [
            {
                "ts": "2026-03-12T10:00:00+09:00",
                "timestamp": "2026-03-12 10:00:00",
                "patch_status": "applied",
                "patch_scope_status": "ok",
                "test_status": "passed",
                "restart_status": "ok",
                "growth_focus": {"target_labels": ["gmail_promo_filtering"]},
                "quality_delta": {"evaluation_failed_before": 1, "evaluation_failed_after": 0},
            }
        ]
        feedback_items = [
            {
                "ts": "2026-03-12T09:50:00+09:00",
                "summary": {
                    "reviewed_count": 10,
                    "actionable_count": 4,
                    "counts": {"good": 2, "bad": 3, "missed": 1, "pending": 4},
                },
            },
            {
                "ts": "2026-03-12T10:20:00+09:00",
                "summary": {
                    "reviewed_count": 12,
                    "actionable_count": 5,
                    "counts": {"good": 4, "bad": 2, "missed": 0, "pending": 5},
                },
            },
        ]

        summary = self.mod.summarize_self_growth(items, feedback_items)

        self.assertEqual(summary["runs"], 1)
        self.assertEqual(summary["measured_runs"], 1)
        self.assertEqual(summary["improved_runs"], 1)
        self.assertEqual(summary["worsened_runs"], 0)
        delta = summary["latest"]["feedback_delta"]
        self.assertEqual(delta["good_before"], 2)
        self.assertEqual(delta["good_after"], 4)
        self.assertEqual(delta["bad_before"], 3)
        self.assertEqual(delta["bad_after"], 2)
        self.assertEqual(delta["missed_before"], 1)
        self.assertEqual(delta["missed_after"], 0)
        self.assertTrue(delta["improved"])

    def test_build_markdown_includes_feedback_effect(self):
        report = {
            "generated_at": "2026-03-12T10:30:00+09:00",
            "window_days": 7,
            "eval": {"runs": 7, "failed_runs": 0, "pass_rate": 1.0, "avg_failure_rate": 0.0, "avg_p95_ms": 900},
            "drill": {"runs": 7, "failed_runs": 0, "pass_rate": 1.0},
            "ab": {"runs": 0, "guard_applied_runs": 0, "arms": {}},
            "feedback": {"runs": 2, "reviewed_count": 12, "actionable_count": 5, "good": 4, "bad": 2, "missed": 0, "pending": 6},
            "self_growth": {
                "runs": 1,
                "success_runs": 1,
                "scope_blocked_runs": 0,
                "patch_status_counts": {"applied": 1},
                "latest": {
                    "patch_status": "applied",
                    "test_status": "passed",
                    "restart_status": "ok",
                    "feedback_delta": {
                        "good_before": 2,
                        "good_after": 4,
                        "bad_before": 3,
                        "bad_after": 2,
                        "missed_before": 1,
                        "missed_after": 0,
                        "improved": True,
                        "worsened": False,
                    },
                },
            },
            "audit": {"ok": True, "files": 1, "errors": 0},
            "freshness": {"present": True, "ok": True, "stale_count": 0, "stale_components": []},
            "ops": {},
        }

        markdown = self.mod.build_markdown(report)
        slack = self.notify_mod.format_weekly_slack(report)

        self.assertIn("feedback_delta: good 2→4, bad 3→2, missed 1→0", markdown)
        self.assertIn("feedback_effect: improved=True worsened=False", markdown)
        self.assertIn("Self Growth 効果", slack)
        self.assertIn("latest feedback delta: good 2→4 / bad 3→2 / missed 1→0", slack)


if __name__ == "__main__":
    main()

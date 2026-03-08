#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main


def _load_feedback_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby-feedback-sync.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_feedback_sync_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRobyFeedbackSync(TestCase):
    def setUp(self):
        self.mod = _load_feedback_module()

    def test_build_neuronic_base_url_derives_from_import_endpoint(self):
        base = self.mod.build_neuronic_base_url(
            {"NEURONIC_URL": "http://127.0.0.1:5174/api/v1/tasks/import"}
        )
        self.assertEqual(base, "http://127.0.0.1:5174/api/v1")

    def test_summarize_feedback_counts_and_recent_actionable(self):
        tasks = [
            {
                "id": "1",
                "title": "A",
                "source": "roby",
                "feedback_state": "pending",
                "updated_at": "2026-03-08T10:00:00Z",
            },
            {
                "id": "2",
                "title": "B",
                "source": "roby",
                "feedback_state": "good",
                "updated_at": "2026-03-08T11:00:00Z",
            },
            {
                "id": "3",
                "title": "C",
                "source": "roby",
                "feedback_state": "bad",
                "feedback_reason_code": "too_broad",
                "updated_at": "2026-03-08T12:00:00Z",
            },
            {
                "id": "4",
                "title": "D",
                "source": "roby",
                "feedback_state": "missed",
                "feedback_reason_code": "not_actionable",
                "updated_at": "2026-03-08T13:00:00Z",
            },
        ]
        summary = self.mod.summarize_feedback(tasks, recent_limit=3)
        self.assertEqual(summary["total_tasks"], 4)
        self.assertEqual(summary["reviewed_count"], 3)
        self.assertEqual(summary["actionable_count"], 2)
        self.assertEqual(summary["counts"]["good"], 1)
        self.assertEqual(summary["counts"]["bad"], 1)
        self.assertEqual(summary["counts"]["missed"], 1)
        self.assertEqual(summary["counts"]["pending"], 1)
        self.assertEqual(summary["actionable_reason_counts"]["too_broad"], 1)
        self.assertEqual(summary["actionable_reason_counts"]["not_actionable"], 1)
        self.assertEqual([x["title"] for x in summary["recent_actionable"]], ["D", "C"])
        self.assertEqual(summary["improvement_targets"][0]["target"], "task_filtering")
        self.assertEqual(summary["improvement_targets"][0]["count"], 1)
        self.assertEqual(summary["improvement_targets"][1]["target"], "task_granularity_split")

    def test_email_specific_reason_maps_to_gmail_target(self):
        tasks = [
            {
                "id": "mail-1",
                "title": "メール確認: お役立ち資料",
                "source": "roby",
                "feedback_state": "bad",
                "feedback_reason_code": "newsletter_false_positive",
                "updated_at": "2026-03-08T14:00:00Z",
            }
        ]
        summary = self.mod.summarize_feedback(tasks, recent_limit=3)
        self.assertEqual(summary["actionable_reason_counts"]["newsletter_false_positive"], 1)
        self.assertEqual(summary["improvement_targets"][0]["target"], "gmail_promo_filtering")
        self.assertEqual(summary["improvement_targets"][0]["label"], "メルマガ判定")


if __name__ == "__main__":
    main()

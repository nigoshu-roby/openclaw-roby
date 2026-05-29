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
    script_path = scripts_dir / "roby_gmail_tasks.py"
    spec = importlib.util.spec_from_file_location("roby_gmail_tasks_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRobyGmailTasks(TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_extract_explicit_email_actions_detects_contract_prep(self):
        actions = self.mod.extract_explicit_email_actions(
            "Re: R8年度契約について",
            "契約更新が決定しました。契約書のご準備をお願い致します。",
            raw_category="needs_review",
            meta={"signals": {"contract_followup_subject": True}},
            tags=[],
        )

        self.assertIn("契約書を準備する", [row["title"] for row in actions])

    def test_normalize_extracted_actions_adds_reply_for_needs_reply(self):
        rows = self.mod.normalize_extracted_actions(
            [{"title": "確認", "task_kind": "action"}],
            raw_category="needs_reply",
            subject="ご確認のお願い",
        )

        self.assertEqual(rows[0]["task_kind"], "reply")
        self.assertEqual(rows[0]["title"], "【返信】ご確認のお願い")
        self.assertEqual(rows[1]["title"], "返信内容を確認して返信する")

    def test_task_gate_downgrades_generic_newsletter_risk(self):
        meta = {
            "signals": {
                "promo_sender_domain": True,
                "is_noreply": True,
                "business_review": False,
                "actionable_notice": False,
                "alert": False,
            },
            "bucket_scores": {"newsletter": 5},
        }
        bucket, reason, gated_meta = self.mod.decide_task_gate(
            "needs_review",
            "task",
            [{"title": "メール内容を確認して対応する", "task_kind": "action"}],
            meta,
            [],
        )

        self.assertEqual(bucket, "review")
        self.assertEqual(reason, "low_confidence_downgraded_to_review")
        self.assertFalse(gated_meta["task_gate"]["applied"])

    def test_task_gate_accepts_reply_with_specific_task(self):
        meta = {"signals": {}, "bucket_scores": {}}
        bucket, reason, gated_meta = self.mod.decide_task_gate(
            "needs_reply",
            "task",
            [{"title": "契約書を準備する", "task_kind": "reply"}],
            meta,
            ["contact:known"],
        )

        self.assertEqual(bucket, "task")
        self.assertEqual(reason, "high_confidence_task")
        self.assertTrue(gated_meta["task_gate"]["applied"])

    def test_build_tasks_creates_parent_and_child_hierarchy(self):
        tasks = self.mod.build_tasks(
            [{"title": "契約書を準備する", "project": "契約", "task_kind": "action"}],
            {
                "id": "msg-1",
                "threadId": "thread-1",
                "subject": "契約更新について",
                "from": "田中さん <tanaka@example.com>",
                "date": "2026-05-28",
            },
            "task",
            ["context:project"],
            run_id="roby:gmail:test",
            raw_category="needs_review",
        )

        self.assertEqual(len(tasks), 2)
        self.assertIsNone(tasks[0]["parent_origin_id"])
        self.assertEqual(tasks[1]["parent_origin_id"], tasks[0]["origin_id"])
        self.assertEqual(tasks[1]["sibling_order"], 0)
        self.assertIn("task_type:action", tasks[1]["tags"])
        self.assertIn("Link: https://mail.google.com/mail/u/0/#inbox/thread-1", tasks[1]["note"])

    def test_cap_extracted_actions_can_be_disabled(self):
        rows = [{"title": "a"}, {"title": "b"}]
        self.assertEqual(self.mod.cap_extracted_actions(rows, 0), rows)
        self.assertEqual(self.mod.cap_extracted_actions(rows, 1), [{"title": "a"}])


if __name__ == "__main__":
    main()

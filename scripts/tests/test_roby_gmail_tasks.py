#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


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

    def test_extract_explicit_email_actions_splits_schedule_url_reply(self):
        actions = self.mod.extract_explicit_email_actions(
            "Re: 兼清杯開催日程のご相談",
            (
                "兼清杯の開催日程について、下記URLから候補日程の◯✕をご回答ください。\n"
                "https://example.com/schedule\n"
                "7月10日までにお願いします。回答後、高田まで返信ください。"
            ),
            raw_category="needs_reply",
            meta={"signals": {"meeting_coordination": True}},
            tags=[],
            sender="高田彰 <takata@example.com>",
        )

        self.assertEqual([row["title"] for row in actions], [
            "指定のURLから候補日程の◯✕を回答する",
            "回答したら高田氏に返信する",
        ])
        self.assertEqual([row["due_date"] for row in actions], ["2026-07-10", "2026-07-10"])
        self.assertEqual(actions[0]["task_kind"], "action")
        self.assertEqual(actions[1]["task_kind"], "reply")

    def test_summarize_tasks_accepts_semantic_llm_action_plan(self):
        payload = {
            "summary": json.dumps(
                {
                    "tasks": [
                        {
                            "title": "指定のURLから候補日程の◯✕を回答する",
                            "due_date": "2026-07-10",
                            "project": "email",
                            "note": "本文でURLから候補日程の可否回答を依頼している。",
                            "task_kind": "action",
                        },
                        {
                            "title": "回答したら高田氏に返信する",
                            "due_date": "7月10日",
                            "project": "email",
                            "note": "回答後に返信する必要がある。",
                            "task_kind": "reply",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        }

        with patch.object(self.mod.subprocess, "check_output", return_value=json.dumps(payload).encode("utf-8")) as mock_check:
            actions = self.mod.summarize_tasks(
                "Subject: Re: 兼清杯開催日程のご相談\n\nURLから候補日程の◯✕を7月10日までに回答し、回答後に返信ください。",
                {"GMAIL_TRIAGE_TASK_LLM_MODEL": "ollama/qwen2.5:7b"},
            )

        self.assertEqual([row["title"] for row in actions], [
            "指定のURLから候補日程の◯✕を回答する",
            "回答したら高田氏に返信する",
        ])
        self.assertEqual([row["due_date"] for row in actions], ["2026-07-10", "2026-07-10"])
        self.assertEqual(actions[0]["task_kind"], "action")
        self.assertIn("--model", mock_check.call_args.args[0])

    def test_summarize_tasks_accepts_waiting_followup_action_plan(self):
        payload = {
            "tasks": [
                {
                    "title": "再依頼が来たらベンダーに依頼内容を共有する",
                    "due_date": "",
                    "project": "email",
                    "task_kind": "action",
                },
                {
                    "title": "本日中に再依頼がなければクライアントに確認する",
                    "due_date": "2026-06-22",
                    "project": "email",
                    "task_kind": "action",
                },
            ]
        }

        with patch.object(self.mod, "run_gemini_json_prompt", return_value=(payload, json.dumps(payload, ensure_ascii=False))):
            actions = self.mod.summarize_tasks(
                "Subject: 夏のあそび場販促準備に伴う確認のご依頼\n\n本日中に改めてタグの埋め込みに関するご相談をお送りします。再度ご依頼をお待ちしております。",
                {"GMAIL_TRIAGE_TASK_LLM_MODEL": "google/gemini-3-flash-preview"},
            )

        self.assertEqual([row["title"] for row in actions], [
            "再依頼が来たらベンダーに依頼内容を共有する",
            "本日中に再依頼がなければクライアントに確認する",
        ])
        self.assertEqual(actions[1]["due_date"], "2026-06-22")

    def test_summarize_tasks_accepts_vendor_status_followup_action_plan(self):
        payload = {
            "tasks": [
                {
                    "title": "ダブルスタンダード社のプロバイダID確認状況を確認する",
                    "due_date": "",
                    "project": "email",
                    "task_kind": "action",
                },
                {
                    "title": "確認結果に応じてベンダーへ相談する",
                    "due_date": "",
                    "project": "email",
                    "task_kind": "action",
                },
            ]
        }

        with patch.object(self.mod, "run_gemini_json_prompt", return_value=(payload, json.dumps(payload, ensure_ascii=False))):
            actions = self.mod.summarize_tasks(
                "Subject: 予約システムのヒアリングについて\n\nまずは同一プロバイダかどうかの確認を進めてまいります。プロバイダIDのご確認につきましてお願いいたします。",
                {"GMAIL_TRIAGE_TASK_LLM_MODEL": "google/gemini-3-flash-preview"},
            )

        self.assertEqual([row["title"] for row in actions], [
            "ダブルスタンダード社のプロバイダID確認状況を確認する",
            "確認結果に応じてベンダーへ相談する",
        ])

    def test_normalize_extracted_actions_removes_generic_reply_when_specific_reply_exists(self):
        rows = self.mod.normalize_extracted_actions(
            [
                {"title": "【返信】Re: 兼清杯開催日程のご相談", "task_kind": "reply"},
                {"title": "回答したら高田氏に返信する", "task_kind": "reply", "due_date": "2026-07-10"},
            ],
            raw_category="needs_reply",
            subject="Re: 兼清杯開催日程のご相談",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "回答したら高田氏に返信する")
        self.assertEqual(rows[0]["due_date"], "2026-07-10")

    def test_normalize_extracted_actions_removes_subject_copy_reply_when_specific_reply_exists(self):
        rows = self.mod.normalize_extracted_actions(
            [
                {"title": "Re: 兼清杯開催日程のご相談", "task_kind": "reply"},
                {"title": "回答したら高田氏に返信する", "task_kind": "reply", "due_date": "2026-07-10"},
            ],
            raw_category="needs_reply",
            subject="Re: 兼清杯開催日程のご相談",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "回答したら高田氏に返信する")

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

    def test_build_tasks_keeps_single_email_action_flat(self):
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

        self.assertEqual(len(tasks), 1)
        self.assertIsNone(tasks[0]["parent_origin_id"])
        self.assertEqual(tasks[0]["title"], "【田中さん】契約書を準備する")
        self.assertEqual(tasks[0]["sibling_order"], 0)
        self.assertIn("task_type:action", tasks[0]["tags"])
        self.assertIn("Link: https://mail.google.com/mail/u/0/#inbox/thread-1", tasks[0]["note"])
        self.assertNotIn("Parent:", tasks[0]["note"])

    def test_build_tasks_uses_semantic_identity_for_duplicate_invoice_mail(self):
        first = self.mod.build_tasks(
            [
                {
                    "title": "株式会社DIPROの2026年6月分請求書の内容確認と支払い手続き",
                    "project": "email",
                    "task_kind": "action",
                }
            ],
            {
                "id": "19f1bb980117b992",
                "threadId": "thread-a",
                "subject": "【株式会社DIPRO】 請求書送付のご案内（2026年6月分）",
                "from": "株式会社DIPRO <billing@dipro.example>",
                "date": "2026-07-01",
            },
            "task",
            [],
            run_id="roby:gmail:test-a",
            raw_category="needs_review",
        )
        second = self.mod.build_tasks(
            [
                {
                    "title": "株式会社DIPROの2026年6月分請求書を確認し、支払処理を行う",
                    "project": "email",
                    "task_kind": "action",
                }
            ],
            {
                "id": "19f1bc57c699e499",
                "threadId": "thread-b",
                "subject": "【株式会社DIPRO】 請求書送付のご案内（2026年6月分）",
                "from": "株式会社DIPRO <billing@dipro.example>",
                "date": "2026-07-01",
            },
            "task",
            [],
            run_id="roby:gmail:test-b",
            raw_category="needs_review",
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(first[0]["origin_id"], second[0]["origin_id"])

    def test_build_tasks_creates_parent_only_for_multiple_email_actions(self):
        tasks = self.mod.build_tasks(
            [
                {
                    "title": "指定のURLから候補日程の◯✕を回答する",
                    "project": "email",
                    "task_kind": "action",
                    "due_date": "2026-07-10",
                },
                {
                    "title": "回答したら高田氏に返信する",
                    "project": "email",
                    "task_kind": "reply",
                    "due_date": "2026-07-10",
                },
            ],
            {
                "id": "msg-2",
                "threadId": "thread-2",
                "subject": "Re: 兼清杯開催日程のご相談",
                "from": "高田彰 <takata@example.com>",
                "date": "2026-05-28",
            },
            "task",
            [],
            run_id="roby:gmail:test",
            raw_category="needs_reply",
        )

        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0]["title"], "【高田彰】メール対応: Re: 兼清杯開催日程のご相談")
        self.assertIsNone(tasks[0]["parent_origin_id"])
        self.assertEqual(tasks[1]["parent_origin_id"], tasks[0]["origin_id"])
        self.assertEqual(tasks[1]["title"], "【高田彰】指定のURLから候補日程の◯✕を回答する")
        self.assertEqual(tasks[1]["due_date"], "2026-07-10")
        self.assertEqual(tasks[2]["title"], "【高田彰】回答したら高田氏に返信する")
        self.assertEqual(tasks[2]["due_date"], "2026-07-10")

    def test_build_tasks_cleans_single_reply_subject_without_parent(self):
        tasks = self.mod.build_tasks(
            [{"title": "【返信】Re: 兼清杯開催日程のご相談", "project": "email", "task_kind": "reply"}],
            {
                "id": "msg-3",
                "threadId": "thread-3",
                "subject": "Re: 兼清杯開催日程のご相談",
                "from": "高田彰 <takata@example.com>",
                "date": "2026-05-28",
            },
            "task",
            [],
            run_id="roby:gmail:test",
            raw_category="needs_reply",
        )

        self.assertEqual(len(tasks), 1)
        self.assertIsNone(tasks[0]["parent_origin_id"])
        self.assertEqual(tasks[0]["title"], "【高田彰】【返信】兼清杯開催日程のご相談")

    def test_latest_message_body_removes_quoted_thread_history(self):
        latest, trimmed = self.mod.latest_message_body(
            "承知しました。こちらで確認します。 "
            "2026年7月2日(木) 16:43 安本愛理 <airi@example.com>: "
            "契約書を準備し、見積書を送付してください。"
        )

        self.assertTrue(trimmed)
        self.assertEqual(latest, "承知しました。こちらで確認します。")
        self.assertNotIn("契約書", latest)

    def test_filter_existing_thread_actions_suppresses_prior_thread_tasks(self):
        rows = [
            {"title": "契約書を準備する", "task_kind": "action"},
            {"title": "指定のURLから候補日程の◯✕を回答する", "task_kind": "action"},
        ]

        kept, suppressed = self.mod.filter_existing_thread_actions(
            rows,
            existing_titles=["【安本愛理】契約書を準備する"],
        )

        self.assertEqual([row["title"] for row in kept], ["指定のURLから候補日程の◯✕を回答する"])
        self.assertEqual([row["title"] for row in suppressed], ["契約書を準備する"])

    def test_cap_extracted_actions_can_be_disabled(self):
        rows = [{"title": "a"}, {"title": "b"}]
        self.assertEqual(self.mod.cap_extracted_actions(rows, 0), rows)
        self.assertEqual(self.mod.cap_extracted_actions(rows, 1), [{"title": "a"}])


if __name__ == "__main__":
    main()

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_gmail_eval_corpus", "/Users/shu/OpenClaw/scripts/roby-gmail-eval-corpus.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GmailEvalCorpusTests(unittest.TestCase):
    def test_is_gmail_candidate_accepts_gmail_run(self):
        self.assertTrue(module.is_gmail_candidate({"run_id": "roby:gmail:abc"}))
        self.assertFalse(module.is_gmail_candidate({"run_id": "roby:minutes:abc", "project": "ボーネルンド"}))

    def test_build_gmail_review_entries_filters_and_joins(self):
        tasks = [
            {
                "id": "task-1",
                "origin_id": "o1",
                "title": "【高田彰】返信内容を確認して返信する",
                "feedback_state": "good",
                "feedback_reason_code": None,
                "status": "inbox",
                "tags": ["task_type:reply", "category:task"],
            },
            {
                "id": "task-2",
                "origin_id": "o2",
                "title": "議事録タスクA",
                "feedback_state": "bad",
                "feedback_reason_code": "too_broad",
                "tags": ["category:task"],
            },
        ]
        candidates = {
            "o1": {
                "origin_id": "o1",
                "run_id": "roby:gmail:run1",
                "project": "email",
                "source_doc_id": "mail-1",
                "source_doc_title": "見積書の件",
                "parent_origin_id": "parent-1",
            },
            "o2": {
                "origin_id": "o2",
                "run_id": "roby:minutes:run2",
                "project": "ボーネルンド",
                "source_doc_id": "doc-1",
                "source_doc_title": "2026/03/10 社内定例MTG",
                "parent_origin_id": None,
            },
        }
        entries = module.build_gmail_review_entries(tasks, candidates)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["origin_id"], "o1")
        self.assertEqual(entries[0]["sender_label"], "高田彰")
        self.assertEqual(entries[0]["task_type"], "reply")
        self.assertEqual(entries[0]["work_bucket"], "task")

    def test_build_golden_and_missed_payloads(self):
        entries = [
            {
                "origin_id": "good-1",
                "task_id": "task-1",
                "title": "【高田彰】返信内容を確認して返信する",
                "sender_label": "高田彰",
                "project": "email",
                "parent_origin_id": "parent-1",
                "source_doc_id": "mail-1",
                "source_doc_title": "見積書の件",
                "source_run_id": "roby:gmail:1",
                "feedback_state": "good",
                "feedback_reason_code": None,
                "updated_at": None,
                "created_at": None,
                "status": "inbox",
                "task_type": "reply",
                "work_bucket": "task",
            },
            {
                "origin_id": "missed-1",
                "task_id": "task-2",
                "title": "【高田彰】見積書を送付する",
                "sender_label": "高田彰",
                "project": "email",
                "parent_origin_id": "parent-1",
                "source_doc_id": "mail-2",
                "source_doc_title": "見積書の件",
                "source_run_id": "roby:gmail:2",
                "feedback_state": "missed",
                "feedback_reason_code": "important_notification_missed",
                "updated_at": None,
                "created_at": None,
                "status": "inbox",
                "task_type": "action",
                "work_bucket": "task",
            },
        ]
        golden = module.build_golden_payload(entries, base_url="http://127.0.0.1:5174/api/v1")
        missed = module.build_missed_payload(entries, base_url="http://127.0.0.1:5174/api/v1")
        self.assertEqual(len(golden["items"]), 1)
        self.assertEqual(golden["items"][0]["origin_id"], "good-1")
        self.assertEqual(len(missed["items"]), 1)
        self.assertEqual(missed["items"][0]["origin_id"], "missed-1")
        self.assertIn("manual_entry_template", missed)

    def test_read_feedback_candidate_index_keeps_latest(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "candidates.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"event":"feedback_candidates","run_id":"roby:gmail:1","items":[{"origin_id":"o1","title":"old"}]}',
                        '{"event":"feedback_candidates","run_id":"roby:gmail:2","items":[{"origin_id":"o1","title":"new"}]}',
                    ]
                ),
                encoding="utf-8",
            )
            idx = module.read_feedback_candidate_index(path)
            self.assertEqual(idx["o1"]["title"], "new")
            self.assertEqual(idx["o1"]["run_id"], "roby:gmail:2")

    def test_manual_missed_entries_are_merged_without_duplicates(self):
        entries = [
            {
                "origin_id": "missed-1",
                "title": "【高田彰】見積書を送付する",
                "feedback_state": "missed",
            }
        ]
        manual_entries = [
            {
                "origin_id": "missed-1",
                "title": "【高田彰】見積書を送付する",
                "feedback_state": "missed",
            },
            {
                "origin_id": "manual-2",
                "title": "【飯野友明】契約更新を確認する",
                "feedback_state": "missed",
            },
        ]
        merged = module.merge_missed_entries(entries, manual_entries)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[1]["origin_id"], "manual-2")


if __name__ == "__main__":
    unittest.main()

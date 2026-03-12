import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_minutes_eval_corpus", "/Users/shu/OpenClaw/scripts/roby-minutes-eval-corpus.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MinutesEvalCorpusTests(unittest.TestCase):
    def test_is_minutes_candidate_accepts_minutes_run(self):
        self.assertTrue(module.is_minutes_candidate({"run_id": "roby:minutes:abc"}))
        self.assertFalse(module.is_minutes_candidate({"run_id": "roby:gmail:abc", "source_doc_title": "請求書"}))

    def test_build_minutes_review_entries_filters_and_joins(self):
        tasks = [
            {
                "id": "task-1",
                "origin_id": "o1",
                "title": "議事録タスクA",
                "feedback_state": "good",
                "feedback_reason_code": None,
                "status": "inbox",
            },
            {
                "id": "task-2",
                "origin_id": "o2",
                "title": "メール確認",
                "feedback_state": "bad",
                "feedback_reason_code": "not_actionable",
            },
        ]
        candidates = {
            "o1": {
                "origin_id": "o1",
                "run_id": "roby:minutes:run1",
                "project": "ボーネルンド",
                "source_doc_id": "doc-1",
                "source_doc_title": "2026/03/10 社内定例MTG",
                "parent_origin_id": None,
            },
            "o2": {
                "origin_id": "o2",
                "run_id": "roby:gmail:run2",
                "project": "email",
                "source_doc_id": "mail-1",
                "source_doc_title": "請求書",
                "parent_origin_id": None,
            },
        }
        entries = module.build_minutes_review_entries(tasks, candidates)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["origin_id"], "o1")
        self.assertEqual(entries[0]["project"], "ボーネルンド")
        self.assertEqual(entries[0]["feedback_state"], "good")

    def test_build_golden_and_missed_payloads(self):
        entries = [
            {
                "origin_id": "good-1",
                "task_id": "task-1",
                "title": "議事録タスクA",
                "project": "ボーネルンド",
                "parent_origin_id": None,
                "source_doc_id": "doc-1",
                "source_doc_title": "2026/03/10 社内定例MTG",
                "source_run_id": "roby:minutes:1",
                "feedback_state": "good",
                "feedback_reason_code": None,
                "updated_at": None,
                "created_at": None,
                "status": "inbox",
            },
            {
                "origin_id": "missed-1",
                "task_id": "task-2",
                "title": "抽出漏れ例",
                "project": "瑞鳳社ーデータ分析",
                "parent_origin_id": None,
                "source_doc_id": "doc-2",
                "source_doc_title": "2026/03/11 社内定例MTG",
                "source_run_id": "roby:minutes:2",
                "feedback_state": "missed",
                "feedback_reason_code": "unclear",
                "updated_at": None,
                "created_at": None,
                "status": "inbox",
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
                '\n'.join(
                    [
                        '{"event":"feedback_candidates","run_id":"roby:minutes:1","items":[{"origin_id":"o1","title":"old"}]}',
                        '{"event":"feedback_candidates","run_id":"roby:minutes:2","items":[{"origin_id":"o1","title":"new"}]}'
                    ]
                ),
                encoding="utf-8",
            )
            idx = module.read_feedback_candidate_index(path)
            self.assertEqual(idx["o1"]["title"], "new")
            self.assertEqual(idx["o1"]["run_id"], "roby:minutes:2")


if __name__ == "__main__":
    unittest.main()

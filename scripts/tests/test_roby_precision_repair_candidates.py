import importlib.util
import sys
import unittest

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_precision_repair_candidates", "/Users/shu/OpenClaw/scripts/roby-precision-repair-candidates.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PrecisionRepairCandidateTests(unittest.TestCase):
    def test_semantic_parent_repair_candidate_includes_suggested_project(self):
        payload = module.build_payload(
            [
                {
                    "source_run_id": "roby:minutes:1",
                    "origin_id": "child-1",
                    "task_id": "task-1",
                    "title": "運営会社一覧の情報提供依頼",
                    "project": "ボーネルンド",
                    "parent_origin_id": "parent-1",
                    "feedback_state": "bad",
                    "feedback_reason_code": "wrong_project",
                    "source_doc_title": "2026/06/02社内定例MTG",
                }
            ],
            base_url="http://127.0.0.1:5174/api/v1",
        )
        self.assertEqual(payload["summary"]["semantic_parent_misnested"], 1)
        repair = payload["semantic_parent_misnested"][0]
        self.assertEqual(repair["current"]["origin_id"], "child-1")
        self.assertEqual(repair["suggested_project"], "LINE広告配信")
        self.assertEqual(repair["recommended_action"], "move_to_project_parent_or_recreate_under_suggested_project")

    def test_duplicate_repair_candidates_keep_oldest(self):
        payload = module.build_payload(
            [
                {
                    "source_run_id": "roby:minutes:1",
                    "origin_id": "newer",
                    "task_id": "task-newer",
                    "source_doc_id": "doc1",
                    "source_doc_title": "2026/03/10 社内定例MTG",
                    "project": "ボーネルンド",
                    "title": "ボーネルンド / 2026/03/10 社内定例MTG",
                    "created_at": "2026-03-11T00:00:00Z",
                },
                {
                    "source_run_id": "roby:minutes:2",
                    "origin_id": "older",
                    "task_id": "task-older",
                    "source_doc_id": "doc1",
                    "source_doc_title": "2026/03/10 社内定例MTG",
                    "project": "ボーネルンド",
                    "title": "ボーネルンド / 2026/03/10 社内定例MTG",
                    "created_at": "2026-03-10T00:00:00Z",
                },
            ],
            base_url="http://127.0.0.1:5174/api/v1",
        )
        self.assertEqual(payload["summary"]["duplicate_groups"], 1)
        repair = payload["duplicates"][0]
        self.assertEqual(repair["type"], "parent_group_duplicate")
        self.assertEqual(repair["keep"]["origin_id"], "older")
        self.assertEqual(repair["duplicates"][0]["origin_id"], "newer")

    def test_gmail_duplicate_repair_candidates_include_invoice_semantic_duplicates(self):
        payload = module.build_payload(
            [],
            base_url="http://127.0.0.1:5174/api/v1",
            duplicate_entries=[
                {
                    "source_run_id": "roby:gmail:1",
                    "origin_id": "roby:auto:c893d39d1ec4",
                    "task_id": "task-a",
                    "source_doc_id": "19f1bb980117b992",
                    "source_doc_title": "【株式会社DIPRO】 請求書送付のご案内（2026年6月分）",
                    "sender_label": "株式会社DIPRO",
                    "project": "email",
                    "title": "【株式会社DIPRO】株式会社DIPROの2026年6月分請求書の内容確認と支払い手続き",
                    "created_at": "2026-07-01T00:00:00Z",
                },
                {
                    "source_run_id": "roby:gmail:2",
                    "origin_id": "roby:auto:057a3c106a43",
                    "task_id": "task-b",
                    "source_doc_id": "19f1bc57c699e499",
                    "source_doc_title": "【株式会社DIPRO】 請求書送付のご案内（2026年6月分）",
                    "sender_label": "株式会社DIPRO",
                    "project": "email",
                    "title": "【株式会社DIPRO】株式会社DIPROの2026年6月分請求書を確認し、支払処理を行う",
                    "created_at": "2026-07-01T00:10:00Z",
                },
            ],
        )

        self.assertEqual(payload["summary"]["duplicate_groups"], 1)
        repair = payload["duplicates"][0]
        self.assertEqual(repair["type"], "gmail_semantic_duplicate")
        self.assertEqual(repair["domain"], "gmail")
        self.assertEqual(repair["keep"]["origin_id"], "roby:auto:c893d39d1ec4")
        self.assertEqual(repair["duplicates"][0]["origin_id"], "roby:auto:057a3c106a43")


if __name__ == "__main__":
    unittest.main()

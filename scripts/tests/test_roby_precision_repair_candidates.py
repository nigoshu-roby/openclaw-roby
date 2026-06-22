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


if __name__ == "__main__":
    unittest.main()

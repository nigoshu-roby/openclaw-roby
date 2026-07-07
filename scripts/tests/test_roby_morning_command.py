import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_morning_command", "/Users/shu/OpenClaw/scripts/roby-morning-command.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MorningCommandTests(unittest.TestCase):
    def test_build_payload_prioritizes_focus_decisions_and_waiting(self):
        tasks = [
            {
                "id": "task-1",
                "origin_id": "o1",
                "title": "ボーネルンドの通信環境判断をクライアントに確認する",
                "status": "inbox",
                "source": "roby",
                "project": "ボーネルンド",
                "due_date": "2026-07-07",
                "updated_at": "2026-07-07T00:00:00+09:00",
            },
            {
                "id": "task-2",
                "origin_id": "o2",
                "title": "BW本番環境移行のジャッジ待ちを確認する",
                "status": "inbox",
                "source": "roby",
                "project": "LINE広告配信",
                "updated_at": "2026-07-06T10:00:00+09:00",
            },
            {
                "id": "task-3",
                "origin_id": "o3",
                "title": "完了済みタスク",
                "status": "done",
                "source": "roby",
                "project": "瑞鳳",
            },
        ]

        with patch.object(module, "read_feedback_candidate_index", return_value={}):
            with patch.object(module, "latest_run_summaries", return_value=[]):
                with patch.object(module, "duplicate_origin_ids_from_repair_candidates", return_value=set()):
                    payload = module.build_payload(
                        tasks,
                        base_url="http://127.0.0.1:5174/api/v1",
                        generated_at=datetime(2026, 7, 7, 9, 0, tzinfo=module.JST),
                    )

        self.assertEqual(payload["summary"]["open_tasks"], 2)
        self.assertEqual(payload["focus"][0]["origin_id"], "o1")
        self.assertTrue(any(row["origin_id"] == "o1" for row in payload["decisions"]))
        self.assertTrue(any(row["origin_id"] == "o2" for row in payload["waiting"]))
        health_projects = [row["project"] for row in payload["project_health"]]
        self.assertIn("ボーネルンド", health_projects)
        self.assertIn("LINE広告配信", health_projects)

    def test_build_payload_suppresses_known_duplicate_origins(self):
        tasks = [
            {"origin_id": "keep", "title": "残すタスク", "status": "inbox", "source": "roby", "project": "LINE広告配信"},
            {"origin_id": "dup", "title": "重複タスク", "status": "inbox", "source": "roby", "project": "LINE広告配信"},
        ]

        with patch.object(module, "read_feedback_candidate_index", return_value={}):
            with patch.object(module, "latest_run_summaries", return_value=[]):
                with patch.object(module, "duplicate_origin_ids_from_repair_candidates", return_value={"dup"}):
                    payload = module.build_payload(
                        tasks,
                        generated_at=datetime(2026, 7, 7, 9, 0, tzinfo=module.JST),
                    )

        self.assertEqual(payload["summary"]["suppressed_duplicate_candidates"], 1)
        self.assertEqual(payload["summary"]["open_tasks"], 1)
        self.assertEqual(payload["focus"][0]["origin_id"], "keep")

    def test_infer_project_groups_gmail_invoice_as_finance(self):
        row = {
            "title": "【株式会社DIPRO】2026年6月分請求書を確認する",
            "run_id": "roby:gmail:abc",
            "project": "email",
        }

        self.assertEqual(module.infer_project(row), "請求・経理")

    def test_render_markdown_contains_core_sections(self):
        payload = {
            "generated_at": "2026-07-07T09:00:00+09:00",
            "mode": "read_only",
            "summary": {"open_tasks": 1, "tasks_total": 2},
            "today_goals": ["ボーネルンド: 通信環境判断を前に進める"],
            "focus": [
                {
                    "project": "ボーネルンド",
                    "title": "通信環境判断を確認する",
                    "due_date": "2026-07-07",
                    "reason": "期限が今日/超過",
                }
            ],
            "decisions": [],
            "waiting": [],
            "watch": [],
            "project_health": [{"state": "yellow", "project": "ボーネルンド", "reason": "未完了 1件"}],
            "next_prompt": "Focusを絞る",
        }

        md = module.render_markdown(payload)

        self.assertIn("# Morning Command 15", md)
        self.assertIn("## 今日のゴール", md)
        self.assertIn("## Focus", md)
        self.assertIn("## Project Health", md)
        self.assertIn("通信環境判断を確認する", md)


if __name__ == "__main__":
    unittest.main()

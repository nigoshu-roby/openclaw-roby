import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_gmail_golden_curate", "/Users/shu/OpenClaw/scripts/roby-gmail-golden-curate.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GmailGoldenCurateTests(unittest.TestCase):
    def test_curate_items_balances_task_types_and_senders(self):
        items = [
            {"origin_id": "1", "task_type": "reply", "sender_label": "A", "source_doc_title": "x", "title": "t1"},
            {"origin_id": "2", "task_type": "reply", "sender_label": "A", "source_doc_title": "x", "title": "t2"},
            {"origin_id": "3", "task_type": "action", "sender_label": "B", "source_doc_title": "x", "title": "t3"},
            {"origin_id": "4", "task_type": "action", "sender_label": "C", "source_doc_title": "x", "title": "t4"},
        ]
        curated = module.curate_items(items, max_items=3)
        self.assertEqual(len(curated), 3)
        self.assertIn("reply", {row.get("task_type") for row in curated})
        self.assertIn("action", {row.get("task_type") for row in curated})

    def test_load_golden_items_reads_items(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "golden.json"
            path.write_text('{"items":[{"origin_id":"1"}]}', encoding="utf-8")
            items = module.load_golden_items(path)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["origin_id"], "1")


if __name__ == "__main__":
    unittest.main()

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_minutes_golden_curate", "/Users/shu/OpenClaw/scripts/roby-minutes-golden-curate.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MinutesGoldenCurateTests(unittest.TestCase):
    def test_curate_items_balances_projects_and_source_docs(self):
        items = [
            {"origin_id": "1", "project": "A", "source_doc_title": "doc1", "title": "t1"},
            {"origin_id": "2", "project": "A", "source_doc_title": "doc1", "title": "t2"},
            {"origin_id": "3", "project": "B", "source_doc_title": "doc2", "title": "t3"},
            {"origin_id": "4", "project": "B", "source_doc_title": "doc3", "title": "t4"},
            {"origin_id": "5", "project": "C", "source_doc_title": "doc3", "title": "t5"},
        ]
        curated = module.curate_items(items, max_items=4)
        self.assertEqual(len(curated), 4)
        self.assertTrue({"A", "B", "C"}.issubset({row.get("project") for row in curated}))
        doc_counts = {}
        for row in curated:
            doc = row.get("source_doc_title")
            doc_counts[doc] = doc_counts.get(doc, 0) + 1
        self.assertLessEqual(doc_counts.get("doc1", 0), 2)

    def test_load_golden_items_reads_items(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "golden.json"
            path.write_text('{"items":[{"origin_id":"1"}]}', encoding="utf-8")
            items = module.load_golden_items(path)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["origin_id"], "1")


if __name__ == "__main__":
    unittest.main()

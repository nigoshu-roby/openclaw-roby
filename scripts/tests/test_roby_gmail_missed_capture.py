import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_gmail_missed_capture", "/Users/shu/OpenClaw/scripts/roby-gmail-missed-capture.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GmailMissedCaptureTests(unittest.TestCase):
    def test_append_and_read_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missed.jsonl"
            module.append_row(path, {"expected_title": "見積書を送付する"})
            rows = module.read_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["expected_title"], "見積書を送付する")

    def test_read_rows_skips_invalid_lines(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missed.jsonl"
            path.write_text("not-json\n" + json.dumps({"expected_title": "A"}, ensure_ascii=False), encoding="utf-8")
            rows = module.read_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["expected_title"], "A")


if __name__ == "__main__":
    unittest.main()

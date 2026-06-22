import importlib.util
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_audit", "/Users/shu/OpenClaw/scripts/roby_audit.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RobyAuditTests(unittest.TestCase):
    def test_append_preserves_chain_after_large_last_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            first = module.append_audit_event(
                "large.event",
                {"body": "x" * 10000},
                path=path,
            )
            second = module.append_audit_event(
                "next.event",
                {"ok": True},
                path=path,
            )

            self.assertEqual(second["seq"], 2)
            self.assertEqual(second["prev_hash"], first["hash"])
            self.assertTrue(module.verify_audit_file(path)["ok"])

    def test_append_rejects_invalid_existing_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text('{"seq": 1}\\nnot-json\\n', encoding="utf-8")

            with self.assertRaises(RuntimeError):
                module.append_audit_event("next.event", {}, path=path)

    def test_concurrent_append_keeps_single_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(
                    pool.map(
                        lambda index: module.append_audit_event(
                            "parallel.event",
                            {"index": index},
                            path=path,
                        ),
                        range(40),
                    )
                )

            report = module.verify_audit_file(path)
            self.assertTrue(report["ok"])
            self.assertEqual(report["count"], 40)


if __name__ == "__main__":
    unittest.main()

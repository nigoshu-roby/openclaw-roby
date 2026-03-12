import importlib.util
import sys
import unittest

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_precision_metrics", "/Users/shu/OpenClaw/scripts/roby-precision-metrics.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PrecisionMetricsTests(unittest.TestCase):
    def test_compute_domain_metrics(self):
        summary = {
            "reviewed_items": 20,
            "counts": {"good": 6, "bad": 2, "missed": 2, "pending": 10},
            "top_feedback_reasons": [{"reason_code": "x", "count": 1}],
        }
        curated = {"source_items": 10, "curated_items": 4}
        result = module.compute_domain_metrics(summary, curated, domain="gmail")
        self.assertEqual(result["domain"], "gmail")
        self.assertEqual(result["precision"], 0.75)
        self.assertEqual(result["recall"], 0.75)
        self.assertEqual(result["usefulness"], 0.6)
        self.assertEqual(result["review_coverage"], 0.5)
        self.assertEqual(result["curated_coverage"], 0.4)
        self.assertTrue(result["false_negative_observed"])
        self.assertFalse(result["recall_provisional"])

    def test_build_overall(self):
        gmail = {"reviewed_items": 10, "good": 4, "bad": 1, "missed": 1, "pending": 4}
        minutes = {"reviewed_items": 20, "good": 5, "bad": 5, "missed": 0, "pending": 10}
        result = module.build_overall(gmail, minutes)
        self.assertEqual(result["reviewed_items"], 30)
        self.assertEqual(result["precision"], 0.6)
        self.assertEqual(result["recall"], 0.9)
        self.assertEqual(result["usefulness"], 0.5625)
        self.assertTrue(result["false_negative_observed"])
        self.assertFalse(result["recall_provisional"])

    def test_recall_is_marked_provisional_when_no_missed_items(self):
        summary = {
            "reviewed_minutes_tasks": 12,
            "counts": {"good": 4, "bad": 2, "missed": 0, "pending": 6},
        }
        curated = {"source_items": 4, "curated_items": 2}
        result = module.compute_domain_metrics(summary, curated, domain="minutes")
        self.assertEqual(result["recall"], 1.0)
        self.assertFalse(result["false_negative_observed"])
        self.assertTrue(result["recall_provisional"])


if __name__ == "__main__":
    unittest.main()

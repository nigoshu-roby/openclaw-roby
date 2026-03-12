import importlib.util
import sys
import unittest

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_precision_eval", "/Users/shu/OpenClaw/scripts/roby-precision-eval.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PrecisionEvalTests(unittest.TestCase):
    def test_evaluate_section_returns_fail_when_precision_below_target(self):
        result = module.evaluate_section(
            "gmail",
            {
                "reviewed_items": 10,
                "precision": 0.2,
                "recall": 1.0,
                "review_coverage": 0.8,
                "curated_coverage": 1.0,
                "recall_provisional": False,
            },
            module.THRESHOLDS["gmail"],
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("precision" in issue for issue in result["issues"]))

    def test_evaluate_section_returns_attention_for_provisional_recall(self):
        result = module.evaluate_section(
            "minutes",
            {
                "reviewed_items": 12,
                "precision": 0.5,
                "recall": 1.0,
                "review_coverage": 0.8,
                "curated_coverage": 1.0,
                "recall_provisional": True,
            },
            module.THRESHOLDS["minutes"],
        )
        self.assertEqual(result["status"], "attention")
        self.assertTrue(any("暫定値" in issue for issue in result["issues"]))

    def test_compute_gate_uses_fail_as_worst_state(self):
        sections = [
            {"name": "overall", "status": "attention", "issues": []},
            {"name": "gmail", "status": "fail", "issues": []},
            {"name": "minutes", "status": "ok", "issues": []},
        ]
        self.assertEqual(module.compute_gate(sections, []), "fail")

    def test_build_summary_lists_each_domain_status(self):
        summary = module.build_summary(
            "attention",
            [
                {"name": "overall", "status": "attention"},
                {"name": "gmail", "status": "ok"},
                {"name": "minutes", "status": "fail"},
            ],
        )
        self.assertIn("gate=attention", summary)
        self.assertIn("overall=attention", summary)
        self.assertIn("minutes=fail", summary)


if __name__ == "__main__":
    unittest.main()

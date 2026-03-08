#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import TestCase, main


def _load_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby-eval-harness.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_eval_harness_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestRobyEvalHarness(TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_evaluate_gates_detects_new_failure_and_latency(self):
        report = {
            "failed": 1,
            "failure_rate": 0.2,
            "latency": {"p95_ms": 1500, "avg_ms": 900},
            "results": [
                {"id": "case-ok", "ok": True},
                {"id": "case-new", "ok": False},
            ],
        }
        previous = {
            "results": [
                {"id": "case-ok", "ok": True},
                {"id": "case-old", "ok": False},
            ]
        }
        policy = self.mod.EvalPolicy(
            max_failed_cases=0,
            max_failure_rate=0.0,
            allow_new_failures=0,
            max_p95_ms=1000,
            max_avg_ms=800,
        )
        gates = self.mod.evaluate_gates(report, previous, policy, skip_gates=False)
        self.assertFalse(gates["ok"])
        self.assertIn("case-new", gates["new_failures"])
        self.assertIn("case-old", gates["resolved_failures"])
        self.assertTrue(any("max_p95_ms" in item for item in gates["failures"]))
        self.assertTrue(any("allow_new_failures" in item for item in gates["failures"]))

    def test_build_markdown_includes_gate_summary(self):
        report = {
            "ts": "2026-03-08T15:10:00+09:00",
            "total": 2,
            "passed": 1,
            "failed": 1,
            "failure_rate": 0.5,
            "latency": {"avg_ms": 120, "p95_ms": 200},
            "retries": {"total": 1, "cases_with_retry": 1},
            "gates": {
                "ok": False,
                "new_failures": ["case-b"],
                "resolved_failures": ["case-a"],
                "failures": ["gate:max_failed_cases exceeded actual=1 limit=0"],
            },
            "results": [
                {"id": "case-a", "ok": True, "elapsed_ms": 100, "attempt_count": 1, "failures": []},
                {"id": "case-b", "ok": False, "elapsed_ms": 200, "attempt_count": 2, "failures": ["x"]},
            ],
        }
        md = self.mod.build_markdown(report)
        self.assertIn("# PBS Evaluation Harness Report", md)
        self.assertIn("gate: FAIL", md)
        self.assertIn("new_failures: case-b", md)
        self.assertIn("resolved_failures: case-a", md)
        self.assertIn("## Gate Failures", md)

    def test_report_schema_version_constant(self):
        self.assertEqual(self.mod.REPORT_SCHEMA_VERSION, 2)


if __name__ == "__main__":
    main()

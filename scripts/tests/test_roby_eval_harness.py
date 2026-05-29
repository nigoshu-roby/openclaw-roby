#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


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

    def test_run_orchestrator_uses_child_env_and_timeout(self):
        case = self.mod.EvalCase(id="c1", description="", message="hello")
        observed = {}

        def fake_run(cmd, cwd=None, capture_output=None, text=None, env=None, timeout=None):
            observed["env"] = dict(env or {})
            observed["timeout"] = timeout

            class Result:
                returncode = 0
                stdout = '{"route":"qa_gemini","action":{"ok":true}}'
                stderr = ""

            return Result()

        with patch.object(self.mod.subprocess, "run", side_effect=fake_run):
            result = self.mod.run_orchestrator(case, {"GEMINI_API_KEY": "secret-1"}, 77)

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(observed["env"].get("GEMINI_API_KEY"), "secret-1")
        self.assertEqual(observed["env"].get("ROBY_ORCH_AB_ROUTER"), "0")
        self.assertEqual(observed["timeout"], 77)

    def test_run_orchestrator_timeout_returns_124(self):
        case = self.mod.EvalCase(id="c1", description="", message="hello")

        def fake_run(cmd, cwd=None, capture_output=None, text=None, env=None, timeout=None):
            raise self.mod.subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output=b"partial", stderr=b"stuck")

        with patch.object(self.mod.subprocess, "run", side_effect=fake_run):
            result = self.mod.run_orchestrator(case, {}, 3)

        self.assertEqual(result["returncode"], 124)
        self.assertIn("partial", result["stdout"])
        self.assertIn("timed out after 3s", result["stderr"])


if __name__ == "__main__":
    main()

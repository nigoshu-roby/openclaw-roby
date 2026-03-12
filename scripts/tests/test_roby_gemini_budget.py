#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SCRIPTS_DIR / "roby-gemini-budget.py"


def _load_module():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("roby_gemini_budget_module", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRobyGeminiBudget(TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_build_summary_returns_ok_under_soft_limit(self):
        summary = self.mod.build_summary(
            "test",
            [{"estimated_tokens": 1000, "chars": 4000}],
            output_tokens=1000,
            soft_limit=5000,
            hard_limit=10000,
        )
        self.assertEqual(summary["decision"], "ok")
        self.assertEqual(summary["estimated_total_tokens"], 2000)

    def test_build_summary_returns_confirm_required(self):
        summary = self.mod.build_summary(
            "test",
            [{"estimated_tokens": 4500, "chars": 18000}],
            output_tokens=1000,
            soft_limit=5000,
            hard_limit=10000,
        )
        self.assertEqual(summary["decision"], "confirm_required")

    def test_build_summary_returns_blocked(self):
        summary = self.mod.build_summary(
            "test",
            [{"estimated_tokens": 9500, "chars": 38000}],
            output_tokens=1000,
            soft_limit=5000,
            hard_limit=10000,
        )
        self.assertEqual(summary["decision"], "blocked")

    def test_iter_text_inputs_skips_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "a.txt"
            existing.write_text("hello world", encoding="utf-8")
            rows = self.mod.iter_text_inputs([str(existing), str(Path(tmp) / "missing.txt")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chars"], 11)
        self.assertGreater(rows[0]["estimated_tokens"], 0)

    def test_cli_confirm_required_exit_code_without_approve(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.txt"
            path.write_text("x" * 4000, encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--label",
                    "budget-test",
                    "--input-file",
                    str(path),
                    "--output-tokens",
                    "1000",
                    "--soft-limit",
                    "1500",
                    "--hard-limit",
                    "10000",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 2)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["decision"], "confirm_required")

    def test_cli_confirm_required_exit_code_with_approve(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.txt"
            path.write_text("x" * 4000, encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--label",
                    "budget-test",
                    "--input-file",
                    str(path),
                    "--output-tokens",
                    "1000",
                    "--soft-limit",
                    "1500",
                    "--hard-limit",
                    "10000",
                    "--approve",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["decision"], "confirm_required")


if __name__ == "__main__":
    main()

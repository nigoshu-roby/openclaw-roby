#!/usr/bin/env python3
"""
Regression tests for roby-minutes -> Neuronic integration.

Acceptance mapping:
1) parent_origin_id / sibling_order normal flow
2) legacy response compatibility
3) payload-too-large split retry
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest import TestCase, main


def _load_minutes_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby-minutes.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_minutes_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRobyMinutesNeuronic(TestCase):
    def setUp(self):
        self.mod = _load_minutes_module()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.hierarchy_state_path = str(Path(self.tmpdir.name) / "hier_state.json")
        self._orig_send_once = self.mod._send_neuronic_once
        self._orig_append_jsonl = self.mod._append_jsonl
        self._orig_format_cli = self.mod._format_neuronic_cli_logs
        self.mod._append_jsonl = lambda *args, **kwargs: None
        self.mod._format_neuronic_cli_logs = lambda *args, **kwargs: []

    def tearDown(self):
        self.mod._send_neuronic_once = self._orig_send_once
        self.mod._append_jsonl = self._orig_append_jsonl
        self.mod._format_neuronic_cli_logs = self._orig_format_cli
        self.tmpdir.cleanup()

    def test_parent_and_sibling_fields_normal_flow(self):
        extracted = [
            {
                "title": "親タスク",
                "project": "TOKIWAGI_MASTER",
                "assignee": "私",
                "subtasks": [
                    {"title": "子タスク1", "project": "TOKIWAGI_MASTER", "assignee": "私"},
                    {"title": "子タスク2", "project": "TOKIWAGI_MASTER", "assignee": "私"},
                ],
            }
        ]
        tasks = self.mod.build_neuronic_tasks(
            extracted=extracted,
            source="notion",
            source_title="2026/02/17 社内定例MTG",
            source_url="https://example.com/notion",
            default_project="TOKIWAGI_MASTER",
            source_id="page_001",
            run_id="run_test_001",
            include_legacy_group_tag=False,
        )

        self.assertEqual(len(tasks), 3)
        parent, child1, child2 = tasks
        self.assertIsNone(parent.get("parent_origin_id"))
        self.assertEqual(parent.get("sibling_order"), 0)
        self.assertEqual(parent.get("outline_path"), "0")
        self.assertTrue(parent.get("origin_id", "").startswith("roby:auto:"))
        self.assertEqual(child1.get("parent_origin_id"), parent.get("origin_id"))
        self.assertEqual(child2.get("parent_origin_id"), parent.get("origin_id"))
        self.assertEqual(child1.get("sibling_order"), 0)
        self.assertEqual(child2.get("sibling_order"), 1)

        def _ok_once(batch: List[Dict[str, Any]], _env: Dict[str, str]) -> Dict[str, Any]:
            return {
                "ok": True,
                "status_code": 200,
                "endpoint_used": "/api/v1/tasks/import",
                "fallback_used": False,
                "body": {
                    "created": len(batch),
                    "updated": 0,
                    "skipped": 0,
                    "errors": [],
                    "hierarchy_applied": True,
                    "order_applied": True,
                },
            }

        self.mod._send_neuronic_once = _ok_once
        result = self.mod.send_neuronic(tasks, {"ROBY_NEURONIC_VERBOSE": "0"})

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("created"), 3)
        self.assertEqual(result.get("items_with_parent"), 2)
        self.assertEqual(result.get("items_with_order"), 3)
        self.assertIs(result.get("hierarchy_applied"), True)
        self.assertIs(result.get("order_applied"), True)

    def test_legacy_response_compatibility(self):
        tasks = [
            {
                "title": "単発タスク",
                "project": "TOKIWAGI",
                "due_date": "",
                "assignee": "私",
                "note": "",
                "source": "roby",
                "origin_id": "roby:auto:legacy01",
                "status": "inbox",
                "priority": 1,
                "tags": ["project:TOKIWAGI"],
                "parent_origin_id": None,
                "sibling_order": 0,
                "outline_path": "0",
            }
        ]

        def _legacy_once(batch: List[Dict[str, Any]], _env: Dict[str, str]) -> Dict[str, Any]:
            return {
                "ok": True,
                "status_code": 200,
                "endpoint_used": "/api/v1/tasks/bulk",
                "fallback_used": True,
                "body": {
                    "created": len(batch),
                    "updated": 0,
                    "skipped": 0,
                    "errors": [],
                },
            }

        self.mod._send_neuronic_once = _legacy_once
        result = self.mod.send_neuronic(tasks, {"ROBY_NEURONIC_VERBOSE": "0"})

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("created"), 1)
        self.assertIsNone(result.get("hierarchy_applied"))
        self.assertIsNone(result.get("order_applied"))
        self.assertEqual(result.get("endpoint_used"), "/api/v1/tasks/bulk")
        self.assertTrue(result.get("fallback_used"))

    def test_payload_too_large_split_send(self):
        tasks: List[Dict[str, Any]] = []
        for i in range(4):
            tasks.append(
                {
                    "title": f"task-{i}",
                    "project": "TOKIWAGI",
                    "due_date": "",
                    "assignee": "私",
                    "note": "",
                    "source": "roby",
                    "origin_id": f"roby:auto:split{i:02d}",
                    "status": "inbox",
                    "priority": 1,
                    "tags": ["project:TOKIWAGI"],
                    "parent_origin_id": None,
                    "sibling_order": i,
                    "outline_path": str(i),
                }
            )

        send_sizes: List[int] = []

        def _split_once(batch: List[Dict[str, Any]], _env: Dict[str, str]) -> Dict[str, Any]:
            send_sizes.append(len(batch))
            if len(batch) > 1:
                return {
                    "ok": False,
                    "status_code": 413,
                    "error": "HTTP 413",
                    "detail": "Payload Too Large",
                    "endpoint_used": "/api/v1/tasks/import",
                    "fallback_used": False,
                }
            return {
                "ok": True,
                "status_code": 200,
                "endpoint_used": "/api/v1/tasks/import",
                "fallback_used": False,
                "body": {
                    "created": 1,
                    "updated": 0,
                    "skipped": 0,
                    "errors": [],
                    "hierarchy_applied": True,
                    "order_applied": True,
                },
            }

        self.mod._send_neuronic_once = _split_once
        result = self.mod.send_neuronic(
            tasks,
            {
                "ROBY_NEURONIC_VERBOSE": "0",
                "NEURONIC_BATCH_SIZE": "20",
                "NEURONIC_MAX_BATCH_BYTES": "999999",
            },
        )

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("created"), 4)
        self.assertEqual(result.get("error_count"), 0)
        self.assertGreater(len(send_sizes), 4)
        self.assertIn(4, send_sizes)
        self.assertIn(2, send_sizes)
        self.assertGreaterEqual(send_sizes.count(1), 4)

    def test_create_only_mode_preserves_manual_hierarchy_on_resync(self):
        tasks = [
            {
                "title": "親タスク",
                "project": "TOKIWAGI",
                "due_date": "",
                "assignee": "私",
                "note": "",
                "source": "roby",
                "origin_id": "roby:auto:parent01",
                "status": "inbox",
                "priority": 1,
                "tags": ["project:TOKIWAGI"],
                "parent_origin_id": None,
                "sibling_order": 0,
                "outline_path": "0",
            },
            {
                "title": "子タスク",
                "project": "TOKIWAGI",
                "due_date": "",
                "assignee": "私",
                "note": "",
                "source": "roby",
                "origin_id": "roby:auto:child01",
                "status": "inbox",
                "priority": 1,
                "tags": ["project:TOKIWAGI"],
                "parent_origin_id": "roby:auto:parent01",
                "sibling_order": 0,
                "outline_path": "0/0",
            },
        ]

        captured_batches: List[List[Dict[str, Any]]] = []

        def _capture_once(batch: List[Dict[str, Any]], _env: Dict[str, str]) -> Dict[str, Any]:
            captured_batches.append([dict(x) for x in batch])
            return {
                "ok": True,
                "status_code": 200,
                "endpoint_used": "/api/v1/tasks/import",
                "fallback_used": False,
                "body": {"created": len(batch), "updated": 0, "skipped": 0, "errors": []},
            }

        self.mod._send_neuronic_once = _capture_once
        env = {
            "ROBY_NEURONIC_VERBOSE": "0",
            "ROBY_NEURONIC_HIERARCHY_MODE": "create_only",
            "ROBY_NEURONIC_HIERARCHY_STATE_PATH": self.hierarchy_state_path,
        }

        # 1st sync (create): hierarchy fields should be sent.
        first = self.mod.send_neuronic(tasks, env)
        self.assertTrue(first.get("ok"))
        self.assertEqual(len(captured_batches), 1)
        first_batch = captured_batches[0]
        self.assertIn("sibling_order", first_batch[0])
        self.assertIn("parent_origin_id", first_batch[1])

        # 2nd sync (resync): hierarchy fields should be omitted to keep manual edits.
        second = self.mod.send_neuronic(tasks, env)
        self.assertTrue(second.get("ok"))
        self.assertEqual(len(captured_batches), 2)
        second_batch = captured_batches[1]
        self.assertNotIn("sibling_order", second_batch[0])
        self.assertNotIn("outline_path", second_batch[0])
        self.assertNotIn("parent_origin_id", second_batch[1])
        self.assertNotIn("sibling_order", second_batch[1])


if __name__ == "__main__":
    main()

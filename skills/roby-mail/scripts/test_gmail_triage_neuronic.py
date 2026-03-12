#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Dict, List
from unittest import TestCase, main
import urllib.error
import urllib.request
import warnings

warnings.simplefilter("ignore", ResourceWarning)


def _load_module():
    script_path = Path(__file__).resolve().parent / "gmail_triage.py"
    spec = importlib.util.spec_from_file_location("gmail_triage_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    def __init__(self, body: Dict):
        self._body = json.dumps(body).encode("utf-8")
        self.status = 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(code: int, body: str = ""):
    return urllib.error.HTTPError(
        url="http://127.0.0.1",
        code=code,
        msg="error",
        hdrs=None,
        fp=None,
    )


class TestGmailTriageNeuronic(TestCase):
    def setUp(self):
        self.mod = _load_module()
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def _tasks(self, n: int) -> List[Dict]:
        rows: List[Dict] = []
        for i in range(n):
            rows.append(
                {
                    "title": f"task-{i}",
                    "project": "email",
                    "due_date": "",
                    "assignee": "私",
                    "note": "",
                    "source": "roby",
                    "origin_id": f"roby:auto:test{i}",
                    "status": "inbox",
                    "priority": 1,
                    "tags": ["project:email"],
                    "parent_origin_id": None,
                    "sibling_order": i,
                }
            )
        return rows

    def test_payload_too_large_is_split_and_succeeds(self):
        send_sizes: List[int] = []

        def fake_urlopen(req, timeout=10):
            payload = json.loads(req.data.decode("utf-8"))
            items = payload.get("items", [])
            send_sizes.append(len(items))
            if len(items) > 1:
                raise _http_error(413, "Payload Too Large")
            return _Resp({"created": 1, "updated": 0, "skipped": 0, "errors": [], "hierarchy_applied": True, "order_applied": True})

        urllib.request.urlopen = fake_urlopen
        result = self.mod.send_neuronic(self._tasks(4), {"NEURONIC_BATCH_SIZE": "10"})

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("created"), 4)
        self.assertEqual(result.get("error_count"), 0)
        self.assertIn(4, send_sizes)
        self.assertIn(2, send_sizes)
        self.assertGreaterEqual(send_sizes.count(1), 4)
        self.assertIs(result.get("hierarchy_applied"), True)
        self.assertIs(result.get("order_applied"), True)

    def test_404_import_fallbacks_to_bulk(self):
        called = {"import": 0, "bulk": 0}

        def fake_urlopen(req, timeout=10):
            url = req.full_url
            if url.endswith("/api/v1/tasks/import"):
                called["import"] += 1
                raise _http_error(404, "Not Found")
            if url.endswith("/api/v1/tasks/bulk"):
                called["bulk"] += 1
                payload = json.loads(req.data.decode("utf-8"))
                return _Resp({"created": len(payload.get("items", [])), "updated": 0, "skipped": 0, "errors": []})
            raise AssertionError(f"unexpected url: {url}")

        urllib.request.urlopen = fake_urlopen
        result = self.mod.send_neuronic(self._tasks(3), {})

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("created"), 3)
        self.assertEqual(called["import"], 1)
        self.assertEqual(called["bulk"], 1)
        self.assertTrue(result.get("fallback_used"))

    def test_build_tasks_prefixes_sender_on_parent_and_children(self):
        msg = {
            "subject": "見積書の件",
            "threadId": "thread-1",
            "id": "msg-1",
            "from": "\"高田彰\" <a.takata@tokiwa-gi.com>",
            "date": "2026-03-12 10:00",
        }
        extracted = [
            {
                "title": "返信内容を確認する",
                "project": "email",
                "due_date": "",
                "note": "",
            }
        ]

        tasks = self.mod.build_tasks(extracted, msg, "needs_review", [], "roby:gmail:test")

        self.assertEqual(len(tasks), 2)
        self.assertTrue(tasks[0]["title"].startswith("【高田彰】メール確認: 見積書の件"))
        self.assertEqual(tasks[1]["title"], "【高田彰】返信内容を確認する")
        self.assertEqual(tasks[1]["parent_origin_id"], tasks[0]["origin_id"])

    def test_normalize_extracted_actions_inserts_reply_for_needs_reply(self):
        rows = self.mod.normalize_extracted_actions(
            [{"title": "見積書を確認する", "project": "email", "due_date": "", "note": ""}],
            raw_category="needs_reply",
            subject="見積書の件",
        )
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]["task_kind"], "reply")
        self.assertIn("返信", rows[0]["title"])

    def test_build_tasks_marks_reply_and_action_tags(self):
        msg = {
            "subject": "ミーティング日程の件",
            "threadId": "thread-2",
            "id": "msg-2",
            "from": "\"高田彰\" <a.takata@tokiwa-gi.com>",
            "date": "2026-03-12 12:00",
        }
        extracted = [
            {"title": "返信内容を確認する", "project": "email", "due_date": "", "note": "", "task_kind": "reply"},
            {"title": "候補日を整理する", "project": "email", "due_date": "", "note": "", "task_kind": "action"},
        ]
        tasks = self.mod.build_tasks(extracted, msg, "task", [], "roby:gmail:test", raw_category="needs_reply")
        self.assertEqual(len(tasks), 3)
        self.assertIn("task_type:reply", tasks[1]["tags"])
        self.assertIn("task_type:action", tasks[2]["tags"])

    def test_task_gate_downgrades_generic_low_confidence_task(self):
        final_bucket, reason, meta = self.mod.decide_task_gate(
            "needs_review",
            "task",
            [{"title": "メール内容を確認して対応する", "task_kind": "action", "project": "email", "due_date": "", "note": ""}],
            {
                "signals": {
                    "meeting_coordination": False,
                    "business_review": False,
                    "actionable_notice": False,
                    "alert": False,
                    "promo_sender_domain": False,
                    "is_noreply": False,
                },
                "bucket_scores": {"newsletter": 0},
                "contact_importance": {"tier": "none", "thread_replied": False},
            },
            [],
        )
        self.assertEqual(final_bucket, "review")
        self.assertEqual(reason, "low_confidence_downgraded_to_review")
        self.assertFalse(meta["task_gate"]["applied"])

    def test_task_gate_keeps_high_confidence_reply_task(self):
        final_bucket, reason, meta = self.mod.decide_task_gate(
            "needs_reply",
            "task",
            [{"title": "返信内容を確認して返信する", "task_kind": "reply", "project": "email", "due_date": "", "note": ""}],
            {
                "signals": {
                    "meeting_coordination": False,
                    "business_review": True,
                    "actionable_notice": True,
                    "alert": False,
                    "promo_sender_domain": False,
                    "is_noreply": False,
                },
                "bucket_scores": {"newsletter": 0},
                "contact_importance": {"tier": "medium", "thread_replied": True},
            },
            ["contact:known"],
        )
        self.assertEqual(final_bucket, "task")
        self.assertEqual(reason, "high_confidence_task")
        self.assertTrue(meta["task_gate"]["applied"])


if __name__ == "__main__":
    main()

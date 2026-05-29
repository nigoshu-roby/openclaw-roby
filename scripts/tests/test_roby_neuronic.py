#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import TestCase, main
import urllib.request


def _load_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script_path = scripts_dir / "roby_neuronic.py"
    spec = importlib.util.spec_from_file_location("roby_neuronic_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    status = 200

    def __init__(self, body):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestRobyNeuronic(TestCase):
    def setUp(self):
        self.mod = _load_module()
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_build_neuronic_items_adds_camel_case_aliases(self):
        rows = self.mod.build_neuronic_items(
            [
                {
                    "origin_id": "roby:auto:1",
                    "parent_origin_id": "roby:auto:parent",
                    "sibling_order": 2,
                    "outline_path": "0/2",
                    "external_ref": "gmail:thread",
                    "run_id": "roby:test",
                    "feedback_state": "pending",
                    "source_doc_id": "doc1",
                    "source_doc_title": "議事録",
                }
            ]
        )

        row = rows[0]
        self.assertEqual(row["parentOriginId"], "roby:auto:parent")
        self.assertEqual(row["siblingOrder"], 2)
        self.assertEqual(row["outlinePath"], "0/2")
        self.assertEqual(row["externalRef"], "gmail:thread")
        self.assertEqual(row["runId"], "roby:test")
        self.assertEqual(row["feedbackState"], "pending")
        self.assertEqual(row["sourceDocId"], "doc1")
        self.assertEqual(row["sourceDocTitle"], "議事録")

    def test_build_neuronic_items_can_skip_outline_path(self):
        rows = self.mod.build_neuronic_items([{"outline_path": "0"}], include_outline_path=False)
        self.assertIn("outline_path", rows[0])
        self.assertNotIn("outlinePath", rows[0])

    def test_post_neuronic_items_keeps_path_endpoint_style(self):
        captured = {}

        def fake_urlopen(req, timeout=10):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            captured["headers"] = dict(req.header_items())
            return _Resp({"created": 1})

        urllib.request.urlopen = fake_urlopen
        res = self.mod.post_neuronic_items(
            "http://127.0.0.1:5174/api/v1/tasks/import",
            [{"title": "task"}],
            headers=self.mod.build_neuronic_headers({"NEURONIC_TOKEN": "token"}),
            endpoint_style="path",
        )

        self.assertEqual(res["endpoint_used"], "/api/v1/tasks/import")
        self.assertEqual(res["body"], {"created": 1})
        self.assertEqual(captured["payload"], {"items": [{"title": "task"}]})
        self.assertEqual(captured["headers"]["Authorization"], "Bearer token")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Tests for Gmail contact importance index."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main


def _load_module():
    script_path = Path(__file__).resolve().parent / "gmail_contact_index.py"
    script_dir = str(script_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("gmail_contact_index_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGmailContactIndex(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_importance_tier_prefers_replied_thread(self):
        tier, score = self.mod.importance_tier(True, 1, 1)
        self.assertEqual(tier, "high")
        self.assertGreaterEqual(score, 8)

    def test_build_contact_index_counts_senders_domains_and_threads(self):
        sent = [
            {"id": "t1"},
            {"id": "t2"},
            {"id": "t2"},
            {"id": "t3"},
        ]
        threads = [
            {"id": "t1", "from": "高田彰 <a.takata@tokiwa-gi.com>", "subject": "件名1", "date": "2026-03-10 10:00"},
            {"id": "t2", "from": "高田彰 <a.takata@tokiwa-gi.com>", "subject": "件名2", "date": "2026-03-11 10:00"},
            {"id": "t3", "from": "飯海様 <iiumi@zuiho-group.co.jp>", "subject": "件名3", "date": "2026-03-09 10:00"},
        ]
        index = self.mod.build_contact_index(sent, threads, lookback_months=18, generated_at="2026-03-12T00:00:00+09:00")
        self.assertEqual(index["replied_thread_count"], 3)
        self.assertEqual(index["indexed_sender_count"], 2)
        self.assertEqual(index["indexed_domain_count"], 2)
        takata = index["sender_index"]["a.takata@tokiwa-gi.com"]
        self.assertEqual(takata["thread_count"], 2)
        self.assertIn(takata["tier"], {"low", "medium", "high"})


if __name__ == "__main__":
    main()

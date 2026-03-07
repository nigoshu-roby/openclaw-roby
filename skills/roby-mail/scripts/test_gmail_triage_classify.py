#!/usr/bin/env python3
"""Tests for gmail_triage rules bootstrap and classification tuning."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main


def _load_module():
    script_path = Path(__file__).resolve().parent / "gmail_triage.py"
    script_dir = str(script_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location("gmail_triage_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGmailTriageClassify(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_load_rules_bootstraps_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rules.json"
            rules = self.mod.load_rules(path)
            self.assertTrue(path.exists())
            self.assertIn("force_archive", rules)
            self.assertIn("force_review", rules)
            self.assertIn("force_reply", rules)
            self.assertIn("mapbox.com", [x.lower() for x in rules["force_archive"]["sender_domains"]])
            self.assertIn("tokiwa-gi.com", [x.lower() for x in rules["force_review"]["sender_domains"]])

    def test_internal_domain_in_cc_forces_review(self):
        category, tags, needs_reply, rule = self.mod.classify_message(
            subject="FYI",
            sender="external@example.com",
            body="共有です",
            rules={},
            cc="member@tokiwa-gi.com",
        )
        self.assertEqual(category, "needs_review")
        self.assertEqual(rule, "internal_domain_review")
        self.assertFalse(needs_reply)
        self.assertTrue(any("internal_domain_review" in x for x in tags))

    def test_promo_sender_domain_is_archived(self):
        category, _, _, _ = self.mod.classify_message(
            subject="Not sure where to start with Mapbox?",
            sender="Team Mapbox <hello@mapbox.com>",
            body="イベントのご案内です",
            rules={},
        )
        self.assertEqual(category, "archive")

    def test_actionable_notice_kept_for_review(self):
        category, _, _, _ = self.mod.classify_message(
            subject="【重要】Synergy!アカウント発行のお知らせ",
            sender="Synergy!カスタマーサポート <support@crmstyle.com>",
            body="アカウント発行のご連絡です",
            rules={},
        )
        self.assertEqual(category, "needs_review")

    def test_line_approval_noreply_is_archived(self):
        category, _, _, _ = self.mod.classify_message(
            subject="広告が承認されました",
            sender="no-reply@line.me",
            body="広告アカウントが承認されました",
            rules={},
        )
        self.assertEqual(category, "archive")

    def test_user_rule_can_force_reply(self):
        rules = {
            "force_archive": {"sender_domains": [], "sender_contains": [], "subject_contains": [], "subject_regex": []},
            "force_review": {"sender_domains": [], "sender_contains": [], "subject_contains": [], "subject_regex": []},
            "force_reply": {"sender_domains": ["example.com"], "sender_contains": [], "subject_contains": [], "subject_regex": []},
        }
        category, _, needs_reply, rule = self.mod.classify_message(
            subject="確認お願いします",
            sender="foo@example.com",
            body="返信お願いします",
            rules=rules,
        )
        self.assertEqual(category, "needs_reply")
        self.assertEqual(rule, "force_reply")
        self.assertTrue(needs_reply)

    def test_cap_extracted_actions(self):
        rows = [{"title": f"t{i}"} for i in range(10)]
        capped = self.mod.cap_extracted_actions(rows, 4)
        self.assertEqual(len(capped), 4)
        self.assertEqual(capped[0]["title"], "t0")
        self.assertEqual(capped[-1]["title"], "t3")

    def test_cap_extracted_actions_disabled_when_non_positive(self):
        rows = [{"title": f"t{i}"} for i in range(3)]
        capped = self.mod.cap_extracted_actions(rows, 0)
        self.assertEqual(len(capped), 3)


if __name__ == "__main__":
    main()

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
        category, tags, needs_reply, rule, _meta = self.mod.classify_message(
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
        category, _, _, _, _meta = self.mod.classify_message(
            subject="Not sure where to start with Mapbox?",
            sender="Team Mapbox <hello@mapbox.com>",
            body="イベントのご案内です",
            rules={},
        )
        self.assertEqual(category, "archive")

    def test_actionable_notice_kept_for_review(self):
        category, _, _, _, _meta = self.mod.classify_message(
            subject="【重要】Synergy!アカウント発行のお知らせ",
            sender="Synergy!カスタマーサポート <support@crmstyle.com>",
            body="アカウント発行のご連絡です",
            rules={},
        )
        self.assertEqual(category, "needs_review")

    def test_instagram_recap_sender_is_archived(self):
        category, _, needs_reply, _, _meta = self.mod.classify_message(
            subject="brodo_japan、見逃したコンテンツをチェックしよう",
            sender="Instagram <posts-recaps@mail.instagram.com>",
            body="最新のコンテンツをチェックしましょう。",
            rules={},
        )
        self.assertEqual(category, "archive")
        self.assertFalse(needs_reply)

    def test_promo_sender_with_invoice_signal_is_not_archived(self):
        category, _, _, _, _meta = self.mod.classify_message(
            subject="【重要】請求書のご案内",
            sender="Mapbox Billing <hello@mapbox.com>",
            body="請求書をご確認ください",
            rules={},
        )
        self.assertEqual(category, "needs_review")

    def test_explicit_reply_request_sets_needs_reply(self):
        category, tags, needs_reply, _, meta = self.mod.classify_message(
            subject="ご確認のお願い",
            sender="client@example.com",
            body="内容をご確認のうえ、ご返信をお願いします。",
            rules={},
        )
        bucket, reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(category, "needs_reply")
        self.assertTrue(needs_reply)
        self.assertEqual(bucket, "task")
        self.assertEqual(reason, "explicit_reply_or_action")

    def test_marketing_like_subject_with_estimate_signal_stays_reviewable(self):
        category, _, _, _, _meta = self.mod.classify_message(
            subject="【無料で試せる】見積書をご確認ください",
            sender="Sales Team <info@example.com>",
            body="見積書を送付します。内容をご確認ください。",
            rules={},
        )
        self.assertEqual(category, "needs_review")

    def test_coupon_promo_does_not_become_reply_task(self):
        category, tags, needs_reply, _, meta = self.mod.classify_message(
            subject="＼3/31迄／⛳【甘楽カントリークラブ（群馬県）】1,000円割引クーポンプレゼント🎉 | アコーディアWeb",
            sender="アコーディアWeb <info@ma.accordiagolf.com>",
            body="クーポンのご案内です。詳しくは本文をご確認ください。",
            rules={},
        )
        bucket, _reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertFalse(needs_reply)
        self.assertNotEqual(bucket, "task")

    def test_funding_marketing_mail_does_not_become_reply_task(self):
        category, tags, needs_reply, _, meta = self.mod.classify_message(
            subject="忙しい3月こそ要注意！年度末の支払いピンチを救う資金調達",
            sender="Chatwork DX相談窓口 <news@ns.chatwork.com>",
            body="資金調達のヒントをお届けします。詳細は本文をご確認ください。",
            rules={},
        )
        bucket, _reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertFalse(needs_reply)
        self.assertNotEqual(bucket, "task")

    def test_line_approval_noreply_is_archived(self):
        category, _, _, _, _meta = self.mod.classify_message(
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
        category, _, needs_reply, rule, _meta = self.mod.classify_message(
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

    def test_local_preclassify_can_promote_archive_to_review(self):
        original = self.mod.local_preclassify_email
        try:
            self.mod.local_preclassify_email = lambda *args, **kwargs: (
                "needs_review",
                "billing notice",
                {"enabled": True, "ok": True},
            )
            category, tags, _, _, meta = self.mod.classify_message(
                subject="Mapbox webinar",
                sender="hello@mapbox.com",
                body="event notice",
                rules={},
                env={"GMAIL_TRIAGE_LOCAL_PRECLASSIFY_ENABLE": "1"},
            )
            self.assertEqual(category, "needs_review")
            self.assertIn("local:override", tags)
            self.assertEqual(meta.get("local_reason"), "billing notice")
        finally:
            self.mod.local_preclassify_email = original

    def test_local_preclassify_cannot_archive_billing_notice(self):
        original = self.mod.local_preclassify_email
        try:
            self.mod.local_preclassify_email = lambda *args, **kwargs: (
                "archive",
                "promo",
                {"enabled": True, "ok": True},
            )
            category, tags, _, _, _meta = self.mod.classify_message(
                subject="【重要】請求書のご案内",
                sender="Mapbox Billing <hello@mapbox.com>",
                body="請求書をご確認ください",
                rules={},
                env={"GMAIL_TRIAGE_LOCAL_PRECLASSIFY_ENABLE": "1"},
            )
            self.assertEqual(category, "needs_review")
            self.assertNotIn("local:override", tags)
        finally:
            self.mod.local_preclassify_email = original

    def test_known_replied_thread_promotes_archive_to_review(self):
        contact_index = {
            "thread_index": {
                "thread-1": {
                    "thread_id": "thread-1",
                    "sender_email": "hello@mapbox.com",
                    "sender_domain": "mapbox.com",
                }
            },
            "sender_index": {
                "hello@mapbox.com": {"thread_count": 1}
            },
            "domain_index": {
                "mapbox.com": {"thread_count": 1}
            },
        }
        category, tags, _, _, meta = self.mod.classify_message(
            subject="Not sure where to start with Mapbox?",
            sender="Team Mapbox <hello@mapbox.com>",
            body="イベントのご案内です",
            rules={},
            thread_id="thread-1",
            contact_index=contact_index,
        )
        self.assertEqual(category, "needs_review")
        self.assertIn("contact:override", tags)
        self.assertEqual(meta.get("contact_reason"), "known_contact_promoted_from_archive")

    def test_known_high_contact_promotes_later_check_to_review(self):
        contact_index = {
            "thread_index": {},
            "sender_index": {
                "ops@example.com": {"thread_count": 4}
            },
            "domain_index": {
                "example.com": {"thread_count": 6}
            },
        }
        category, tags, _, _, meta = self.mod.classify_message(
            subject="設定のお知らせ",
            sender="Google Ops <ops@example.com>",
            body="Google と AWS の設定です",
            rules={},
            thread_id="thread-2",
            contact_index=contact_index,
        )
        self.assertEqual(category, "needs_review")
        self.assertIn("contact:known", tags)
        self.assertIn("contact:override", tags)
        self.assertEqual(meta.get("contact_reason"), "known_contact_promoted_from_later_check")

    def test_work_bucket_maps_later_check_to_digest(self):
        bucket, reason = self.mod.decide_work_bucket("later_check", False, {"signals": {}}, [])
        self.assertEqual(bucket, "digest")
        self.assertEqual(reason, "tool_notice_or_digest")

    def test_work_bucket_maps_needs_reply_to_task(self):
        bucket, reason = self.mod.decide_work_bucket("needs_reply", True, {"signals": {}}, [])
        self.assertEqual(bucket, "task")
        self.assertEqual(reason, "explicit_reply_or_action")

    def test_work_bucket_keeps_plain_review_as_review(self):
        bucket, reason = self.mod.decide_work_bucket(
            "needs_review",
            False,
            {"signals": {"meeting_coordination": False}},
            [],
        )
        self.assertEqual(bucket, "review")
        self.assertEqual(reason, "human_review_needed")

    def test_work_bucket_promotes_meeting_coordination_to_task(self):
        bucket, reason = self.mod.decide_work_bucket(
            "needs_review",
            False,
            {"signals": {"meeting_coordination": True}},
            [],
        )
        self.assertEqual(bucket, "task")
        self.assertEqual(reason, "coordination_requires_followup")

    def test_work_bucket_downgrades_marketing_review_to_digest(self):
        bucket, reason = self.mod.decide_work_bucket(
            "needs_review",
            False,
            {
                "signals": {
                    "meeting_coordination": False,
                    "promo_subject": True,
                    "marketing_sender": True,
                    "promo_sender_domain": False,
                    "ad_hint": True,
                    "is_noreply": True,
                    "business_review": False,
                    "actionable_notice": False,
                    "alert": False,
                    "urgent": False,
                }
            },
            [],
        )
        self.assertEqual(bucket, "digest")
        self.assertEqual(reason, "newsletter_review_downgraded")

    def test_work_bucket_promotes_known_tool_notice_to_review(self):
        bucket, reason = self.mod.decide_work_bucket(
            "later_check",
            False,
            {
                "signals": {
                    "business_review": False,
                    "actionable_notice": False,
                    "alert": False,
                    "urgent": False,
                    "promo_subject": False,
                    "marketing_sender": False,
                    "promo_sender_domain": False,
                    "ad_hint": False,
                    "is_noreply": True,
                    "meeting_coordination": False,
                },
                "contact_importance": {
                    "known": True,
                    "thread_replied": True,
                    "tier": "high",
                },
            },
            ["tool:google"],
        )
        self.assertEqual(bucket, "review")

    def test_context_seed_sender_hint_marks_sender_as_known_contact(self):
        sender_hints, domain_hints = self.mod.build_context_sender_hints(
            {
                "email": {
                    "important_senders": [
                        {
                            "name": "飯野さん",
                            "emails": ["t-iino@bornelund.co.jp"],
                            "domains": ["bornelund.co.jp"],
                            "importance": "高",
                            "company": "株式会社ボーネルンド",
                            "topics": "運用調整",
                        }
                    ]
                }
            }
        )
        meta = self.mod.contact_importance(
            "",
            "飯野友明 <t-iino@bornelund.co.jp>",
            {},
            context_sender_hints=sender_hints,
            context_domain_hints=domain_hints,
        )
        self.assertTrue(meta["known"])
        self.assertTrue(meta["context_seed"])
        self.assertIn(meta["tier"], {"medium", "high"})


if __name__ == "__main__":
    main()

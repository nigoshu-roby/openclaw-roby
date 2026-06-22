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
            self.assertIn("asobi-yoyaku@bornelund.co.jp", [x.lower() for x in rules["force_archive"]["sender_contains"]])
            self.assertIn("just-enterprise@mail.justsystems.com", [x.lower() for x in rules["force_archive"]["sender_contains"]])
            self.assertIn("info@social-db.co.jp", [x.lower() for x in rules["force_archive"]["sender_contains"]])
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

    def test_calendar_acceptance_is_archived(self):
        category, tags, needs_reply, rule, _meta = self.mod.classify_message(
            subject="承諾: ボーネルンド様：スマレジ画面打ち合わせ＠本社",
            sender='"田子一之" <tago@tokiwa-gi.com>',
            body="承諾されました。",
            rules={},
        )
        self.assertEqual(category, "archive")
        self.assertEqual(rule, "calendar_response")
        self.assertFalse(needs_reply)
        self.assertIn("rule:calendar_response", tags)

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

    def test_contract_prep_request_becomes_task(self):
        category, tags, needs_reply, _, meta = self.mod.classify_message(
            subject="Re: R8年度契約について",
            sender="田中麻紀子 <makiko-tanaka@boatrace-hamanako.or.jp>",
            body="契約更新が決定しました。契約書のご準備をお願い致します。",
            rules={},
        )
        bucket, reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(category, "needs_review")
        self.assertFalse(needs_reply)
        self.assertEqual(bucket, "task")
        self.assertEqual(reason, "coordination_requires_followup")

    def test_contract_prep_request_passes_task_gate(self):
        category, tags, needs_reply, _, meta = self.mod.classify_message(
            subject="Re: R8年度契約について",
            sender="田中麻紀子 <makiko-tanaka@boatrace-hamanako.or.jp>",
            body="契約更新が決定しました。契約書のご準備をお願い致します。",
            rules={},
        )
        bucket, _reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        final_bucket, gate_reason, gated_meta = self.mod.decide_task_gate(
            category,
            bucket,
            [{"title": "メール内容を確認して対応する", "task_kind": "action", "note": "", "due_date": "", "project": "email"}],
            meta,
            tags,
        )
        self.assertEqual(final_bucket, "task")
        self.assertEqual(gate_reason, "high_confidence_task")
        self.assertGreaterEqual(gated_meta["task_gate"]["confidence"], 4.0)

    def test_extract_explicit_email_actions_for_contract_prep(self):
        actions = self.mod.extract_explicit_email_actions(
            "Re: R8年度契約について",
            "契約更新が決定しました。契約書のご準備をお願い致します。",
            raw_category="needs_review",
            meta={"signals": {"contract_followup_subject": True}},
            tags=[],
        )
        titles = [item["title"] for item in actions]
        self.assertIn("契約書を準備する", titles)

    def test_marketing_like_subject_with_estimate_signal_stays_reviewable(self):
        category, tags, needs_reply, _, meta = self.mod.classify_message(
            subject="【無料で試せる】見積書をご確認ください",
            sender="Sales Team <info@example.com>",
            body="見積書を送付します。内容をご確認ください。",
            rules={},
        )
        bucket, reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(category, "needs_review")
        self.assertEqual(bucket, "review")
        self.assertEqual(reason, "broadcast_business_review")

    def test_broadcast_business_words_do_not_become_reply_task(self):
        category, tags, needs_reply, _, meta = self.mod.classify_message(
            subject="【業務改善通信 Vol.12】契約更新と請求確認のポイント",
            sender="Marketing Team <news@example.com>",
            body="本メールはメールマガジンです。契約更新や請求確認の事例をご紹介します。ご確認ください。",
            rules={},
        )
        bucket, reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(category, "needs_review")
        self.assertFalse(needs_reply)
        self.assertEqual(bucket, "review")
        self.assertEqual(reason, "broadcast_business_review")

    def test_coupon_promo_does_not_become_reply_task(self):
        category, tags, needs_reply, _, meta = self.mod.classify_message(
            subject="＼3/31迄／⛳【甘楽カントリークラブ（群馬県）】1,000円割引クーポンプレゼント🎉 | アコーディアWeb",
            sender="アコーディアWeb <info@ma.accordiagolf.com>",
            body="クーポンのご案内です。詳しくは本文をご確認ください。",
            rules={},
        )
        bucket, _reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(category, "archive")
        self.assertFalse(needs_reply)
        self.assertNotEqual(bucket, "task")

    def test_pipeline_success_is_archived(self):
        category, tags, needs_reply, rule, _meta = self.mod.classify_message(
            subject="[AWS PIPELINE] 成功 - ETL結果 (2026-03-15)",
            sender='"s.nigo@tokiwa-gi.com" <s.nigo@tokiwa-gi.com>',
            body="正常終了しました。",
            rules={},
        )
        self.assertEqual(category, "archive")
        self.assertEqual(rule, "pipeline_success_archive")
        self.assertFalse(needs_reply)
        self.assertIn("rule:pipeline_success_archive", tags)

    def test_tokiwagi_base_info_is_archived(self):
        category, tags, needs_reply, rule, _meta = self.mod.classify_message(
            subject="[tokiwagi-base.tw5.quickconnect.to] TOKIWAGI-BASE 上の DSM とパッケージが最新版ではありません",
            sender="TOKIWAGI-BASE - Synology NAS <s.nigo@tokiwa-gi.com>",
            body="最新版ではありません。",
            rules={},
        )
        self.assertEqual(category, "archive")
        self.assertEqual(rule, "tokiwagi_base_info_archive")
        self.assertFalse(needs_reply)
        self.assertIn("rule:tokiwagi_base_info_archive", tags)

    def test_internal_instagram_recap_is_archived(self):
        category, tags, needs_reply, rule, _meta = self.mod.classify_message(
            subject="tokiwagi_business ― フィードでpokiiir、ryoko698などをチェックしよう",
            sender='"Instagram" via info <info@tokiwa-gi.com>',
            body="Instagram の更新です。",
            rules={},
        )
        self.assertEqual(category, "archive")
        self.assertEqual(rule, "internal_instagram_recap_archive")
        self.assertFalse(needs_reply)
        self.assertIn("rule:internal_instagram_recap_archive", tags)

    def test_funding_marketing_mail_does_not_become_reply_task(self):
        category, tags, needs_reply, _, meta = self.mod.classify_message(
            subject="忙しい3月こそ要注意！年度末の支払いピンチを救う資金調達",
            sender="Chatwork DX相談窓口 <news@ns.chatwork.com>",
            body="資金調達のヒントをお届けします。詳細は本文をご確認ください。",
            rules={},
        )
        bucket, _reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(category, "archive")
        self.assertFalse(needs_reply)
        self.assertNotEqual(bucket, "task")

    def test_chatwork_mention_is_not_archived(self):
        category, _tags, needs_reply, _rule, _meta = self.mod.classify_message(
            subject="Chatwork メンション通知",
            sender="Chatwork <notify@chatwork.com>",
            body="あなた宛のメンションがあります。",
            rules={},
        )
        self.assertNotEqual(category, "archive")
        self.assertFalse(needs_reply)

    def test_autoro_error_notice_becomes_task_and_passes_gate(self):
        category, tags, needs_reply, _, meta = self.mod.classify_message(
            subject="スケジュールエラー通知 [AUTORO]",
            sender="AUTORO <noreply@autoro.io>",
            body="ワークフローでエラーが発生しました。",
            rules={},
        )
        bucket, _reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        final_bucket, gate_reason, gated_meta = self.mod.decide_task_gate(
            category,
            bucket,
            [{"title": "メール内容を確認して対応する", "task_kind": "action", "note": "", "due_date": "", "project": "email"}],
            meta,
            tags,
        )
        self.assertEqual(bucket, "task")
        self.assertEqual(final_bucket, "task")
        self.assertEqual(gate_reason, "high_confidence_task")
        self.assertGreaterEqual(gated_meta["task_gate"]["confidence"], 4.0)

    def test_extract_explicit_email_actions_for_autoro_error(self):
        actions = self.mod.extract_explicit_email_actions(
            "スケジュールエラー通知 [AUTORO]",
            "ワークフローでエラーが発生しました。",
            raw_category="needs_review",
            meta={"signals": {"alert": True}},
            tags=["tool:autoro"],
        )
        titles = [item["title"] for item in actions]
        self.assertIn("AUTOROのエラー内容を確認する", titles)

    def test_autoro_force_review_override_does_not_block_task_path(self):
        rules = {
            "force_archive": {"sender_domains": [], "sender_contains": [], "subject_contains": [], "subject_regex": []},
            "force_review": {"sender_domains": ["autoro.io"], "sender_contains": [], "subject_contains": [], "subject_regex": []},
            "force_reply": {"sender_domains": [], "sender_contains": [], "subject_contains": [], "subject_regex": []},
        }
        category, tags, needs_reply, rule, meta = self.mod.classify_message(
            subject="スケジュールエラー通知 [AUTORO]",
            sender="AUTORO <noreply@autoro.io>",
            body="ワークフローでエラーが発生しました。",
            rules=rules,
        )
        bucket, _reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(rule, None)
        self.assertEqual(bucket, "task")

    def test_line_approval_noreply_is_archived(self):
        category, _, _, _, _meta = self.mod.classify_message(
            subject="広告が承認されました",
            sender="no-reply@line.me",
            body="広告アカウントが承認されました",
            rules={},
        )
        self.assertEqual(category, "archive")

    def test_line_budget_notice_stays_archive_even_with_project_context(self):
        project_hints = self.mod.build_context_project_hints(
            {
                "projects": [
                    {
                        "project": "広告運用",
                        "client_name": "株式会社TOKIWAGI",
                        "aliases": ["LINE広告"],
                        "related_entities": [],
                    }
                ]
            }
        )
        category, tags, needs_reply, rule, meta = self.mod.classify_message(
            subject="キャンペーンの予算が消化されました",
            sender="LINE Ads Platform <no-reply@line.me>",
            body="株式会社TOKIWAGI の広告キャンペーンの予算が消化されました。",
            rules={},
            context_project_hints=project_hints,
        )
        self.assertEqual(category, "archive")
        self.assertFalse(needs_reply)
        self.assertTrue(meta.get("suppress_project_override"))
        self.assertNotIn("context:project", tags)

    def test_bornelund_asobi_promo_sender_stays_archive_with_project_context(self):
        project_hints = self.mod.build_context_project_hints(
            {
                "projects": [
                    {
                        "project": "ボーネルンド / スマレジ",
                        "client_name": "株式会社ボーネルンド",
                        "aliases": ["ボーネルンド"],
                        "related_entities": ["あそび場"],
                    }
                ]
            }
        )
        category, tags, needs_reply, rule, meta = self.mod.classify_message(
            subject="大型連休におすすめ！PLAYFUL WEEKイベント開催＆おすすめあそび場のおしらせ",
            sender='"ボーネルンドあそび場" <asobi-yoyaku@bornelund.co.jp>',
            body="ボーネルンドのあそび場イベントをご案内します。",
            rules={},
            context_project_hints=project_hints,
        )
        self.assertEqual(category, "archive")
        self.assertEqual(rule, "bornelund_asobi_promo_archive")
        self.assertFalse(needs_reply)
        self.assertTrue(meta.get("suppress_project_override"))
        self.assertNotIn("context:project", tags)

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

    def test_context_project_hint_does_not_promote_project_related_promo(self):
        project_hints = self.mod.build_context_project_hints(
            {
                "projects": [
                    {
                        "project": "ボーネルンド",
                        "client_name": "株式会社ボーネルンド",
                        "aliases": ["Bornelund"],
                        "related_entities": ["KIDKID", "キドキド"],
                    }
                ]
            }
        )
        category, tags, needs_reply, _rule, meta = self.mod.classify_message(
            subject="無料セミナーのご案内",
            sender="Marketing Team <promo@mapbox.com>",
            body="株式会社ボーネルンドの予約運用に使える最新事例をご紹介します。",
            rules={},
            context_project_hints=project_hints,
        )
        self.assertEqual(category, "archive")
        self.assertFalse(needs_reply)
        self.assertTrue(meta["signals"]["context_project_match"])
        self.assertIn("context:project", tags)
        self.assertEqual(meta.get("project_reason"), "context_project_suppressed_for_promo")

    def test_creately_discount_promo_stays_archive(self):
        category, tags, needs_reply, _rule, meta = self.mod.classify_message(
            subject="30% off to come back - this week only",
            sender="Creately <marketing@creately.com>",
            body="A limited discount to reactivate your workspace.",
            rules={},
        )
        bucket, reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(category, "archive")
        self.assertEqual(bucket, "archive")
        self.assertEqual(reason, "newsletter_low_value")
        self.assertFalse(needs_reply)

    def test_ambassador_newsletter_stays_archive(self):
        category, tags, needs_reply, rule, meta = self.mod.classify_message(
            subject="【サクミルアンバサダー通信 Vol.11】発注・仕入・支払管理機能リリース予定のご共有",
            sender="根本 直樹 <info@example.com>",
            body="アンバサダー向けに新機能をご紹介します。",
            rules={},
        )
        bucket, reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(category, "archive")
        self.assertEqual(rule, "ambassador_newsletter_archive")
        self.assertEqual(bucket, "archive")
        self.assertEqual(reason, "promo_or_low_value")
        self.assertFalse(needs_reply)

    def test_calendar_invite_notice_stays_review_not_task(self):
        category, tags, needs_reply, _rule, meta = self.mod.classify_message(
            subject="招待: 事前review（LINE/YAHOO）",
            sender="Google Calendar <calendar-notification@google.com>",
            body="会議への招待です。参加可否をご確認ください。",
            rules={},
        )
        bucket, reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        final_bucket, gate_reason, gated_meta = self.mod.decide_task_gate(
            category,
            bucket,
            [{"title": "依頼内容を確認して対応する", "task_kind": "action", "note": "", "due_date": "", "project": "email"}],
            meta,
            tags,
        )
        self.assertEqual(category, "needs_review")
        self.assertFalse(needs_reply)
        self.assertEqual(bucket, "review")
        self.assertEqual(reason, "review_only_notice")
        self.assertEqual(final_bucket, "review")
        self.assertEqual(gate_reason, "task_gate_not_applicable")
        self.assertTrue(gated_meta["signals"]["review_only_notice"])

    def test_tripla_campaign_reply_request_stays_archive(self):
        category, tags, needs_reply, _rule, meta = self.mod.classify_message(
            subject="【重要】キャンセル保険ウィジェット 一括設置のお知らせ",
            sender="tripla株式会社 カスタマーサクセスチーム <cs@tripla.ai>",
            body="不要な場合は5/25までにご回答ください。キャンセル保険のご案内です。",
            rules={},
        )
        bucket, reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(category, "archive")
        self.assertFalse(needs_reply)
        self.assertEqual(bucket, "archive")
        self.assertEqual(reason, "newsletter_low_value")

    def test_work_bucket_labels_use_requested_japanese_terms(self):
        self.assertEqual(self.mod.WORK_BUCKET_LABELS["archive"], "一括保管")
        self.assertEqual(self.mod.WORK_BUCKET_LABELS["digest"], "後で読む")
        self.assertEqual(self.mod.WORK_BUCKET_LABELS["review"], "要確認")
        self.assertEqual(self.mod.WORK_BUCKET_LABELS["task"], "要確認")

    def test_label_changes_for_work_bucket_archives_digest(self):
        add, remove = self.mod.label_changes_for_work_bucket("digest", archive_digest=True)
        self.assertEqual(add, ["後で読む"])
        self.assertIn("INBOX", remove)
        self.assertIn("要確認", remove)
        self.assertIn("一括保管", remove)

    def test_label_changes_for_task_uses_review_label(self):
        add, remove = self.mod.label_changes_for_work_bucket("task", archive_digest=True)
        self.assertEqual(add, ["要確認"])
        self.assertIn("後で読む", remove)
        self.assertIn("一括保管", remove)
        self.assertNotIn("INBOX", remove)

    def test_reply_review_is_limited_to_human_task_mail(self):
        self.assertTrue(
            self.mod.should_propose_reply_review(
                "task",
                "needs_reply",
                True,
                {"signals": {"is_noreply": False}},
                [],
                "Client <client@example.com>",
            )
        )

    def test_order_confirmation_and_schedule_candidates_becomes_reply_review_task(self):
        category, tags, needs_reply, _rule, meta = self.mod.classify_message(
            subject="発注書のご確認",
            sender="佐田峰 <nigoshu@gmail.com>",
            body=(
                "先日お送りしました発注書について受け取り確認できましたでしょうか。"
                "本件について急ぎ進めていただきたく、ご査収くださいませ。"
                "今後の進め方についてもすり合わせしたく、打ち合わせの日程候補を3つほどいただけますと幸いです。"
            ),
            rules={},
        )
        bucket, _reason = self.mod.decide_work_bucket(category, needs_reply, meta, tags)
        self.assertEqual(category, "needs_reply")
        self.assertTrue(needs_reply)
        self.assertEqual(bucket, "task")
        self.assertTrue(self.mod.should_propose_reply_review(bucket, category, needs_reply, meta, tags, "佐田峰 <nigoshu@gmail.com>"))
        self.assertFalse(
            self.mod.should_propose_reply_review(
                "task",
                "needs_reply",
                True,
                {"signals": {"is_noreply": True}},
                [],
                "no-reply@example.com",
            )
        )
        self.assertFalse(
            self.mod.should_propose_reply_review(
                "task",
                "needs_review",
                False,
                {"signals": {"is_noreply": False}},
                ["tool:autoro"],
                "AUTORO <noreply@autoro.io>",
            )
        )


if __name__ == "__main__":
    main()

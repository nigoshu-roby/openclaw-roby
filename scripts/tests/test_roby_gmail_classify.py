#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main


def _load_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script_path = scripts_dir / "roby_gmail_classify.py"
    spec = importlib.util.spec_from_file_location("roby_gmail_classify_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRobyGmailClassify(TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_detect_related_tools_avoids_line_inside_pipeline(self):
        related = self.mod.detect_related_tools(
            "AWS <no-reply@example.com>",
            "[AWS PIPELINE] 成功 - ETL結果",
            related_tools=["line", "aws"],
            related_domains={},
        )

        self.assertEqual(related, ["aws"])

    def test_detect_related_tools_falls_back_to_sender_domain(self):
        related = self.mod.detect_related_tools(
            "LINE Ads Platform <no-reply@line.me>",
            "キャンペーンの予算が消化されました",
            related_tools=["autoro"],
            related_domains={"line.me": "line"},
        )

        self.assertEqual(related, ["line"])

    def test_build_email_signals_marks_business_and_project_context(self):
        signals = self.mod.build_email_signals(
            "Re: 契約更新について",
            "Client <client@example.com>",
            "",
            "請求書と契約更新の確認をお願いします。",
            contact_meta={"known": True},
            matched_projects=[{"project": "A", "match_kind": "client"}],
            important_keywords=["確認"],
            alert_hints=["エラー"],
            ad_hints=["広告"],
            promo_subject_hints=["セミナー"],
            actionable_notice_hints=["契約更新"],
            business_review_keywords=["請求書", "契約"],
            promo_sender_domains=["promo.example.com"],
        )

        self.assertTrue(signals["urgent"])
        self.assertTrue(signals["actionable_notice"])
        self.assertTrue(signals["business_review"])
        self.assertTrue(signals["contract_followup_subject"])
        self.assertTrue(signals["context_project_match"])
        self.assertTrue(signals["context_project_strong"])

    def test_decide_work_bucket_scores_are_written_to_meta(self):
        meta = {
            "signals": {
                "meeting_coordination": True,
                "promo_subject": False,
            }
        }
        bucket, reason = self.mod.decide_work_bucket("needs_review", False, meta, [])

        self.assertEqual(bucket, "task")
        self.assertEqual(reason, "coordination_requires_followup")
        self.assertEqual(meta["bucket_scores"]["task"], 3)

    def test_decide_work_bucket_downgrades_promo_review_to_digest(self):
        meta = {
            "signals": {
                "promo_subject": True,
                "marketing_sender": True,
                "business_review": False,
            }
        }
        bucket, reason = self.mod.decide_work_bucket("needs_review", False, meta, [])

        self.assertEqual(bucket, "digest")
        self.assertEqual(reason, "newsletter_review_downgraded")

    def test_decide_work_bucket_does_not_promote_project_matched_promo(self):
        meta = {
            "signals": {
                "promo_subject": True,
                "marketing_sender": True,
                "business_review": False,
                "actionable_notice": False,
                "alert": False,
                "context_project_match": True,
                "context_project_strong": True,
            }
        }
        bucket, reason = self.mod.decide_work_bucket("archive", False, meta, [])

        self.assertEqual(bucket, "archive")
        self.assertEqual(reason, "newsletter_low_value")
        self.assertTrue(meta["bucket_scores"]["newsletter_low_value"])

    def test_decide_work_bucket_keeps_calendar_invite_as_review(self):
        meta = {
            "signals": {
                "meeting_coordination": True,
                "review_only_notice": True,
                "business_review": False,
                "actionable_notice": False,
                "alert": False,
            }
        }
        bucket, reason = self.mod.decide_work_bucket("needs_review", False, meta, [])

        self.assertEqual(bucket, "review")
        self.assertEqual(reason, "review_only_notice")

    def test_waiting_followup_promotes_review_to_task(self):
        meta = {
            "signals": {
                "waiting_followup": True,
                "business_review": False,
                "actionable_notice": False,
                "alert": False,
            }
        }
        bucket, reason = self.mod.decide_work_bucket("needs_review", False, meta, [])

        self.assertEqual(bucket, "task")
        self.assertEqual(reason, "coordination_requires_followup")

    def test_detect_early_archive_rule_marks_calendar_response(self):
        rule, suppress = self.mod.detect_early_archive_rule(
            "承諾: 定例ミーティング",
            "Client <client@example.com>",
            "承諾されました。",
            chatwork_mention_hints=("メンション",),
            non_actionable_subject_patterns=[],
        )

        self.assertEqual(rule, "calendar_response")
        self.assertFalse(suppress)

    def test_detect_early_archive_rule_suppresses_project_for_bornelund_asobi(self):
        rule, suppress = self.mod.detect_early_archive_rule(
            "イベント開催のお知らせ",
            '"ボーネルンドあそび場" <asobi-yoyaku@bornelund.co.jp>',
            "あそび場イベントをご案内します。",
            chatwork_mention_hints=("メンション",),
            non_actionable_subject_patterns=[],
        )

        self.assertEqual(rule, "bornelund_asobi_promo_archive")
        self.assertTrue(suppress)

    def test_detect_reply_intent_returns_reply_and_action_flags(self):
        has_reply, has_action, promo_risk = self.mod.detect_reply_intent(
            "打ち合わせの日程候補を3つほどいただけますと幸いです。",
            explicit_reply_patterns=(r"(日程候補).*?(いただけます)",),
            explicit_action_request_patterns=(r"(打ち合わせ).*?(日程候補)",),
            promo_reply_suppress_hints=("クーポン",),
        )

        self.assertTrue(has_reply)
        self.assertTrue(has_action)
        self.assertFalse(promo_risk)

    def test_apply_local_preclassify_result_promotes_archive_to_review(self):
        category, tags, meta, needs_reply = self.mod.apply_local_preclassify_result(
            "archive",
            [],
            {},
            False,
            local_category="needs_review",
            local_reason="billing notice",
            local_meta={"enabled": True, "ok": True},
            sender="Mapbox <hello@mapbox.com>",
            subject="Mapbox webinar",
            promo_sender_domains=["mapbox.com"],
            promo_subject_hints=["webinar"],
            business_review_keywords=["請求書"],
        )

        self.assertEqual(category, "needs_review")
        self.assertFalse(needs_reply)
        self.assertIn("local:override", tags)
        self.assertEqual(meta["local_reason"], "billing notice")
        self.assertEqual(meta["local_preclassify"]["reason"], "billing notice")

    def test_apply_local_preclassify_result_cannot_archive_business_subject(self):
        category, tags, meta, _needs_reply = self.mod.apply_local_preclassify_result(
            "needs_review",
            [],
            {},
            False,
            local_category="archive",
            local_reason="promo",
            local_meta={"enabled": True, "ok": True},
            sender="Mapbox <hello@mapbox.com>",
            subject="【重要】請求書のご案内",
            promo_sender_domains=["mapbox.com"],
            promo_subject_hints=[],
            business_review_keywords=["請求書"],
        )

        self.assertEqual(category, "needs_review")
        self.assertNotIn("local:override", tags)
        self.assertEqual(meta["local_preclassify"]["category"], "archive")


if __name__ == "__main__":
    main()

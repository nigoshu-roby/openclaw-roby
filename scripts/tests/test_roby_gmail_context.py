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
    script_path = scripts_dir / "roby_gmail_context.py"
    spec = importlib.util.spec_from_file_location("roby_gmail_context_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRobyGmailContext(TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_build_context_sender_hints_indexes_emails_and_domains(self):
        sender_hints, domain_hints = self.mod.build_context_sender_hints(
            {
                "email": {
                    "important_senders": [
                        {
                            "name": "飯野さん",
                            "emails": ["T-IINO@bornelund.co.jp"],
                            "domains": ["Bornelund.co.jp"],
                            "importance": "高",
                            "company": "株式会社ボーネルンド",
                            "topics": "運用調整",
                        }
                    ]
                }
            }
        )

        self.assertEqual(sender_hints["t-iino@bornelund.co.jp"]["importance"], "高")
        self.assertEqual(domain_hints["bornelund.co.jp"]["company"], "株式会社ボーネルンド")

    def test_contact_importance_uses_context_seed_for_known_contact(self):
        sender_hints, domain_hints = self.mod.build_context_sender_hints(
            {
                "email": {
                    "important_senders": [
                        {
                            "emails": ["t-iino@bornelund.co.jp"],
                            "domains": ["bornelund.co.jp"],
                            "importance": "高",
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
        self.assertEqual(meta["sender_email"], "t-iino@bornelund.co.jp")
        self.assertEqual(meta["tier"], "medium")

    def test_build_context_project_hints_deduplicates_terms(self):
        hints = self.mod.build_context_project_hints(
            {
                "projects": [
                    {
                        "project": "ボーネルンド",
                        "client_name": "株式会社ボーネルンド",
                        "aliases": ["Bornelund", "bornelund"],
                        "related_entities": ["キドキド"],
                    }
                ]
            }
        )

        terms = hints[0]["terms"]
        self.assertEqual([term["value"] for term in terms], ["ボーネルンド", "株式会社ボーネルンド", "Bornelund", "キドキド"])

    def test_match_context_projects_uses_token_boundary_for_ascii_terms(self):
        hints = self.mod.build_context_project_hints(
            {
                "projects": [
                    {
                        "project": "LINE広告",
                        "aliases": ["line"],
                    }
                ]
            }
        )

        self.assertEqual(self.mod.match_context_projects("pipeline success", "", "", "", hints), [])
        matches = self.mod.match_context_projects("LINE の広告設定", "", "", "", hints)
        self.assertEqual(matches[0]["project"], "LINE広告")
        self.assertEqual(matches[0]["match_kind"], "alias")

    def test_apply_contact_override_promotes_replied_archive(self):
        category, tags, meta = self.mod.apply_contact_override(
            "archive",
            ["tool:mapbox"],
            {},
            {"known": True, "thread_replied": True, "tier": "low"},
            is_noreply=False,
        )

        self.assertEqual(category, "needs_review")
        self.assertIn("contact:override", tags)
        self.assertEqual(meta["contact_reason"], "known_contact_promoted_from_archive")

    def test_apply_project_override_can_be_suppressed(self):
        category, tags, meta = self.mod.apply_project_override(
            "archive",
            [],
            {
                "suppress_project_override": True,
                "context_projects": [{"project": "ボーネルンド"}],
            },
        )

        self.assertEqual(category, "archive")
        self.assertNotIn("context:project", tags)
        self.assertNotIn("project_reason", meta)

    def test_apply_project_override_promotes_context_related_archive(self):
        category, tags, meta = self.mod.apply_project_override(
            "archive",
            ["context_project:ボーネルンド"],
            {"context_projects": [{"project": "ボーネルンド"}]},
        )

        self.assertEqual(category, "needs_review")
        self.assertIn("context:project", tags)
        self.assertEqual(tags.count("context_project:ボーネルンド"), 1)
        self.assertEqual(meta["project_reason"], "context_project_promoted_from_archive")

    def test_apply_project_override_keeps_promotional_project_mail_archived(self):
        category, tags, meta = self.mod.apply_project_override(
            "archive",
            ["context_project:ボーネルンド"],
            {
                "context_projects": [{"project": "ボーネルンド"}],
                "signals": {
                    "promo_subject": True,
                    "marketing_sender": True,
                    "business_review": False,
                    "actionable_notice": False,
                    "alert": False,
                },
            },
        )

        self.assertEqual(category, "archive")
        self.assertIn("context:project", tags)
        self.assertEqual(tags.count("context_project:ボーネルンド"), 1)
        self.assertEqual(meta["project_reason"], "context_project_suppressed_for_promo")


if __name__ == "__main__":
    main()

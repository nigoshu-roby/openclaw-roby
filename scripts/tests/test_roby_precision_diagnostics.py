import importlib.util
import sys
import unittest

sys.path.insert(0, "/Users/shu/OpenClaw/scripts")

spec = importlib.util.spec_from_file_location(
    "roby_precision_diagnostics", "/Users/shu/OpenClaw/scripts/roby-precision-diagnostics.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PrecisionDiagnosticsTests(unittest.TestCase):
    def test_gmail_newsletter_with_business_words_gets_refined_cause(self):
        entry = {
            "source_run_id": "roby:gmail:1",
            "title": "【アンバサダー通信】重要なお知らせと契約確認",
            "feedback_state": "bad",
            "feedback_reason_code": "newsletter_false_positive",
        }
        self.assertEqual(module.classify_refined_cause(entry), "promo_mail_with_business_words")

    def test_minutes_wrong_project_with_conflicting_terms_gets_topic_collision(self):
        entry = {
            "source_run_id": "roby:minutes:1",
            "project": "SNW様-第三者広告配信",
            "title": "777BEACONとSSBPの設定を確認する",
            "feedback_state": "bad",
            "feedback_reason_code": "wrong_project",
        }
        self.assertEqual(module.classify_refined_cause(entry), "cross_project_topic_collision")

    def test_minutes_wrong_project_child_with_conflicting_terms_gets_misnested(self):
        entry = {
            "source_run_id": "roby:minutes:1",
            "project": "ボーネルンド",
            "title": "運営会社一覧の情報提供依頼",
            "parent_origin_id": "roby:auto:parent",
            "feedback_state": "bad",
            "feedback_reason_code": "wrong_project",
        }
        self.assertEqual(module.classify_refined_cause(entry), "semantic_parent_misnested")

    def test_bw_beacon_management_suggests_line_ad_project(self):
        entry = {
            "source_run_id": "roby:minutes:1",
            "project": "ボーネルンド",
            "title": "BWのビーコン管理システムについて、提供目安を明確にして構築作業を進める",
            "parent_origin_id": "roby:auto:parent",
            "feedback_state": "bad",
            "feedback_reason_code": "wrong_project",
        }
        self.assertEqual(module.detect_meeting_term_projects(entry["title"]), ["LINE広告配信"])
        self.assertEqual(module.classify_refined_cause(entry), "semantic_parent_misnested")

    def test_build_diagnostics_creates_domain_and_recent_cohorts(self):
        entries = [
            {
                "source_run_id": "roby:gmail:1",
                "title": "【人事】回答を確認する",
                "feedback_state": "good",
                "created_at": "2026-06-10T00:00:00Z",
                "updated_at": "2026-06-10T00:00:00Z",
                "project": "email",
            },
            {
                "source_run_id": "roby:minutes:1",
                "title": "777BEACONとSSBPの設定を確認する",
                "project": "SNW様-第三者広告配信",
                "feedback_state": "bad",
                "feedback_reason_code": "wrong_project",
                "created_at": "2026-06-10T00:00:00Z",
                "updated_at": "2026-06-10T00:00:00Z",
            },
        ]
        result = module.build_diagnostics(entries, generated_at="2026-06-15T00:00:00+00:00")
        self.assertEqual(result["overall"]["reviewed"], 2)
        self.assertIn("gmail:created_last_30d", result["cohorts"])
        self.assertIn("minutes:since_2026_06_02", result["cohorts"])
        causes = result["cohorts"]["minutes:since_2026_06_02"]["top_refined_causes"]
        self.assertEqual(causes[0]["cause"], "cross_project_topic_collision")

    def test_duplicate_clusters_are_scoped_by_project(self):
        entries = [
            {
                "source_run_id": "roby:minutes:1",
                "source_doc_id": "doc1",
                "project": "ボーネルンド",
                "title": "ボーネルンド / スマレジ打ち合わせを調整する",
            },
            {
                "source_run_id": "roby:minutes:1",
                "source_doc_id": "doc1",
                "project": "ボーネルンド",
                "title": "スマレジ打ち合わせを調整してください",
            },
            {
                "source_run_id": "roby:minutes:1",
                "source_doc_id": "doc1",
                "project": "MIDジャパン-パチンコレポート",
                "title": "スマレジ打ち合わせを調整する",
            },
        ]
        clusters = module.annotate_duplicate_clusters(module.apply_annotations(entries))
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["project"], "ボーネルンド")
        self.assertEqual(clusters[0]["count"], 2)

    def test_duplicate_clusters_label_parent_group_duplicates(self):
        entries = [
            {
                "source_run_id": "roby:minutes:1",
                "source_doc_id": "doc1",
                "source_doc_title": "2026/03/10 社内定例MTG",
                "project": "ボーネルンド",
                "title": "ボーネルンド / 2026/03/10 社内定例MTG",
            },
            {
                "source_run_id": "roby:minutes:2",
                "source_doc_id": "doc1",
                "source_doc_title": "2026/03/10 社内定例MTG",
                "project": "ボーネルンド",
                "title": "ボーネルンド / 2026/03/10 社内定例MTG",
            },
        ]
        clusters = module.annotate_duplicate_clusters(module.apply_annotations(entries))
        self.assertEqual(clusters[0]["kind"], "parent_group_duplicate")

    def test_build_diagnostics_lists_semantic_parent_misnesting_candidates(self):
        entries = [
            {
                "source_run_id": "roby:minutes:1",
                "source_doc_title": "2026/06/02社内定例MTG",
                "project": "ボーネルンド",
                "title": "運営会社一覧の情報提供依頼",
                "parent_origin_id": "roby:auto:parent",
                "feedback_state": "bad",
                "feedback_reason_code": "wrong_project",
                "created_at": "2026-06-10T00:00:00Z",
                "updated_at": "2026-06-10T00:00:00Z",
            }
        ]
        result = module.build_diagnostics(entries, generated_at="2026-06-15T00:00:00+00:00")
        self.assertEqual(
            result["overall"]["top_refined_causes"][0]["cause"],
            "semantic_parent_misnested",
        )
        candidates = result["semantic_parent_misnesting_candidates"]
        self.assertEqual(candidates[0]["suggested_projects"], ["LINE広告配信"])


if __name__ == "__main__":
    unittest.main()

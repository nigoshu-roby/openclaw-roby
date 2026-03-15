#!/usr/bin/env python3
"""
Quality regression tests for minutes extraction:
- memo/noise reduction
- project inference accuracy
- stable parent/child shaping
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


def _load_minutes_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby-minutes.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_minutes_quality_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRobyMinutesQuality(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_minutes_module()

    def test_infer_primary_project_prefers_known_project_in_text(self):
        known_projects = ["TOKIWAGI_MASTER", "ボーネルンド", "瑞鳳社ーデータ分析", "BRODO"]
        text = (
            "社内定例MTG\n"
            "ボーネルンド: OBIC連携仕様の確認依頼\n"
            "次回までに見積項目を整理する。\n"
        )
        inferred = self.mod.infer_primary_project(
            text=text,
            known_projects=known_projects,
            source_title="2026/03/05 社内定例",
            fallback_project="TOKIWAGI",
        )
        self.assertEqual(inferred, "ボーネルンド")

    def test_sanitize_filters_noise_memo_lines(self):
        extracted = [
            {"title": "進捗報告", "project": "TOKIWAGI_MASTER", "assignee": "私"},
            {"title": "背景メモ", "project": "TOKIWAGI_MASTER", "assignee": "私"},
            {"title": "OBIC仕様の差分を確認して共有", "project": "ボーネルンド", "assignee": "私"},
        ]
        cleaned = self.mod.sanitize_extracted_tasks(
            extracted=extracted,
            default_project="TOKIWAGI_MASTER",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド"],
            source_title="2026/03/05 社内定例",
            max_tasks_per_doc=20,
            max_subtasks_per_parent=8,
        )
        titles = [x.get("title", "") for x in cleaned]
        self.assertIn("OBIC仕様の差分を確認して共有", titles)
        self.assertNotIn("進捗報告", titles)
        self.assertNotIn("背景メモ", titles)

    def test_single_subtask_parent_is_flattened_when_parent_is_noise(self):
        extracted = [
            {
                "title": "要確認",
                "project": "TOKIWAGI_MASTER",
                "assignee": "私",
                "subtasks": [
                    {"title": "見積項目を作成して共有", "project": "ボーネルンド", "assignee": "私"}
                ],
            }
        ]
        cleaned = self.mod.sanitize_extracted_tasks(
            extracted=extracted,
            default_project="TOKIWAGI_MASTER",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド"],
            source_title="2026/03/05 社内定例",
            max_tasks_per_doc=20,
            max_subtasks_per_parent=8,
        )
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].get("title"), "見積項目を作成して共有")
        self.assertEqual(cleaned[0].get("project"), "ボーネルンド")
        self.assertNotIn("subtasks", cleaned[0])

    def test_candidate_models_accepts_ollama_provider(self):
        env = {"MINUTES_SUMMARY_MODELS": "ollama/qwen2.5:7b,google/gemini-3-flash-preview"}
        models = self.mod._candidate_models(env, "MINUTES_SUMMARY_MODELS", [])
        self.assertIn("ollama/qwen2.5:7b", models)

    def test_local_preprocess_candidates_become_hint_tasks(self):
        local_preprocess = {
            "primary_project": "ボーネルンド",
            "action_candidates": [
                "OBIC見積項目を整理して共有する",
                "OBIC見積項目を整理して共有する",
                "進捗報告",
            ],
        }
        tasks = self.mod.tasks_from_local_preprocess(
            local_preprocess,
            default_project="TOKIWAGI_MASTER",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド"],
            max_items=8,
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["project"], "ボーネルンド")
        self.assertIn("local_preprocess.action_candidates", tasks[0]["note"])

    def test_build_neuronic_tasks_groups_flat_minutes_by_project(self):
        extracted = [
            {"title": "リンク挿入の抜け漏れがないか確認する", "project": "BT振興会-Mooovi", "assignee": "私"},
            {"title": "ログイン情報を共有する", "project": "BT振興会-Mooovi", "assignee": "私"},
            {"title": "見積項目を整理する", "project": "ボーネルンド", "assignee": "私"},
        ]
        tasks = self.mod.build_neuronic_tasks(
            extracted=extracted,
            source="gdocs",
            source_title="2026/03/10 15:06 JST に開始した会議 - Gemini によるメモ",
            source_url="https://docs.google.com/document/d/example",
            default_project="TOKIWAGI_MASTER",
            source_id="doc-example",
            run_id="roby:minutes:test",
        )
        parent_titles = [x.get("title") for x in tasks if x.get("parent_origin_id") is None]
        child_projects = [x.get("project") for x in tasks if x.get("parent_origin_id")]
        self.assertEqual(len(parent_titles), 2)
        self.assertTrue(any("BT振興会-Mooovi" in title for title in parent_titles))
        self.assertTrue(any("ボーネルンド" in title for title in parent_titles))
        self.assertEqual(child_projects.count("BT振興会-Mooovi"), 2)
        self.assertEqual(child_projects.count("ボーネルンド"), 1)

    def test_sanitize_reinfers_parent_project_from_subtasks(self):
        extracted = [
            {
                "title": "BRODO 対応タスク",
                "project": "BRODO",
                "assignee": "私",
                "subtasks": [
                    {"title": "MIDの提案において差分資料を作成する", "assignee": "私"},
                    {"title": "堀之内店へのヒアリング結果を整理する", "assignee": "私"},
                ],
            }
        ]
        cleaned = self.mod.sanitize_extracted_tasks(
            extracted=extracted,
            default_project="TOKIWAGI_MASTER",
            known_projects=["TOKIWAGI_MASTER", "BRODO", "MIDジャパン-パチンコレポート"],
            source_title="2026/03/10 社内定例MTG",
            max_tasks_per_doc=20,
            max_subtasks_per_parent=8,
        )
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["project"], "ミッド・ガーデン・ジャパン")
        self.assertTrue(cleaned[0]["title"].startswith("ミッド・ガーデン・ジャパン / "))
        self.assertTrue(all(x["project"] == "ミッド・ガーデン・ジャパン" for x in cleaned[0]["subtasks"]))

    def test_sanitize_rewrites_generic_leaf_title_from_note(self):
        extracted = [
            {
                "title": "進捗",
                "project": "ボーネルンド",
                "assignee": "私",
                "note": "ネクストアクション: 見積項目を整理して共有する",
            }
        ]
        cleaned = self.mod.sanitize_extracted_tasks(
            extracted=extracted,
            default_project="TOKIWAGI_MASTER",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド"],
            source_title="2026/03/10 社内定例MTG",
            max_tasks_per_doc=20,
            max_subtasks_per_parent=8,
        )
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["title"], "見積項目を整理して共有する")

    def test_sanitize_decomposes_multiple_action_clauses_from_note(self):
        extracted = [
            {
                "title": "対応",
                "project": "ボーネルンド",
                "assignee": "私",
                "note": (
                    "ネクストアクション:\n"
                    "見積項目を整理して共有する。\n"
                    "新しい日報テンプレートを作成する。"
                ),
            }
        ]
        cleaned = self.mod.sanitize_extracted_tasks(
            extracted=extracted,
            default_project="TOKIWAGI_MASTER",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド"],
            source_title="2026/03/10 社内定例MTG",
            max_tasks_per_doc=20,
            max_subtasks_per_parent=8,
        )
        titles = [item["title"] for item in cleaned]
        self.assertEqual(len(cleaned), 2)
        self.assertIn("見積項目を整理して共有する", titles)
        self.assertIn("新しい日報テンプレートを作成する", titles)

    def test_subtask_note_can_expand_into_multiple_actionable_subtasks(self):
        extracted = [
            {
                "title": "ボーネルンド / 2026/03/10 社内定例MTG",
                "project": "ボーネルンド",
                "assignee": "私",
                "subtasks": [
                    {
                        "title": "対応",
                        "project": "ボーネルンド",
                        "assignee": "私",
                        "note": (
                            "ネクストアクション:\n"
                            "既存の旧レジでオープンする案を推奨する。\n"
                            "新しい日報テンプレートを作成する。"
                        ),
                    }
                ],
            }
        ]
        cleaned = self.mod.sanitize_extracted_tasks(
            extracted=extracted,
            default_project="TOKIWAGI_MASTER",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド"],
            source_title="2026/03/10 社内定例MTG",
            max_tasks_per_doc=20,
            max_subtasks_per_parent=8,
        )
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(len(cleaned[0]["subtasks"]), 2)
        titles = [item["title"] for item in cleaned[0]["subtasks"]]
        self.assertIn("既存の旧レジでオープンする案を推奨する", titles)
        self.assertIn("新しい日報テンプレートを作成する", titles)

    def test_normalize_minutes_parent_title_prefixes_specific_title_with_project(self):
        title = self.mod._normalize_minutes_parent_title(
            "渋谷Billage事務所情報",
            "TOKIWAGI_MASTER",
            "渋谷Billage事務所情報",
        )
        self.assertEqual(title, "TOKIWAGI_MASTER / 渋谷Billage事務所情報")

    def test_canonical_project_display_name_normalizes_mid_variants(self):
        self.assertEqual(self.mod._canonical_project_display_name("MID"), "ミッド・ガーデン・ジャパン")
        self.assertEqual(
            self.mod._canonical_project_display_name("MIDジャパン-パチンコレポート"),
            "ミッド・ガーデン・ジャパン",
        )

    def test_normalize_google_doc_id_accepts_url_and_raw_id(self):
        raw_id = "1FkTpYX7WaywzSzJHe4odswOUL2MCDb7j7jFz8Oirx5c"
        url = f"https://docs.google.com/document/d/{raw_id}/edit?usp=sharing"
        self.assertEqual(self.mod.normalize_google_doc_id(url), raw_id)
        self.assertEqual(self.mod.normalize_google_doc_id(raw_id), raw_id)

    def test_detect_minutes_target_source_detects_notion_and_gdocs(self):
        notion_url = "https://www.notion.so/shusbrain/2026-03-10-MTG-31c35365114380aa99f845f3f1b80efc"
        gdocs_url = "https://docs.google.com/document/d/1FkTpYX7WaywzSzJHe4odswOUL2MCDb7j7jFz8Oirx5c/edit"
        self.assertEqual(self.mod.detect_minutes_target_source(notion_url, "auto"), "notion")
        self.assertEqual(self.mod.detect_minutes_target_source(gdocs_url, "auto"), "gdocs")
        self.assertEqual(self.mod.detect_minutes_target_source(gdocs_url, "gdocs"), "gdocs")

    def test_build_target_candidate_for_notion_uses_page_metadata_and_structure(self):
        page_id = "31c35365114380aa99f845f3f1b80efc"
        metadata = {
            "id": page_id,
            "last_edited_time": "2026-03-12T01:02:03.000Z",
            "url": f"https://www.notion.so/{page_id}",
            "parent": {"database_id": "7064abbbcd4640a38367b87a5b14d520"},
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"plain_text": "2026/03/10 社内定例MTG"}],
                }
            },
        }
        structure = {
            "databases": [
                {
                    "id": "7064abbbcd4640a38367b87a5b14d520",
                    "project": "TOKIWAGI_MASTER",
                    "title": "TOKIWAGIインナー議事録",
                }
            ]
        }
        with patch.object(self.mod, "fetch_notion_page_metadata", return_value=metadata):
            candidate = self.mod.build_target_candidate(
                f"https://www.notion.so/shusbrain/2026-03-10-MTG-{page_id}",
                "auto",
                {},
                "",
                "notion-token",
                "2025-09-03",
                structure=structure,
            )
        self.assertEqual(candidate["source"], "notion")
        self.assertEqual(candidate["page_id"], page_id)
        self.assertEqual(candidate["project"], "TOKIWAGI_MASTER")
        self.assertEqual(candidate["db_title"], "TOKIWAGIインナー議事録")
        self.assertEqual(candidate["title"], "2026/03/10 社内定例MTG")

    def test_build_target_candidate_for_gdocs_uses_drive_metadata(self):
        doc_id = "1FkTpYX7WaywzSzJHe4odswOUL2MCDb7j7jFz8Oirx5c"
        metadata = {
            "id": doc_id,
            "name": "2026/03/09 15:44 JST に開始した会議 - Gemini によるメモ",
            "modifiedTime": "2026-03-09T07:50:20.264Z",
        }
        with patch.object(self.mod, "fetch_drive_file_metadata", return_value=metadata):
            candidate = self.mod.build_target_candidate(
                f"https://docs.google.com/document/d/{doc_id}/edit",
                "auto",
                {},
                "s.nigo@tokiwa-gi.com",
                "",
                "2025-09-03",
                structure={},
            )
        self.assertEqual(candidate["source"], "gdocs")
        self.assertEqual(candidate["doc_id"], doc_id)
        self.assertEqual(candidate["title"], metadata["name"])
        self.assertEqual(candidate["updated"], metadata["modifiedTime"])

    def test_owner_mentions_override_default_self_assignee(self):
        extracted = [
            {
                "title": "対応",
                "project": "ボーネルンド",
                "assignee": "私",
                "note": "高田さんに確認を依頼する",
            }
        ]
        cleaned = self.mod.sanitize_extracted_tasks(
            extracted=extracted,
            default_project="TOKIWAGI_MASTER",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド"],
            source_title="2026/03/10 社内定例MTG",
            max_tasks_per_doc=20,
            max_subtasks_per_parent=8,
        )
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["assignee"], "高田")

    def test_build_neuronic_tasks_filters_non_self_assignees(self):
        extracted = [
            {"title": "私が見積項目を整理する", "project": "ボーネルンド", "assignee": "私"},
            {"title": "高田さんが顧客へ共有する", "project": "ボーネルンド", "assignee": "高田"},
        ]
        tasks = self.mod.build_neuronic_tasks(
            extracted=extracted,
            source="notion",
            source_title="2026/03/10 社内定例MTG",
            source_url="https://www.notion.so/example",
            default_project="TOKIWAGI_MASTER",
            source_id="page-example",
            run_id="roby:minutes:test",
        )
        parent_tasks = [x for x in tasks if x.get("parent_origin_id") is None]
        child_tasks = [x for x in tasks if x.get("parent_origin_id")]
        self.assertEqual(len(parent_tasks), 1)
        self.assertEqual(len(child_tasks), 1)
        self.assertEqual(child_tasks[0]["title"], "私が見積項目を整理する")
        self.assertEqual(child_tasks[0]["assignee"], "私")

    def test_build_neuronic_tasks_omits_assignee_tag_when_blank(self):
        extracted = [
            {"title": "見積項目を整理する", "project": "ボーネルンド", "assignee": ""},
        ]
        tasks = self.mod.build_neuronic_tasks(
            extracted=extracted,
            source="notion",
            source_title="2026/03/10 社内定例MTG",
            source_url="https://www.notion.so/example",
            default_project="TOKIWAGI_MASTER",
            source_id="page-example",
            run_id="roby:minutes:test",
        )
        self.assertEqual(len(tasks), 2)
        self.assertTrue(all("assignee:" not in " ".join(task.get("tags", [])) for task in tasks))

    def test_build_neuronic_tasks_keeps_confident_project_task(self):
        extracted = [
            {"title": "ボーネルンドの見積項目を整理する", "project": "ボーネルンド", "assignee": "私"},
        ]
        tasks = self.mod.build_neuronic_tasks(
            extracted=extracted,
            source="notion",
            source_title="2026/03/10 社内定例MTG",
            source_url="https://www.notion.so/example",
            default_project="TOKIWAGI_MASTER",
            source_id="page-example",
            run_id="roby:minutes:test",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド", "BRODO"],
            registry={},
        )
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["project"], "ボーネルンド")

    def test_build_neuronic_tasks_drops_weak_generic_cross_project_task(self):
        extracted = [
            {
                "title": "見積項目を整理する",
                "project": "TOKIWAGI_MASTER",
                "assignee": "私",
                "note": "review.cross_project_actions",
            },
        ]
        tasks = self.mod.build_neuronic_tasks(
            extracted=extracted,
            source="notion",
            source_title="2026/03/10 社内定例MTG",
            source_url="https://www.notion.so/example",
            default_project="TOKIWAGI_MASTER",
            source_id="page-example",
            run_id="roby:minutes:test",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド", "BRODO"],
            registry={},
        )
        self.assertEqual(tasks, [])

    def test_build_neuronic_tasks_drops_conflicting_project_task(self):
        extracted = [
            {
                "title": "BRODOの在庫整理を進める",
                "project": "ボーネルンド",
                "assignee": "私",
            },
        ]
        tasks = self.mod.build_neuronic_tasks(
            extracted=extracted,
            source="gdocs",
            source_title="2026/03/10 社内定例MTG",
            source_url="https://docs.google.com/document/d/example",
            default_project="TOKIWAGI_MASTER",
            source_id="doc-example",
            run_id="roby:minutes:test",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド", "BRODO"],
            registry={},
        )
        self.assertEqual(tasks, [])

    def test_build_neuronic_tasks_drops_task_outside_document_project_hints(self):
        extracted = [
            {
                "title": "進行表を更新する",
                "project": "BRODO",
                "assignee": "私",
            },
        ]
        tasks = self.mod.build_neuronic_tasks(
            extracted=extracted,
            source="gdocs",
            source_title="2026/03/10 社内定例MTG",
            source_url="https://docs.google.com/document/d/example",
            default_project="TOKIWAGI_MASTER",
            source_id="doc-example",
            run_id="roby:minutes:test",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド", "BRODO"],
            doc_project_hints=["ボーネルンド"],
            registry={},
        )
        self.assertEqual(tasks, [])

    def test_build_neuronic_tasks_keeps_task_when_document_project_hints_include_target(self):
        extracted = [
            {
                "title": "BRODOの在庫整理を進める",
                "project": "BRODO",
                "assignee": "私",
            },
        ]
        tasks = self.mod.build_neuronic_tasks(
            extracted=extracted,
            source="gdocs",
            source_title="2026/03/10 社内定例MTG",
            source_url="https://docs.google.com/document/d/example",
            default_project="TOKIWAGI_MASTER",
            source_id="doc-example",
            run_id="roby:minutes:test",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド", "BRODO"],
            doc_project_hints=["ボーネルンド", "BRODO"],
            registry={},
        )
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["project"], "BRODO")

    def test_build_neuronic_tasks_keeps_only_confident_children(self):
        extracted = [
            {
                "title": "ボーネルンド / 2026/03/10 社内定例MTG",
                "project": "ボーネルンド",
                "assignee": "私",
                "subtasks": [
                    {
                        "title": "ボーネルンドの見積項目を整理する",
                        "project": "ボーネルンド",
                        "assignee": "私",
                    },
                    {
                        "title": "BRODOの在庫整理を進める",
                        "project": "ボーネルンド",
                        "assignee": "私",
                    },
                    {
                        "title": "議事録を見直す",
                        "project": "TOKIWAGI_MASTER",
                        "assignee": "私",
                        "note": "review.cross_project_actions",
                    },
                ],
            }
        ]
        tasks = self.mod.build_neuronic_tasks(
            extracted=extracted,
            source="notion",
            source_title="2026/03/10 社内定例MTG",
            source_url="https://www.notion.so/example",
            default_project="TOKIWAGI_MASTER",
            source_id="page-example",
            run_id="roby:minutes:test",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド", "BRODO"],
            registry={},
        )
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["title"], "ボーネルンド / 2026/03/10 社内定例MTG")
        self.assertEqual(tasks[1]["title"], "ボーネルンドの見積項目を整理する")

    def test_registry_aliases_extend_project_matching(self):
        registry = {
            "project_registry": [
                {
                    "project": "ボーネルンド",
                    "aliases": ["BONELAND"],
                    "top_owners": [{"value": "飯野", "count": 2}],
                    "top_action_patterns": [{"value": "資料作成", "count": 3}],
                    "local_llm": {"aliases": ["BORNELAND"], "owner_hints": ["飯野さん"]},
                }
            ]
        }
        self.mod.apply_tokiwagi_master_registry(registry)
        matched = self.mod._match_known_project_name("BONELAND案件", ["ボーネルンド"])
        self.assertEqual(matched, "ボーネルンド")
        self.assertIn("飯野", self.mod.PROJECT_OWNER_HINTS_REGISTRY["ボーネルンド"])

    def test_segment_minutes_text_marks_multiple_projects(self):
        registry = {
            "project_registry": [
                {"project": "ボーネルンド", "aliases": ["BONELAND"]},
                {"project": "BRODO", "aliases": ["BRODO"]},
            ]
        }
        self.mod.apply_tokiwagi_master_registry(registry)
        text = (
            "ボーネルンド\n"
            "見積項目を整理して共有する\n"
            "BRODO\n"
            "在庫整理の手順を確認する\n"
        )
        segmented, meta = self.mod.segment_minutes_text(
            text,
            default_project="TOKIWAGI_MASTER",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド", "BRODO"],
            source_title="2026/03/10 社内定例MTG",
        )
        self.assertTrue(meta["segmented"])
        self.assertIn("[Project: ボーネルンド]", segmented)
        self.assertIn("[Project: BRODO]", segmented)
        self.assertIn("ボーネルンド", meta["project_hints"])
        self.assertIn("BRODO", meta["project_hints"])

    def test_normalize_owner_hint_candidate_filters_noise(self):
        self.assertEqual(self.mod._normalize_owner_hint_candidate("高田さん"), "高田")
        self.assertEqual(self.mod._normalize_owner_hint_candidate("AI"), "")
        self.assertEqual(self.mod._normalize_owner_hint_candidate("一広"), "")

    def test_apply_context_seed_data_merges_project_alias_owner_and_self_aliases(self):
        self.mod.PROJECT_ALIAS_REGISTRY.clear()
        self.mod.PROJECT_EXTRA_ALIASES.clear()
        self.mod.PROJECT_OWNER_HINTS_REGISTRY.clear()
        self.mod.PROJECT_ACTION_HINTS_REGISTRY.clear()
        self.mod.CONTEXT_SELF_OWNER_ALIASES = []
        self.mod.apply_context_seed_data(
            {
                "role": {"self_aliases": ["にーご"]},
                "owner_rules": {"self_aliases": ["新後"]},
                "projects": [
                    {
                        "project": "ボーネルンド",
                        "aliases": ["Bornelund"],
                        "owner_hints": ["飯野さん", "早川さん"],
                        "action_hints": ["資料作成"],
                    }
                ],
            }
        )
        self.assertEqual(self.mod.PROJECT_ALIAS_REGISTRY.get(self.mod._normalize_project_token("Bornelund")), "ボーネルンド")
        self.assertIn("飯野", self.mod.PROJECT_OWNER_HINTS_REGISTRY.get("ボーネルンド", []))
        self.assertIn("資料作成", self.mod.PROJECT_ACTION_HINTS_REGISTRY.get("ボーネルンド", []))
        aliases = self.mod._get_self_owner_aliases({"ROBY_MINUTES_SELF_ALIASES": ""})
        self.assertIn("にーご", aliases)
        self.assertIn("新後", aliases)

    def test_run_with_doc_timeout_returns_function_result_when_alarm_is_stubbed(self):
        with patch.object(self.mod.signal, "signal"), patch.object(self.mod.signal, "setitimer"):
            result = self.mod.run_with_doc_timeout(1, lambda x: x + 1, 2)
        self.assertEqual(result, 3)


if __name__ == "__main__":
    main()

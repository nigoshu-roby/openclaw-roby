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
        self.assertEqual(cleaned[0]["project"], "MIDジャパン-パチンコレポート")
        self.assertTrue(cleaned[0]["title"].startswith("MIDジャパン-パチンコレポート / "))
        self.assertTrue(all(x["project"] == "MIDジャパン-パチンコレポート" for x in cleaned[0]["subtasks"]))

    def test_normalize_minutes_parent_title_prefixes_specific_title_with_project(self):
        title = self.mod._normalize_minutes_parent_title(
            "渋谷Billage事務所情報",
            "TOKIWAGI_MASTER",
            "渋谷Billage事務所情報",
        )
        self.assertEqual(title, "TOKIWAGI_MASTER / 渋谷Billage事務所情報")

    def test_run_with_doc_timeout_returns_function_result_when_alarm_is_stubbed(self):
        with patch.object(self.mod.signal, "signal"), patch.object(self.mod.signal, "setitimer"):
            result = self.mod.run_with_doc_timeout(1, lambda x: x + 1, 2)
        self.assertEqual(result, 3)


if __name__ == "__main__":
    main()

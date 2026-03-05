#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main


def _load_orchestrator_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby-orchestrator.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_orchestrator_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WRAPPED_MESSAGE = """[直近会話コンテキスト]
あなた: robyとしての機能を確認してリスト化してください。
Roby: Ollama APIは接続OKです。

[ユーザーの最新依頼]
Neuronicへのタスク登録をお願いします。
「ボーネルンドスマレジテスト」という大タスクの配下に、下記タスクを配列してください。
入れ子状態を保ってタスク登録を実施してください。
■フェーズ1：スマレジ環境の基本設定
◆タスクカテゴリー：店舗・端末の基盤構築
・大タスク：テスト店舗の設定
- 小タスク：店舗情報の登録
* 小小タスク：スマレジ管理画面（Web）にてテスト店舗名・基本情報を入力する
"""

FULL_REGISTER_MESSAGE_WITH_DRILL_WORD = """Neuronicへのタスク登録をお願いします。
「ボーネルンドスマレジテスト」という大タスクの配下に、下記タスクを配列してください。
■フェーズ4：運用テストと業務フローの確立
◆タスクカテゴリー：現場運用に向けた検証
・大タスク：機器・連携テスト
- 小タスク：バーコードリーダー等の動作確認
"""


class TestRobyOrchestratorRouting(TestCase):
    def setUp(self):
        self.mod = _load_orchestrator_module()

    def test_extract_latest_user_request_ignores_prior_context(self):
        latest = self.mod.extract_latest_user_request(WRAPPED_MESSAGE)
        self.assertIn("Neuronicへのタスク登録をお願いします。", latest)
        self.assertNotIn("Ollama APIは接続OKです。", latest)

    def test_classify_direct_register_to_minutes_pipeline(self):
        route = self.mod.classify_intent_heuristic(WRAPPED_MESSAGE)
        self.assertEqual(route, self.mod.ROUTE_MINUTES)

    def test_classify_direct_register_prioritized_over_drill_keywords(self):
        route = self.mod.classify_intent_heuristic(FULL_REGISTER_MESSAGE_WITH_DRILL_WORD)
        self.assertEqual(route, self.mod.ROUTE_MINUTES)

    def test_self_status_detection_not_triggered_by_wrapped_context(self):
        self.assertFalse(self.mod.is_self_status_request(WRAPPED_MESSAGE))

    def test_build_direct_neuronic_tasks_keeps_hierarchy(self):
        tasks, meta = self.mod._build_direct_neuronic_tasks(WRAPPED_MESSAGE)
        self.assertGreaterEqual(len(tasks), 5)
        self.assertEqual(tasks[0]["title"], "ボーネルンドスマレジテスト")
        phase = tasks[1]
        category = tasks[2]
        major = tasks[3]
        minor = tasks[4]
        self.assertEqual(phase["parent_origin_id"], tasks[0]["origin_id"])
        self.assertEqual(category["parent_origin_id"], phase["origin_id"])
        self.assertEqual(major["parent_origin_id"], category["origin_id"])
        self.assertEqual(minor["parent_origin_id"], major["origin_id"])
        self.assertEqual(meta.get("root_title"), "ボーネルンドスマレジテスト")

    def test_handle_neuronic_direct_register_dryrun(self):
        result = self.mod.handle_neuronic_direct_register(WRAPPED_MESSAGE, env={}, execute=False)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("mode"), "direct_register")
        self.assertGreater(int(result.get("task_count", 0)), 1)

    def test_is_direct_register_managed_task(self):
        task = {
            "id": "T1",
            "source": "roby",
            "external_ref": "roby:chat",
            "source_doc_title": "ボーネルンドスマレジテスト",
        }
        self.assertTrue(self.mod._is_direct_register_managed_task(task, "ボーネルンドスマレジテスト"))
        self.assertFalse(self.mod._is_direct_register_managed_task(task, "別タイトル"))


if __name__ == "__main__":
    main()

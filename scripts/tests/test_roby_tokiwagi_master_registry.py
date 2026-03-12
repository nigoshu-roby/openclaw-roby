#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main


def _load_registry_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby-tokiwagi-master-registry.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_tokiwagi_registry_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_minutes_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby-minutes.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_minutes_for_registry_tests", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRobyTokiwagiMasterRegistry(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_registry_module()
        cls.minutes = _load_minutes_module()

    def test_extract_project_sections_groups_lines_under_headings(self):
        text = (
            "ボーネルンド\n"
            "OBICの見積項目を確認する\n"
            "スマレジの日報フォーマットを整理する\n"
            "瑞鳳社ーデータ分析\n"
            "Mapboxの表示を確認する\n"
        )
        sections = self.mod.extract_project_sections(
            text,
            default_project="TOKIWAGI_MASTER",
            known_projects=["TOKIWAGI_MASTER", "ボーネルンド", "瑞鳳社ーデータ分析"],
            source_title="2026/03/10 社内定例MTG",
            mod=self.minutes,
        )
        self.assertIn("ボーネルンド", sections)
        self.assertIn("瑞鳳社ーデータ分析", sections)
        self.assertGreaterEqual(sections["ボーネルンド"]["action_count"], 2)

    def test_extract_owner_mentions(self):
        owners = self.mod.extract_owner_mentions("高田さんに確認し、飯海氏へ共有する")
        self.assertIn("高田", owners)
        self.assertIn("飯海", owners)

    def test_classify_action_patterns(self):
        labels = self.mod.classify_action_patterns("見積書を作成して共有し、日程を調整する")
        self.assertIn("資料作成", labels)
        self.assertIn("会議調整", labels)
        self.assertIn("連携・共有", labels)


if __name__ == "__main__":
    main()

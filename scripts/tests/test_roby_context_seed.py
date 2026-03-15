#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main


def _load_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby_context_seed.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_context_seed_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRobyContextSeed(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_parse_context_seed_extracts_projects_and_sender_emails(self):
        text = """
## 1. 自分の役割
- 表示名の揺れ:
  - 例: `私`, `にーご`

## 2. Project / Client テンプレート
### Project
- 正式名: ボーネルンド
- 略称 / 別名: ボーネルンド, Bornelund
- 関係者:
  - クライアント担当者: 飯野さん、早川さん
  - 社内担当者: 高田さん
- よくある作業:
  - 資料作成
  - 会議調整
- task にしやすいもの: 資料修正、日程調整
- task にしなくてよいもの: 雑談、背景共有だけの話

## 3. Owner / 担当者ルール
- 自分扱いにしてよい表現:
  - 例: `私`, `新後`
- 他担当としてよく出る人:
  - 名前: `高田`, `清`

## 4. Email 判断ルール
### 4.1 重要な送信者 / 宛先
- よくやり取りする相手:
  - 名前: 飯野さん
  - メール: t-iino@bornelund.co.jp
  - 会社: 株式会社ボーネルンド
  - 重要度: 高
  - どういう内容が多いか: 運用調整
"""
        parsed = self.mod.parse_context_seed(text)
        self.assertIn("にーご", parsed["role"]["self_aliases"])
        self.assertIn("高田", parsed["owner_rules"]["other_owner_names"])
        self.assertEqual(parsed["projects"][0]["project"], "ボーネルンド")
        self.assertIn("Bornelund", parsed["projects"][0]["aliases"])
        self.assertIn("飯野", parsed["projects"][0]["owner_hints"])
        self.assertIn("会議調整", parsed["projects"][0]["action_hints"])
        self.assertIn("資料修正", parsed["projects"][0]["positive_task_hints"])
        self.assertIn("背景共有だけの話", parsed["projects"][0]["negative_task_hints"])
        self.assertEqual(parsed["email"]["important_senders"][0]["emails"], ["t-iino@bornelund.co.jp"])


if __name__ == "__main__":
    main()

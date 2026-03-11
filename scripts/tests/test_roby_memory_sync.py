#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


def _load_memory_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby-memory-sync.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_memory_sync_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRobyMemorySync(TestCase):
    def setUp(self):
        self.mod = _load_memory_module()

    def test_replace_or_append_block_appends_when_markers_missing(self):
        text = "# MEMORY\n"
        updated = self.mod.replace_or_append_block(text, self.mod.MEMORY_START, self.mod.MEMORY_END, "- snapshot")
        self.assertIn(self.mod.MEMORY_START, updated)
        self.assertIn("- snapshot", updated)
        self.assertIn(self.mod.MEMORY_END, updated)

    def test_build_snapshot_marks_attention_when_eval_or_stale_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weekly = root / "weekly.json"
            feedback = root / "feedback.json"
            eval_latest = root / "eval.json"
            drill_latest = root / "drill.json"

            weekly.write_text(
                json.dumps(
                    {
                        "generated_at": "2000-03-12T10:00:00+09:00",
                        "eval": {"failed_runs": 2},
                        "drill": {"failed_runs": 0},
                        "freshness": {"stale_components": ["minutes_sync"]},
                        "audit": {"errors": 1},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            feedback.write_text(
                json.dumps(
                    {
                        "updated_at": "2026-03-12T10:05:00+09:00",
                        "summary": {
                            "reviewed_count": 5,
                            "actionable_count": 2,
                            "counts": {"good": 2, "bad": 1, "missed": 1, "pending": 1},
                            "actionable_reason_counts": {
                                "newsletter_false_positive": 1
                            },
                            "improvement_targets": [
                                {
                                    "target": "gmail_promo_filtering",
                                    "label": "メルマガ判定",
                                    "count": 3,
                                    "recommendation": "archive閾値を見直す",
                                }
                            ],
                            "recent_actionable": [
                                {
                                    "title": "メール確認: サンプル",
                                    "feedback_state": "bad",
                                    "feedback_reason_code": "newsletter_false_positive",
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            eval_latest.write_text(
                json.dumps({"all_ok": False, "failed": 1, "total": 7}, ensure_ascii=False),
                encoding="utf-8",
            )
            drill_latest.write_text(
                json.dumps({"all_ok": True, "failed": 0, "total": 4}, ensure_ascii=False),
                encoding="utf-8",
            )

            with (
                patch.object(self.mod, "WEEKLY_LATEST", weekly),
                patch.object(self.mod, "FEEDBACK_LATEST", feedback),
                patch.object(self.mod, "EVAL_LATEST", eval_latest),
                patch.object(self.mod, "DRILL_LATEST", drill_latest),
                patch.object(
                    self.mod,
                    "LIVE_FRESHNESS_TARGETS",
                    [
                        {
                            "name": "minutes_sync",
                            "type": "json",
                            "path": weekly,
                            "max_minutes_env": "ROBY_DRILL_MINUTES_MAX_MIN",
                            "default": 240,
                        }
                    ],
                ),
            ):
                snapshot = self.mod.build_snapshot()

            self.assertEqual(snapshot["heartbeat_status"], "HEARTBEAT_ATTENTION")
            self.assertIn("Evaluation Harness fail 1/7", snapshot["unresolved"])
            self.assertIn("stale component: minutes_sync", snapshot["unresolved"])
            self.assertEqual(snapshot["counts"]["bad"], 1)
            self.assertEqual(snapshot["top_targets"][0]["label"], "メルマガ判定")
            self.assertTrue(snapshot["sources"]["weekly"]["present"])
            self.assertFalse(snapshot["quality"]["evaluation"]["all_ok"])
            self.assertEqual(snapshot["feedback_reason_counts"][0]["reason_code"], "newsletter_false_positive")

    def test_render_blocks_include_structured_sections(self):
        snapshot = {
            "updated_at": "2026-03-12T10:00:00+09:00",
            "heartbeat_status": "HEARTBEAT_OK",
            "unresolved": [],
            "sources": {
                "weekly": {"present": True, "updated_at": "2026-03-12T09:00:00+09:00"},
                "feedback": {"present": True, "updated_at": "2026-03-12T09:05:00+09:00"},
                "evaluation": {"present": True, "updated_at": "2026-03-12T09:10:00+09:00"},
                "drill": {"present": True, "updated_at": "2026-03-12T09:12:00+09:00"},
            },
            "quality": {
                "evaluation": {"all_ok": True, "failed": 0, "total": 7},
                "drill": {"all_ok": True, "failed": 0, "total": 4},
                "audit_errors_7d": 0,
                "stale_components": [],
            },
            "stale_components": [],
            "eval_failed_runs_7d": 0,
            "drill_failed_runs_7d": 0,
            "audit_errors_7d": 0,
            "reviewed_count": 10,
            "actionable_count": 2,
            "counts": {"good": 5, "bad": 2, "missed": 0, "pending": 3},
            "feedback_reason_counts": [{"reason_code": "not_actionable", "count": 2}],
            "top_targets": [
                {
                    "target": "task_filtering",
                    "label": "タスク抽出閾値",
                    "count": 2,
                    "recommendation": "弱い文を除外する。",
                }
            ],
            "recent_actionable": [
                {
                    "title": "メール確認: テスト",
                    "feedback_state": "bad",
                    "feedback_reason_code": "not_actionable",
                }
            ],
        }

        memory_block = self.mod.render_memory_block(snapshot)
        heartbeat_block = self.mod.render_heartbeat_block(snapshot)

        self.assertIn("### 現在の運用状態", memory_block)
        self.assertIn("### 監視ソース", memory_block)
        self.assertIn("### 品質ゲート", memory_block)
        self.assertIn("### フィードバック要約", memory_block)
        self.assertIn("### 判定", heartbeat_block)
        self.assertIn("### いま見るべき運用信号", heartbeat_block)
        self.assertIn("### 次に見るべき改善対象", heartbeat_block)


    def test_compute_live_stale_components_uses_jsonl_seconds_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            growth = root / "self_growth_runs.jsonl"
            weekly = root / "weekly.json"
            growth.write_text(json.dumps({"ts": 4102444800}) + "\n", encoding="utf-8")
            weekly.write_text(json.dumps({"generated_at": "2100-01-01T00:00:00+00:00"}), encoding="utf-8")

            with patch.object(
                self.mod,
                "LIVE_FRESHNESS_TARGETS",
                [
                    {
                        "name": "self_growth",
                        "type": "jsonl",
                        "path": growth,
                        "max_minutes_env": "ROBY_DRILL_SELF_GROWTH_MAX_MIN",
                        "default": 180,
                    },
                    {
                        "name": "weekly_report",
                        "type": "json",
                        "path": weekly,
                        "max_minutes_env": "ROBY_DRILL_WEEKLY_MAX_MIN",
                        "default": 10080,
                    },
                ],
            ):
                stale = self.mod.compute_live_stale_components()

            self.assertEqual(stale, [])

    def test_run_writes_memory_heartbeat_daily_note_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_file = root / "MEMORY.md"
            heartbeat_file = root / "HEARTBEAT.md"
            daily_dir = root / "memory"
            state_path = root / "memory_sync_state.json"
            run_log = root / "memory_sync_runs.jsonl"
            weekly = root / "weekly.json"
            feedback = root / "feedback.json"
            eval_latest = root / "eval.json"
            drill_latest = root / "drill.json"

            memory_file.write_text("# MEMORY\n", encoding="utf-8")
            heartbeat_file.write_text("# HEARTBEAT\n", encoding="utf-8")
            weekly.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-03-12T10:00:00+09:00",
                        "eval": {"failed_runs": 0},
                        "drill": {"failed_runs": 0},
                        "freshness": {"stale_components": []},
                        "audit": {"errors": 0},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            feedback.write_text(
                json.dumps(
                    {
                        "updated_at": "2026-03-12T10:05:00+09:00",
                        "summary": {
                            "reviewed_count": 1,
                            "actionable_count": 0,
                            "counts": {"good": 1, "bad": 0, "missed": 0, "pending": 0},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            eval_latest.write_text(json.dumps({"all_ok": True, "failed": 0, "total": 7}), encoding="utf-8")
            drill_latest.write_text(json.dumps({"all_ok": True, "failed": 0, "total": 4}), encoding="utf-8")

            with (
                patch.object(self.mod, "MEMORY_FILE", memory_file),
                patch.object(self.mod, "HEARTBEAT_FILE", heartbeat_file),
                patch.object(self.mod, "DAILY_MEMORY_DIR", daily_dir),
                patch.object(self.mod, "STATE_PATH", state_path),
                patch.object(self.mod, "RUN_LOG_PATH", run_log),
                patch.object(self.mod, "WEEKLY_LATEST", weekly),
                patch.object(self.mod, "FEEDBACK_LATEST", feedback),
                patch.object(self.mod, "EVAL_LATEST", eval_latest),
                patch.object(self.mod, "DRILL_LATEST", drill_latest),
                patch.object(self.mod, "compute_live_stale_components", return_value=[]),
                patch.object(self.mod, "append_audit_event"),
            ):
                result = self.mod.run(dry_run=False)

            self.assertEqual(result["heartbeat_status"], "HEARTBEAT_OK")
            self.assertTrue(state_path.exists())
            self.assertTrue(run_log.exists())
            self.assertIn(self.mod.MEMORY_START, memory_file.read_text(encoding="utf-8"))
            self.assertIn(self.mod.HEARTBEAT_START, heartbeat_file.read_text(encoding="utf-8"))
            day_notes = list(daily_dir.glob("*.md"))
            self.assertEqual(len(day_notes), 1)
            self.assertIn("PBS Ops Memory", day_notes[0].read_text(encoding="utf-8"))
            self.assertIn("sources", result)
            self.assertIn("quality", result)
            self.assertIn("feedback_reason_counts", result)


if __name__ == "__main__":
    main()

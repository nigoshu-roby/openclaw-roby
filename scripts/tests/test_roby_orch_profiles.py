#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase, main


def _load_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script_path = scripts_dir / "roby_orch_profiles.py"
    spec = importlib.util.spec_from_file_location("roby_orch_profiles_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRobyOrchProfiles(TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_resolve_schedule_uses_night_window(self):
        schedule = self.mod.resolve_local_first_schedule(
            {
                "ROBY_ORCH_LOCAL_FIRST_SCHEDULE": "1",
                "ROBY_ORCH_LOCAL_FIRST_TZ": "Asia/Tokyo",
                "ROBY_ORCH_LOCAL_FIRST_DAY_START": "08:00",
                "ROBY_ORCH_LOCAL_FIRST_DAY_END": "20:00",
                "ROBY_ORCH_MINUTES_PROFILE_DAY": "hybrid",
                "ROBY_ORCH_MINUTES_PROFILE_NIGHT": "local",
            },
            route="MINUTES",
            base_profile_key="ROBY_ORCH_MINUTES_LLM_PROFILE",
            default_profile="hybrid",
            default_day_profile="hybrid",
            default_night_profile="local",
            now=datetime(2026, 3, 12, 14, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(schedule["window"], "night")
        self.assertEqual(schedule["effective_profile"], "local")

    def test_minutes_hybrid_profile_prefers_fast_local_preprocess(self):
        profile, overrides = self.mod.apply_minutes_llm_profile(
            {
                "ROBY_ORCH_LOCAL_FIRST_SCHEDULE": "0",
                "ROBY_ORCH_MINUTES_LLM_PROFILE": "hybrid",
                "ROBY_ORCH_MINUTES_LOCAL_FAST_MODEL": "ollama/llama3.2:3b",
                "ROBY_ORCH_MINUTES_LOCAL_QUALITY_MODEL": "ollama/qwen2.5:7b",
                "ROBY_ORCH_MINUTES_CLOUD_MODEL": "google/gemini-3-flash-preview",
            }
        )

        self.assertEqual(profile, "hybrid")
        self.assertEqual(overrides["MINUTES_LOCAL_PREPROCESS_MODEL"], "ollama/llama3.2:3b")
        self.assertEqual(
            overrides["MINUTES_REVIEW_MODELS"],
            "google/gemini-3-flash-preview,ollama/qwen2.5:7b,ollama/llama3.2:3b",
        )

    def test_gmail_fast_profile_disables_llm_review(self):
        profile, overrides = self.mod.apply_gmail_profile(
            {
                "ROBY_ORCH_LOCAL_FIRST_SCHEDULE": "0",
                "ROBY_ORCH_GMAIL_PROFILE": "fast",
                "ROBY_ORCH_GMAIL_LLM_FAST_MODEL": "ollama/llama3.2:3b",
                "ROBY_ORCH_GMAIL_LLM_QUALITY_MODEL": "ollama/qwen2.5:7b",
            }
        )

        self.assertEqual(profile, "fast")
        self.assertEqual(overrides["GMAIL_TRIAGE_LLM_ENABLE"], "0")
        self.assertEqual(overrides["GMAIL_TRIAGE_SEMANTIC_TRIAGE_ENABLE"], "1")
        self.assertEqual(overrides["GMAIL_TRIAGE_SEMANTIC_TRIAGE_MODEL"], "google/gemini-3-flash-preview")
        self.assertEqual(overrides["GMAIL_TRIAGE_SEMANTIC_TRIAGE_MAX_PER_RUN"], "8")
        self.assertEqual(overrides["GMAIL_TRIAGE_TASK_LLM_MODEL"], "google/gemini-3-flash-preview")
        self.assertEqual(overrides["GMAIL_TRIAGE_LLM_MAX_REVIEWS"], "0")

    def test_gmail_quality_profile_enables_limited_llm_review(self):
        profile, overrides = self.mod.apply_gmail_profile(
            {
                "ROBY_ORCH_LOCAL_FIRST_SCHEDULE": "0",
                "ROBY_ORCH_GMAIL_PROFILE": "quality",
                "ROBY_ORCH_GMAIL_LLM_MAX_REVIEWS_QUALITY": "7",
            }
        )

        self.assertEqual(profile, "quality")
        self.assertEqual(overrides["GMAIL_TRIAGE_LLM_ENABLE"], "1")
        self.assertEqual(overrides["GMAIL_TRIAGE_LLM_MODEL"], "google/gemini-3-flash-preview")
        self.assertEqual(overrides["GMAIL_TRIAGE_SEMANTIC_TRIAGE_ENABLE"], "1")
        self.assertEqual(overrides["GMAIL_TRIAGE_SEMANTIC_TRIAGE_MODEL"], "google/gemini-3-flash-preview")
        self.assertEqual(overrides["GMAIL_TRIAGE_SEMANTIC_TRIAGE_MAX_PER_RUN"], "24")
        self.assertEqual(overrides["GMAIL_TRIAGE_TASK_LLM_MODEL"], "google/gemini-3-flash-preview")
        self.assertEqual(overrides["GMAIL_TRIAGE_LLM_MAX_REVIEWS"], "7")


if __name__ == "__main__":
    main()

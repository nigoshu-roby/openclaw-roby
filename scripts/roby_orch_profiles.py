#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo


JST = timezone(timedelta(hours=9))


def _env_enabled(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_hhmm(text: Optional[str], default_minutes: int) -> int:
    raw = str(text or "").strip()
    if not raw:
        return default_minutes
    import re

    match = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if not match:
        return default_minutes
    hour = max(0, min(23, int(match.group(1))))
    minute = max(0, min(59, int(match.group(2))))
    return hour * 60 + minute


def _now_in_tz(tz_name: str, now: Optional[datetime] = None) -> datetime:
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    try:
        return base.astimezone(ZoneInfo(tz_name))
    except Exception:
        return base.astimezone(JST)


def _within_day_window(now_minutes: int, start_minutes: int, end_minutes: int) -> bool:
    if start_minutes == end_minutes:
        return True
    if start_minutes < end_minutes:
        return start_minutes <= now_minutes < end_minutes
    return now_minutes >= start_minutes or now_minutes < end_minutes


def resolve_local_first_schedule(
    env: Dict[str, str],
    *,
    route: str,
    base_profile_key: str,
    default_profile: str,
    default_day_profile: str,
    default_night_profile: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    tz_name = (env.get("ROBY_ORCH_LOCAL_FIRST_TZ", "Asia/Tokyo") or "Asia/Tokyo").strip()
    schedule_enabled = _env_enabled(env.get("ROBY_ORCH_LOCAL_FIRST_SCHEDULE"), True)
    base_profile = (env.get(base_profile_key, default_profile) or default_profile).strip().lower()
    day_profile = (env.get(f"ROBY_ORCH_{route}_PROFILE_DAY", default_day_profile) or default_day_profile).strip().lower()
    night_profile = (env.get(f"ROBY_ORCH_{route}_PROFILE_NIGHT", default_night_profile) or default_night_profile).strip().lower()
    start_raw = env.get("ROBY_ORCH_LOCAL_FIRST_DAY_START", "08:00")
    end_raw = env.get("ROBY_ORCH_LOCAL_FIRST_DAY_END", "20:00")
    start_minutes = _parse_hhmm(start_raw, 8 * 60)
    end_minutes = _parse_hhmm(end_raw, 20 * 60)
    local_now = _now_in_tz(tz_name, now)
    minute_of_day = local_now.hour * 60 + local_now.minute
    in_day = _within_day_window(minute_of_day, start_minutes, end_minutes)
    effective_profile = base_profile
    window = "fixed"
    if schedule_enabled:
        window = "day" if in_day else "night"
        effective_profile = day_profile if in_day else night_profile
    return {
        "schedule_enabled": schedule_enabled,
        "tz": tz_name,
        "window": window,
        "window_label": "日中" if window == "day" else "深夜" if window == "night" else "固定",
        "base_profile": base_profile,
        "day_profile": day_profile,
        "night_profile": night_profile,
        "effective_profile": effective_profile,
        "day_start": start_raw,
        "day_end": end_raw,
        "local_time": local_now.strftime("%H:%M"),
    }


def apply_minutes_llm_profile(env: Dict[str, str], now: Optional[datetime] = None) -> Tuple[str, Dict[str, str]]:
    schedule = resolve_local_first_schedule(
        env,
        route="MINUTES",
        base_profile_key="ROBY_ORCH_MINUTES_LLM_PROFILE",
        default_profile="hybrid",
        default_day_profile="hybrid",
        default_night_profile="local",
        now=now,
    )
    profile = str(schedule["effective_profile"])
    local_fast = (env.get("ROBY_ORCH_MINUTES_LOCAL_FAST_MODEL", "ollama/llama3.2:3b") or "").strip()
    local_quality = (env.get("ROBY_ORCH_MINUTES_LOCAL_QUALITY_MODEL", "ollama/qwen2.5:7b") or "").strip()
    cloud = (env.get("ROBY_ORCH_MINUTES_CLOUD_MODEL", env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview")) or "").strip()

    def _csv(*models: str) -> str:
        return ",".join([m for m in models if m])

    overrides: Dict[str, str] = {}
    if profile == "local":
        overrides = {
            "MINUTES_LOCAL_PREPROCESS_ENABLE": "1",
            "MINUTES_LOCAL_PREPROCESS_MODEL": local_quality or local_fast or cloud,
            "MINUTES_LOCAL_PREPROCESS_MIN_CHARS": env.get("MINUTES_LOCAL_PREPROCESS_MIN_CHARS", "1800"),
            "MINUTES_LOCAL_PREPROCESS_MAX_INPUT_CHARS": env.get("MINUTES_LOCAL_PREPROCESS_MAX_INPUT_CHARS", "12000"),
            "MINUTES_LOCAL_PREPROCESS_TIMEOUT_SEC": env.get("MINUTES_LOCAL_PREPROCESS_TIMEOUT_SEC", "45"),
            "MINUTES_LOCAL_PREPROCESS_NUM_PREDICT": env.get("MINUTES_LOCAL_PREPROCESS_NUM_PREDICT", "1400"),
            "MINUTES_REVIEW_MODELS": _csv(local_quality, local_fast, cloud),
            "MINUTES_TASKS_MODELS": _csv(local_quality, local_fast, cloud),
            "MINUTES_SUMMARY_MODELS": _csv(local_quality, local_fast, cloud),
            "MINUTES_COMPACT_MODELS": _csv(local_fast, local_quality, cloud),
            "MINUTES_REPAIR_MODELS": _csv(local_quality, local_fast, cloud),
            "MINUTES_ENRICH_MODELS": _csv(local_fast, local_quality, cloud),
        }
    elif profile == "cloud":
        overrides = {
            "MINUTES_LOCAL_PREPROCESS_ENABLE": env.get("ROBY_ORCH_MINUTES_LOCAL_PREPROCESS_IN_CLOUD", "0"),
            "MINUTES_LOCAL_PREPROCESS_MODEL": local_fast or local_quality or cloud,
            "MINUTES_LOCAL_PREPROCESS_MIN_CHARS": env.get("MINUTES_LOCAL_PREPROCESS_MIN_CHARS", "1800"),
            "MINUTES_LOCAL_PREPROCESS_MAX_INPUT_CHARS": env.get("MINUTES_LOCAL_PREPROCESS_MAX_INPUT_CHARS", "12000"),
            "MINUTES_LOCAL_PREPROCESS_TIMEOUT_SEC": env.get("MINUTES_LOCAL_PREPROCESS_TIMEOUT_SEC", "30"),
            "MINUTES_LOCAL_PREPROCESS_NUM_PREDICT": env.get("MINUTES_LOCAL_PREPROCESS_NUM_PREDICT", "900"),
            "MINUTES_REVIEW_MODELS": _csv(cloud, local_quality),
            "MINUTES_TASKS_MODELS": _csv(cloud, local_quality),
            "MINUTES_SUMMARY_MODELS": _csv(cloud, local_quality),
            "MINUTES_COMPACT_MODELS": _csv(cloud, local_fast),
            "MINUTES_REPAIR_MODELS": _csv(cloud, local_quality),
            "MINUTES_ENRICH_MODELS": _csv(cloud, local_fast),
        }
    else:  # hybrid
        profile = "hybrid"
        overrides = {
            "MINUTES_LOCAL_PREPROCESS_ENABLE": "1",
            "MINUTES_LOCAL_PREPROCESS_MODEL": local_fast or local_quality or cloud,
            "MINUTES_LOCAL_PREPROCESS_MIN_CHARS": env.get("MINUTES_LOCAL_PREPROCESS_MIN_CHARS", "1800"),
            "MINUTES_LOCAL_PREPROCESS_MAX_INPUT_CHARS": env.get("MINUTES_LOCAL_PREPROCESS_MAX_INPUT_CHARS", "12000"),
            "MINUTES_LOCAL_PREPROCESS_TIMEOUT_SEC": env.get("MINUTES_LOCAL_PREPROCESS_TIMEOUT_SEC", "30"),
            "MINUTES_LOCAL_PREPROCESS_NUM_PREDICT": env.get("MINUTES_LOCAL_PREPROCESS_NUM_PREDICT", "900"),
            "MINUTES_REVIEW_MODELS": _csv(cloud, local_quality, local_fast),
            "MINUTES_TASKS_MODELS": _csv(cloud, local_quality, local_fast),
            "MINUTES_SUMMARY_MODELS": _csv(cloud, local_quality, local_fast),
            "MINUTES_COMPACT_MODELS": _csv(local_fast, cloud),
            "MINUTES_REPAIR_MODELS": _csv(cloud, local_quality, local_fast),
            "MINUTES_ENRICH_MODELS": _csv(local_fast, cloud),
        }
    overrides["ROBY_ORCH_MINUTES_EFFECTIVE_PROFILE"] = profile
    overrides["ROBY_ORCH_MINUTES_WINDOW"] = str(schedule["window"])
    return profile, overrides


def apply_gmail_profile(env: Dict[str, str], now: Optional[datetime] = None) -> Tuple[str, Dict[str, str]]:
    schedule = resolve_local_first_schedule(
        env,
        route="GMAIL",
        base_profile_key="ROBY_ORCH_GMAIL_PROFILE",
        default_profile="fast",
        default_day_profile="fast",
        default_night_profile="hybrid",
        now=now,
    )
    profile = str(schedule["effective_profile"])
    fast_model = (env.get("ROBY_ORCH_GMAIL_LLM_FAST_MODEL", "ollama/llama3.2:3b") or "").strip()
    quality_model = (env.get("ROBY_ORCH_GMAIL_LLM_QUALITY_MODEL", "ollama/qwen2.5:7b") or "").strip()
    overrides: Dict[str, str] = {}
    if profile == "quality":
        overrides = {
            "GMAIL_TRIAGE_LOCAL_PRECLASSIFY_ENABLE": "1",
            "GMAIL_TRIAGE_LOCAL_PRECLASSIFY_MODEL": quality_model or fast_model,
            "GMAIL_TRIAGE_LLM_ENABLE": "1",
            "GMAIL_TRIAGE_LLM_MODEL": quality_model or fast_model,
            "GMAIL_TRIAGE_LLM_MAX_REVIEWS": env.get("ROBY_ORCH_GMAIL_LLM_MAX_REVIEWS_QUALITY", "30"),
        }
    elif profile == "hybrid":
        overrides = {
            "GMAIL_TRIAGE_LOCAL_PRECLASSIFY_ENABLE": "1",
            "GMAIL_TRIAGE_LOCAL_PRECLASSIFY_MODEL": fast_model or quality_model,
            "GMAIL_TRIAGE_LLM_ENABLE": "1",
            "GMAIL_TRIAGE_LLM_MODEL": fast_model or quality_model,
            "GMAIL_TRIAGE_LLM_MAX_REVIEWS": env.get("ROBY_ORCH_GMAIL_LLM_MAX_REVIEWS_HYBRID", "10"),
        }
    else:  # fast
        profile = "fast"
        overrides = {
            "GMAIL_TRIAGE_LOCAL_PRECLASSIFY_ENABLE": env.get("ROBY_ORCH_GMAIL_LOCAL_PRECLASSIFY_FAST", "1"),
            "GMAIL_TRIAGE_LOCAL_PRECLASSIFY_MODEL": fast_model or quality_model,
            "GMAIL_TRIAGE_LLM_ENABLE": "0",
            "GMAIL_TRIAGE_LLM_MODEL": fast_model or quality_model,
            "GMAIL_TRIAGE_LLM_MAX_REVIEWS": env.get("ROBY_ORCH_GMAIL_LLM_MAX_REVIEWS_FAST", "0"),
        }
    overrides["ROBY_ORCH_GMAIL_EFFECTIVE_PROFILE"] = profile
    overrides["ROBY_ORCH_GMAIL_WINDOW"] = str(schedule["window"])
    return profile, overrides

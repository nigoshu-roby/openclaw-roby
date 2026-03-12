#!/usr/bin/env python3
"""Compute precision sprint metrics from local Gmail/Minutes eval corpora."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from roby_audit import append_audit_event

STATE_ROOT = Path.home() / ".openclaw" / "roby"
GMAIL_SUMMARY_PATH = STATE_ROOT / "gmail_eval_corpus_summary.json"
MINUTES_SUMMARY_PATH = STATE_ROOT / "minutes_eval_corpus_summary.json"
GMAIL_CURATED_SUMMARY_PATH = STATE_ROOT / "gmail_golden_curated_summary.json"
MINUTES_CURATED_SUMMARY_PATH = STATE_ROOT / "minutes_golden_curated_summary.json"
METRICS_PATH = STATE_ROOT / "precision_metrics_latest.json"
RUN_LOG_PATH = STATE_ROOT / "precision_metrics_runs.jsonl"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_div(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def compute_domain_metrics(summary: Dict[str, Any], curated_summary: Dict[str, Any], *, domain: str) -> Dict[str, Any]:
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    good = int(counts.get("good", 0) or 0)
    bad = int(counts.get("bad", 0) or 0)
    missed = int(counts.get("missed", 0) or 0)
    pending = int(counts.get("pending", 0) or 0)
    reviewed = int(summary.get("reviewed_items") or summary.get("reviewed_minutes_tasks") or 0)
    curated_items = int(curated_summary.get("curated_items", 0) or 0)
    source_items = int(curated_summary.get("source_items", 0) or 0)

    precision = safe_div(good, good + bad)
    recall = safe_div(good, good + missed)
    usefulness = safe_div(good, good + bad + missed)
    review_coverage = safe_div(good + bad + missed, reviewed)
    curated_coverage = safe_div(curated_items, source_items)
    recall_provisional = missed == 0

    return {
        "domain": domain,
        "reviewed_items": reviewed,
        "good": good,
        "bad": bad,
        "missed": missed,
        "pending": pending,
        "precision": precision,
        "recall": recall,
        "usefulness": usefulness,
        "review_coverage": review_coverage,
        "curated_items": curated_items,
        "curated_source_items": source_items,
        "curated_coverage": curated_coverage,
        "false_negative_observed": missed > 0,
        "recall_provisional": recall_provisional,
        "top_feedback_reasons": summary.get("top_feedback_reasons") or [],
    }


def build_overall(gmail: Dict[str, Any], minutes: Dict[str, Any]) -> Dict[str, Any]:
    good = int(gmail.get("good", 0)) + int(minutes.get("good", 0))
    bad = int(gmail.get("bad", 0)) + int(minutes.get("bad", 0))
    missed = int(gmail.get("missed", 0)) + int(minutes.get("missed", 0))
    reviewed = int(gmail.get("reviewed_items", 0)) + int(minutes.get("reviewed_items", 0))
    pending = int(gmail.get("pending", 0)) + int(minutes.get("pending", 0))
    return {
        "reviewed_items": reviewed,
        "good": good,
        "bad": bad,
        "missed": missed,
        "pending": pending,
        "precision": safe_div(good, good + bad),
        "recall": safe_div(good, good + missed),
        "usefulness": safe_div(good, good + bad + missed),
        "review_coverage": safe_div(good + bad + missed, reviewed),
        "false_negative_observed": missed > 0,
        "recall_provisional": missed == 0,
    }


def log_run(entry: Dict[str, Any]) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    gmail_summary = load_json(GMAIL_SUMMARY_PATH)
    minutes_summary = load_json(MINUTES_SUMMARY_PATH)
    gmail_curated_summary = load_json(GMAIL_CURATED_SUMMARY_PATH)
    minutes_curated_summary = load_json(MINUTES_CURATED_SUMMARY_PATH)

    gmail = compute_domain_metrics(gmail_summary, gmail_curated_summary, domain="gmail")
    minutes = compute_domain_metrics(minutes_summary, minutes_curated_summary, domain="minutes")
    overall = build_overall(gmail, minutes)

    payload = {
        "schema_version": 1,
        "generated_at": iso_now(),
        "kind": "precision_metrics",
        "formula": {
            "precision": "good / (good + bad)",
            "recall": "good / (good + missed)",
            "usefulness": "good / (good + bad + missed)",
            "review_coverage": "(good + bad + missed) / reviewed_items",
            "curated_coverage": "curated_items / curated_source_items",
        },
        "notes": {
            "recall_provisional": "missed=0 のときは false negative 観測が未整備の可能性があるため、recall を暫定値として扱う",
        },
        "gmail": gmail,
        "minutes": minutes,
        "overall": overall,
        "paths": {
            "gmail_summary": str(GMAIL_SUMMARY_PATH),
            "minutes_summary": str(MINUTES_SUMMARY_PATH),
            "gmail_curated_summary": str(GMAIL_CURATED_SUMMARY_PATH),
            "minutes_curated_summary": str(MINUTES_CURATED_SUMMARY_PATH),
            "metrics": str(METRICS_PATH),
        },
    }

    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log_run({
        "ts": iso_now(),
        "event": "precision_metrics",
        "gmail_precision": gmail["precision"],
        "minutes_precision": minutes["precision"],
        "overall_precision": overall["precision"],
    })
    append_audit_event(
        "precision.metrics",
        {
            "status": "ok",
            "gmail_precision": gmail["precision"],
            "minutes_precision": minutes["precision"],
            "overall_precision": overall["precision"],
        },
        source="roby-precision-metrics",
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

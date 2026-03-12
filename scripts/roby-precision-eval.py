#!/usr/bin/env python3
"""Evaluate precision sprint metrics against operational thresholds."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from roby_audit import append_audit_event

STATE_ROOT = Path.home() / ".openclaw" / "roby"
PRECISION_METRICS_PATH = STATE_ROOT / "precision_metrics_latest.json"
PRECISION_EVAL_PATH = STATE_ROOT / "precision_eval_latest.json"
RUN_LOG_PATH = STATE_ROOT / "precision_eval_runs.jsonl"

THRESHOLDS = {
    "overall": {
        "target_precision": 0.35,
        "min_review_coverage": 0.50,
    },
    "gmail": {
        "target_precision": 0.40,
        "min_review_coverage": 0.50,
        "min_curated_coverage": 0.70,
    },
    "minutes": {
        "target_precision": 0.30,
        "min_review_coverage": 0.55,
        "min_curated_coverage": 0.70,
    },
}


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


def log_run(entry: Dict[str, Any]) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def evaluate_section(
    name: str,
    section: Dict[str, Any],
    thresholds: Dict[str, float],
) -> Dict[str, Any]:
    reviewed_items = _as_int(section.get("reviewed_items"))
    precision = _as_float(section.get("precision"))
    recall = _as_float(section.get("recall"))
    usefulness = _as_float(section.get("usefulness"))
    review_coverage = _as_float(section.get("review_coverage"))
    curated_coverage = _as_float(section.get("curated_coverage"))
    recall_provisional = bool(section.get("recall_provisional"))

    issues: List[str] = []
    status = "ok"

    if reviewed_items <= 0:
        status = "insufficient"
        issues.append(f"{name}: reviewed_items が 0 件です")
    else:
        target_precision = float(thresholds.get("target_precision", 0.0))
        min_review_coverage = float(thresholds.get("min_review_coverage", 0.0))
        min_curated_coverage = float(thresholds.get("min_curated_coverage", 0.0))

        if precision < target_precision:
            status = "fail"
            issues.append(
                f"{name}: precision {precision:.1%} が目標 {target_precision:.1%} を下回っています"
            )

        if review_coverage < min_review_coverage:
            status = "attention" if status == "ok" else status
            issues.append(
                f"{name}: review coverage {review_coverage:.1%} が下限 {min_review_coverage:.1%} を下回っています"
            )

        if min_curated_coverage > 0 and curated_coverage < min_curated_coverage:
            status = "attention" if status == "ok" else status
            issues.append(
                f"{name}: curated coverage {curated_coverage:.1%} が下限 {min_curated_coverage:.1%} を下回っています"
            )

        if recall_provisional:
            status = "attention" if status == "ok" else status
            issues.append(f"{name}: recall は暫定値です（missed 未観測）")

    return {
        "name": name,
        "status": status,
        "precision": precision,
        "recall": recall,
        "recall_provisional": recall_provisional,
        "usefulness": usefulness,
        "review_coverage": review_coverage,
        "curated_coverage": curated_coverage,
        "reviewed_items": reviewed_items,
        "target_precision": float(thresholds.get("target_precision", 0.0)),
        "min_review_coverage": float(thresholds.get("min_review_coverage", 0.0)),
        "min_curated_coverage": float(thresholds.get("min_curated_coverage", 0.0)),
        "issues": issues,
    }


def compute_gate(sections: List[Dict[str, Any]], issues: List[str]) -> str:
    statuses = {str(section.get("status") or "") for section in sections}
    if "fail" in statuses:
        return "fail"
    if "attention" in statuses or issues:
        return "attention"
    if "insufficient" in statuses:
        return "insufficient"
    return "ok"


def build_summary(gate: str, sections: List[Dict[str, Any]]) -> str:
    labels = " / ".join(
        f"{section['name']}={section['status']}"
        for section in sections
        if isinstance(section, dict)
    )
    return f"gate={gate} ({labels})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    metrics = load_json(PRECISION_METRICS_PATH)
    overall = evaluate_section("overall", metrics.get("overall") or {}, THRESHOLDS["overall"])
    gmail = evaluate_section("gmail", metrics.get("gmail") or {}, THRESHOLDS["gmail"])
    minutes = evaluate_section("minutes", metrics.get("minutes") or {}, THRESHOLDS["minutes"])

    section_rows = [overall, gmail, minutes]
    issues = [issue for section in section_rows for issue in list(section.get("issues") or [])]
    gate = compute_gate(section_rows, issues)

    payload = {
        "schema_version": 1,
        "generated_at": iso_now(),
        "kind": "precision_eval",
        "gate": gate,
        "thresholds": THRESHOLDS,
        "overall": overall,
        "gmail": gmail,
        "minutes": minutes,
        "issues": issues,
        "summary": build_summary(gate, section_rows),
        "paths": {
            "precision_metrics": str(PRECISION_METRICS_PATH),
            "precision_eval": str(PRECISION_EVAL_PATH),
        },
    }

    PRECISION_EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRECISION_EVAL_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log_run(
        {
            "ts": iso_now(),
            "event": "precision_eval",
            "gate": gate,
            "overall_status": overall["status"],
            "gmail_status": gmail["status"],
            "minutes_status": minutes["status"],
            "issue_count": len(issues),
        }
    )
    append_audit_event(
        "precision.eval",
        {
            "status": gate,
            "issue_count": len(issues),
            "overall_status": overall["status"],
            "gmail_status": gmail["status"],
            "minutes_status": minutes["status"],
        },
        source="roby-precision-eval",
    )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

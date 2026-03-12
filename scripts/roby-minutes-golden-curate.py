#!/usr/bin/env python3
"""Curate a representative minutes golden set for eval runs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from roby_audit import append_audit_event

STATE_ROOT = Path.home() / ".openclaw" / "roby"
SOURCE_GOLDEN_PATH = STATE_ROOT / "minutes_golden_set.json"
CURATED_PATH = STATE_ROOT / "minutes_golden_curated.json"
SUMMARY_PATH = STATE_ROOT / "minutes_golden_curated_summary.json"
RUN_LOG_PATH = STATE_ROOT / "minutes_golden_curated_runs.jsonl"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_golden_items(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) else []
    return [row for row in items if isinstance(row, dict)]


def stable_sort_key(row: Dict[str, Any]) -> tuple:
    return (
        str(row.get("project") or ""),
        str(row.get("source_doc_title") or ""),
        str(row.get("parent_origin_id") or ""),
        str(row.get("title") or ""),
    )


def curate_items(items: List[Dict[str, Any]], *, max_items: int) -> List[Dict[str, Any]]:
    if max_items <= 0:
        return []
    rows = sorted(items, key=stable_sort_key)
    selected: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    project_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    by_project: dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for row in rows:
        by_project[str(row.get("project") or "unknown")].append(row)

    # First pass: at least one item per project
    for project in sorted(by_project):
        for row in by_project[project]:
            key = str(row.get("origin_id") or "")
            if not key or key in seen_keys:
                continue
            selected.append(row)
            seen_keys.add(key)
            if project:
                project_counts[project] += 1
            source = str(row.get("source_doc_title") or "").strip()
            if source:
                source_counts[source] += 1
            break

    # Second pass: keep source-doc diversity while filling remaining slots
    for row in rows:
        if len(selected) >= max_items:
            break
        key = str(row.get("origin_id") or "")
        if not key or key in seen_keys:
            continue
        source = str(row.get("source_doc_title") or "").strip()
        if source and source_counts[source] >= 2:
            continue
        selected.append(row)
        seen_keys.add(key)
        project = str(row.get("project") or "").strip()
        if project:
            project_counts[project] += 1
        if source:
            source_counts[source] += 1

    # Final fill if still short
    for row in rows:
        if len(selected) >= max_items:
            break
        key = str(row.get("origin_id") or "")
        if not key or key in seen_keys:
            continue
        selected.append(row)
        seen_keys.add(key)

    return selected[:max_items]


def build_summary(items: List[Dict[str, Any]], curated: List[Dict[str, Any]]) -> Dict[str, Any]:
    project_counts = Counter(str(row.get("project") or "") for row in curated if row.get("project"))
    source_counts = Counter(str(row.get("source_doc_title") or "") for row in curated if row.get("source_doc_title"))
    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "source_items": len(items),
        "curated_items": len(curated),
        "project_counts": dict(project_counts),
        "top_source_docs": [{"source_doc_title": key, "count": value} for key, value in source_counts.most_common(10)],
        "source_path": str(SOURCE_GOLDEN_PATH),
        "curated_path": str(CURATED_PATH),
    }


def log_run(entry: Dict[str, Any]) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-items", type=int, default=25)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    items = load_golden_items(SOURCE_GOLDEN_PATH)
    curated = curate_items(items, max_items=max(1, args.max_items))
    payload = {
        "schema_version": 1,
        "generated_at": iso_now(),
        "kind": "minutes_golden_curated",
        "summary": {
            "items": len(curated),
            "max_items": max(1, args.max_items),
            "note": "代表ケースを eval 用に固定化した minutes curated golden set。",
        },
        "items": curated,
    }
    summary = build_summary(items, curated)
    CURATED_PATH.parent.mkdir(parents=True, exist_ok=True)
    CURATED_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log_run({"ts": iso_now(), "event": "minutes_golden_curated", "source_items": len(items), "curated_items": len(curated)})
    append_audit_event(
        "minutes_golden_curated.run",
        {"status": "ok", "source_items": len(items), "curated_items": len(curated)},
        source="roby-minutes-golden-curate",
    )
    result = {
        "source_items": len(items),
        "curated_items": len(curated),
        "summary_path": str(SUMMARY_PATH),
        "curated_path": str(CURATED_PATH),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

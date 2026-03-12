#!/usr/bin/env python3
"""Capture Gmail false negatives locally for eval corpus updates."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from roby_audit import append_audit_event

STATE_ROOT = Path.home() / ".openclaw" / "roby"
MISSED_MANUAL_PATH = STATE_ROOT / "gmail_missed_manual.jsonl"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_row(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--source-doc-id", default="")
    parser.add_argument("--source-doc-title", default="")
    parser.add_argument("--sender-label", default="")
    parser.add_argument("--expected-title", default="")
    parser.add_argument("--expected-task-type", choices=["reply", "action"], default="reply")
    parser.add_argument("--expected-bucket", default="task")
    parser.add_argument("--reason", default="")
    args = parser.parse_args()

    if args.list:
        rows = read_rows(MISSED_MANUAL_PATH)
        output = {"items": rows, "count": len(rows), "path": str(MISSED_MANUAL_PATH)}
        print(json.dumps(output, ensure_ascii=False) if args.json else json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    if not args.expected_title.strip():
        raise SystemExit("--expected-title is required")

    row = {
        "id": f"gmail-missed-{uuid.uuid4().hex[:12]}",
        "ts": iso_now(),
        "origin_id": f"manual:gmail:missed:{uuid.uuid4().hex[:10]}",
        "source_doc_id": args.source_doc_id.strip(),
        "source_doc_title": args.source_doc_title.strip(),
        "sender_label": args.sender_label.strip(),
        "expected_title": args.expected_title.strip(),
        "expected_task_type": args.expected_task_type,
        "expected_bucket": args.expected_bucket.strip() or "task",
        "reason": args.reason.strip(),
        "reason_code": "manual_missed_capture",
        "project": "email",
    }
    append_row(MISSED_MANUAL_PATH, row)
    append_audit_event(
        "gmail_missed_capture.add",
        {
            "status": "ok",
            "source_doc_id": row["source_doc_id"],
            "source_doc_title": row["source_doc_title"],
            "expected_task_type": row["expected_task_type"],
        },
        source="roby-gmail-missed-capture",
    )
    output = {"ok": True, "path": str(MISSED_MANUAL_PATH), "item": row}
    print(json.dumps(output, ensure_ascii=False) if args.json else json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

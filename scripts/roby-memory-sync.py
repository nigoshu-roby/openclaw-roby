#!/usr/bin/env python3
"""Update PBS durable memory and heartbeat from local ops artifacts."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from roby_audit import append_audit_event

JST = timezone(timedelta(hours=9))
STATE_ROOT = Path.home() / ".openclaw" / "roby"
STATE_PATH = STATE_ROOT / "memory_sync_state.json"
RUN_LOG_PATH = STATE_ROOT / "memory_sync_runs.jsonl"
OPENCLAW_REPO = Path(__file__).resolve().parent.parent
MEMORY_FILE = OPENCLAW_REPO / "MEMORY.md"
HEARTBEAT_FILE = OPENCLAW_REPO / "HEARTBEAT.md"
DAILY_MEMORY_DIR = OPENCLAW_REPO / "memory"

WEEKLY_LATEST = STATE_ROOT / "reports" / "weekly_latest.json"
FEEDBACK_LATEST = STATE_ROOT / "feedback_sync_state.json"
EVAL_LATEST = STATE_ROOT / "evals" / "latest.json"
DRILL_LATEST = STATE_ROOT / "drills" / "latest.json"

MEMORY_START = "<!-- ROBY:MEMORY-SNAPSHOT:START -->"
MEMORY_END = "<!-- ROBY:MEMORY-SNAPSHOT:END -->"
HEARTBEAT_START = "<!-- ROBY:HEARTBEAT-STATUS:START -->"
HEARTBEAT_END = "<!-- ROBY:HEARTBEAT-STATUS:END -->"

LIVE_FRESHNESS_TARGETS = [
    {
        "name": "self_growth",
        "type": "jsonl",
        "path": STATE_ROOT / "self_growth_runs.jsonl",
        "max_minutes_env": "ROBY_DRILL_SELF_GROWTH_MAX_MIN",
        "default": 180,
    },
    {
        "name": "minutes_sync",
        "type": "jsonl",
        "path": STATE_ROOT / "minutes_runs.jsonl",
        "max_minutes_env": "ROBY_DRILL_MINUTES_MAX_MIN",
        "default": 240,
    },
    {
        "name": "gmail_triage",
        "type": "jsonl",
        "path": STATE_ROOT / "gmail_triage_runs.jsonl",
        "max_minutes_env": "ROBY_DRILL_GMAIL_MAX_MIN",
        "default": 120,
    },
    {
        "name": "notion_sync",
        "type": "json",
        "path": STATE_ROOT / "notion_sync_state.json",
        "max_minutes_env": "ROBY_DRILL_NOTION_MAX_MIN",
        "default": 1440,
    },
    {
        "name": "weekly_report",
        "type": "json",
        "path": STATE_ROOT / "reports" / "weekly_latest.json",
        "max_minutes_env": "ROBY_DRILL_WEEKLY_MAX_MIN",
        "default": 10080,
    },
]


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_last_jsonl(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return {}
        payload = json.loads(lines[-1])
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def read_last_jsonl_timestamp(path: Path) -> Optional[datetime]:
    if not path.exists():
        return None
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception:
        return None
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            ts = parse_ts(payload.get("ts") or payload.get("timestamp"))
            if ts is not None:
                return ts
    return None


def parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(JST)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST)
    except Exception:
        return None


def compute_live_stale_components() -> List[str]:
    now = datetime.now(timezone.utc)
    stale: List[str] = []
    for target in LIVE_FRESHNESS_TARGETS:
        if target["type"] == "jsonl":
            ts = read_last_jsonl_timestamp(target["path"])
        else:
            payload = read_json(target["path"])
            ts = parse_ts(payload.get("updated_at") or payload.get("generated_at") or payload.get("ts"))
        age_minutes: Optional[float] = None
        if ts is not None:
            age_minutes = (now - ts.astimezone(timezone.utc)).total_seconds() / 60.0
        threshold = int(os.getenv(target["max_minutes_env"], str(target["default"])) or target["default"])
        if age_minutes is None or age_minutes > threshold:
            stale.append(str(target["name"]))
    return stale


def replace_or_append_block(text: str, start_marker: str, end_marker: str, block: str) -> str:
    replacement = f"{start_marker}\n{block}\n{end_marker}"
    if start_marker in text and end_marker in text:
        prefix, rest = text.split(start_marker, 1)
        _old, suffix = rest.split(end_marker, 1)
        return f"{prefix}{replacement}{suffix}"
    base = text.rstrip()
    if base:
        base += "\n\n"
    return f"{base}{replacement}\n"


def build_snapshot() -> Dict[str, Any]:
    weekly = read_json(WEEKLY_LATEST)
    feedback = read_json(FEEDBACK_LATEST)
    eval_latest = read_json(EVAL_LATEST)
    drill_latest = read_json(DRILL_LATEST)

    generated_at = datetime.now(JST)
    weekly_ts = parse_ts(weekly.get("generated_at"))
    feedback_ts = parse_ts(feedback.get("updated_at"))

    eval_failed = int(weekly.get("eval", {}).get("failed_runs", 0) or 0)
    drill_failed = int(weekly.get("drill", {}).get("failed_runs", 0) or 0)
    weekly_stale_components = [
        str(x).strip()
        for x in (weekly.get("freshness", {}) or {}).get("stale_components", []) or []
        if str(x).strip()
    ]
    stale_components = compute_live_stale_components()
    audit_errors = int(weekly.get("audit", {}).get("errors", 0) or 0)

    feedback_summary = feedback.get("summary", {}) if isinstance(feedback.get("summary"), dict) else {}
    reviewed_count = int(feedback_summary.get("reviewed_count", 0) or 0)
    actionable_count = int(feedback_summary.get("actionable_count", 0) or 0)
    counts = feedback_summary.get("counts", {}) if isinstance(feedback_summary.get("counts"), dict) else {}
    actionable_reason_counts = (
        feedback_summary.get("actionable_reason_counts", {})
        if isinstance(feedback_summary.get("actionable_reason_counts"), dict)
        else {}
    )
    improvement_targets = feedback_summary.get("improvement_targets", []) if isinstance(feedback_summary.get("improvement_targets"), list) else []
    recent_actionable = feedback_summary.get("recent_actionable", []) if isinstance(feedback_summary.get("recent_actionable"), list) else []

    unresolved: List[str] = []
    if not bool(eval_latest.get("all_ok", False)) and eval_latest:
        unresolved.append(
            f"Evaluation Harness fail {int(eval_latest.get('failed', 0) or 0)}/{int(eval_latest.get('total', 0) or 0)}"
        )
    if not bool(drill_latest.get("all_ok", False)) and drill_latest:
        unresolved.append(
            f"Runbook Drill fail {int(drill_latest.get('failed', 0) or 0)}/{int(drill_latest.get('total', 0) or 0)}"
        )
    if stale_components:
        unresolved.append(f"stale component: {' / '.join(stale_components)}")
    if audit_errors > 0:
        unresolved.append(f"audit errors: {audit_errors}")

    heartbeat_status = "HEARTBEAT_ATTENTION" if unresolved else "HEARTBEAT_OK"
    top_targets = []
    for row in improvement_targets[:3]:
        if not isinstance(row, dict):
            continue
        top_targets.append(
            {
                "target": str(row.get("target") or "").strip(),
                "label": str(row.get("label") or "").strip(),
                "count": int(row.get("count", 0) or 0),
                "recommendation": str(row.get("recommendation") or "").strip(),
            }
        )

    feedback_reason_rows = []
    for reason_code, count in sorted(
        actionable_reason_counts.items(),
        key=lambda item: int(item[1] or 0),
        reverse=True,
    ):
        feedback_reason_rows.append(
            {
                "reason_code": str(reason_code).strip(),
                "count": int(count or 0),
            }
        )

    weekly_current_failed = int(eval_latest.get("failed", 0) or 0) if eval_latest else 0
    weekly_current_total = int(eval_latest.get("total", 0) or 0) if eval_latest else 0
    drill_current_failed = int(drill_latest.get("failed", 0) or 0) if drill_latest else 0
    drill_current_total = int(drill_latest.get("total", 0) or 0) if drill_latest else 0

    return {
        "updated_at": generated_at.isoformat(),
        "heartbeat_status": heartbeat_status,
        "unresolved": unresolved,
        "weekly_generated_at": weekly_ts.isoformat() if weekly_ts else "",
        "feedback_updated_at": feedback_ts.isoformat() if feedback_ts else "",
        "sources": {
            "weekly": {
                "present": bool(weekly),
                "updated_at": weekly_ts.isoformat() if weekly_ts else "",
            },
            "feedback": {
                "present": bool(feedback),
                "updated_at": feedback_ts.isoformat() if feedback_ts else "",
            },
            "evaluation": {
                "present": bool(eval_latest),
                "updated_at": str(eval_latest.get("ts") or "").strip() if eval_latest else "",
            },
            "drill": {
                "present": bool(drill_latest),
                "updated_at": str(drill_latest.get("ts") or "").strip() if drill_latest else "",
            },
        },
        "quality": {
            "evaluation": {
                "all_ok": bool(eval_latest.get("all_ok", False)) if eval_latest else False,
                "failed": weekly_current_failed,
                "total": weekly_current_total,
            },
            "drill": {
                "all_ok": bool(drill_latest.get("all_ok", False)) if drill_latest else False,
                "failed": drill_current_failed,
                "total": drill_current_total,
            },
            "audit_errors_7d": audit_errors,
            "stale_components": stale_components,
        },
        "stale_components": stale_components,
        "weekly_stale_components": weekly_stale_components,
        "eval_failed_runs_7d": eval_failed,
        "drill_failed_runs_7d": drill_failed,
        "audit_errors_7d": audit_errors,
        "reviewed_count": reviewed_count,
        "actionable_count": actionable_count,
        "counts": {
            "good": int(counts.get("good", 0) or 0),
            "bad": int(counts.get("bad", 0) or 0),
            "missed": int(counts.get("missed", 0) or 0),
            "pending": int(counts.get("pending", 0) or 0),
        },
        "feedback_reason_counts": feedback_reason_rows,
        "top_targets": top_targets,
        "recent_actionable": [
            {
                "title": str((row or {}).get("title") or "").strip(),
                "feedback_state": str((row or {}).get("feedback_state") or "").strip(),
                "feedback_reason_code": str((row or {}).get("feedback_reason_code") or "").strip(),
            }
            for row in recent_actionable[:3]
            if isinstance(row, dict)
        ],
    }


def render_memory_block(snapshot: Dict[str, Any]) -> str:
    quality = snapshot.get("quality", {}) if isinstance(snapshot.get("quality"), dict) else {}
    eval_quality = quality.get("evaluation", {}) if isinstance(quality.get("evaluation"), dict) else {}
    drill_quality = quality.get("drill", {}) if isinstance(quality.get("drill"), dict) else {}
    sources = snapshot.get("sources", {}) if isinstance(snapshot.get("sources"), dict) else {}
    reason_rows = snapshot.get("feedback_reason_counts", []) if isinstance(snapshot.get("feedback_reason_counts"), list) else []

    lines = [
        "### 現在の運用状態",
        f"- 最終同期: {snapshot['updated_at']}",
        f"- heartbeat: {snapshot['heartbeat_status']}",
        f"- 未解消項目: {' / '.join(snapshot.get('unresolved', [])) if snapshot.get('unresolved') else 'なし'}",
        "",
        "### 監視ソース",
        f"- 週次集計: {(sources.get('weekly', {}) if isinstance(sources.get('weekly'), dict) else {}).get('updated_at') or '未取得'}",
        f"- feedback: {(sources.get('feedback', {}) if isinstance(sources.get('feedback'), dict) else {}).get('updated_at') or '未取得'}",
        f"- evaluation: {(sources.get('evaluation', {}) if isinstance(sources.get('evaluation'), dict) else {}).get('updated_at') or '未取得'}",
        f"- drill: {(sources.get('drill', {}) if isinstance(sources.get('drill'), dict) else {}).get('updated_at') or '未取得'}",
        "",
        "### 品質ゲート",
        f"- evaluation(current): {'PASS' if eval_quality.get('all_ok') else 'FAIL'} {int(eval_quality.get('failed', 0) or 0)}/{int(eval_quality.get('total', 0) or 0)}",
        f"- drill(current): {'PASS' if drill_quality.get('all_ok') else 'FAIL'} {int(drill_quality.get('failed', 0) or 0)}/{int(drill_quality.get('total', 0) or 0)}",
        f"- audit errors(7d): {snapshot.get('audit_errors_7d', 0)}",
        f"- stale component(now): {' / '.join(snapshot.get('stale_components', [])) if snapshot.get('stale_components') else 'なし'}",
        "",
        "### フィードバック要約",
        (
            f"- reviewed {snapshot.get('reviewed_count', 0)} / actionable {snapshot.get('actionable_count', 0)}"
            f" / good {snapshot.get('counts', {}).get('good', 0)} / bad {snapshot.get('counts', {}).get('bad', 0)} / missed {snapshot.get('counts', {}).get('missed', 0)}"
        ),
    ]
    if reason_rows:
        lines.append("- Bad理由の上位:")
        for row in reason_rows[:5]:
            lines.append(f"  - {row.get('reason_code') or 'unknown'}: {int(row.get('count', 0) or 0)}")
    targets = snapshot.get("top_targets", [])
    if targets:
        lines.extend(["", "### 直近の改善フォーカス"])
        for row in targets:
            label = row.get("label") or row.get("target") or "unknown"
            count = int(row.get("count", 0) or 0)
            recommendation = row.get("recommendation") or ""
            lines.append(f"  - {label}: {count}")
            if recommendation:
                lines.append(f"    - {recommendation}")
    recent = snapshot.get("recent_actionable", [])
    if recent:
        lines.extend(["", "### 直近の要確認評価"])
        for row in recent:
            title = row.get("title") or "unknown"
            state = row.get("feedback_state") or "unknown"
            reason = row.get("feedback_reason_code") or ""
            reason_part = f" / {reason}" if reason else ""
            lines.append(f"  - [{state}{reason_part}] {title}")
    return "\n".join(lines)


def render_heartbeat_block(snapshot: Dict[str, Any]) -> str:
    quality = snapshot.get("quality", {}) if isinstance(snapshot.get("quality"), dict) else {}
    eval_quality = quality.get("evaluation", {}) if isinstance(quality.get("evaluation"), dict) else {}
    drill_quality = quality.get("drill", {}) if isinstance(quality.get("drill"), dict) else {}
    lines = [
        "### 判定",
        f"- 最終同期: {snapshot['updated_at']}",
        f"- 現在状態: {snapshot['heartbeat_status']}",
        "",
        "### いま見るべき運用信号",
        f"- stale component: {' / '.join(snapshot.get('stale_components', [])) if snapshot.get('stale_components') else 'なし'}",
        f"- evaluation(current): {'PASS' if eval_quality.get('all_ok') else 'FAIL'} {int(eval_quality.get('failed', 0) or 0)}/{int(eval_quality.get('total', 0) or 0)}",
        f"- drill(current): {'PASS' if drill_quality.get('all_ok') else 'FAIL'} {int(drill_quality.get('failed', 0) or 0)}/{int(drill_quality.get('total', 0) or 0)}",
        f"- eval fail runs (7d): {snapshot.get('eval_failed_runs_7d', 0)}",
        f"- drill fail runs (7d): {snapshot.get('drill_failed_runs_7d', 0)}",
        f"- audit errors (7d): {snapshot.get('audit_errors_7d', 0)}",
    ]
    if snapshot.get("unresolved"):
        lines.extend(["", "### 現在の未解消事項"])
        for item in snapshot["unresolved"]:
            lines.append(f"  - {item}")
    else:
        lines.extend(["", "### 現在の未解消事項", "- なし"])
    targets = snapshot.get("top_targets", [])
    if targets:
        lines.extend(["", "### 次に見るべき改善対象"])
        for row in targets:
            label = row.get("label") or row.get("target") or "unknown"
            recommendation = row.get("recommendation") or ""
            lines.append(f"  - {label}")
            if recommendation:
                lines.append(f"    - {recommendation}")
    return "\n".join(lines)


def write_files(snapshot: Dict[str, Any], dry_run: bool) -> Dict[str, str]:
    memory_text = MEMORY_FILE.read_text(encoding="utf-8")
    heartbeat_text = HEARTBEAT_FILE.read_text(encoding="utf-8")
    updated_memory = replace_or_append_block(memory_text, MEMORY_START, MEMORY_END, render_memory_block(snapshot))
    updated_heartbeat = replace_or_append_block(
        heartbeat_text, HEARTBEAT_START, HEARTBEAT_END, render_heartbeat_block(snapshot)
    )

    day_file = DAILY_MEMORY_DIR / f"{datetime.now(JST).strftime('%Y-%m-%d')}.md"
    daily_section = "\n".join(
        [
            f"# {datetime.now(JST).strftime('%Y-%m-%d')} PBS Ops Memory",
            "",
            f"## {datetime.now(JST).strftime('%H:%M JST')}",
            render_memory_block(snapshot),
            "",
        ]
    )

    if not dry_run:
        MEMORY_FILE.write_text(updated_memory, encoding="utf-8")
        HEARTBEAT_FILE.write_text(updated_heartbeat, encoding="utf-8")
        DAILY_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        existing = day_file.read_text(encoding="utf-8") if day_file.exists() else ""
        if daily_section.strip() not in existing:
            body = existing.rstrip()
            if body:
                body += "\n\n"
            body += daily_section.strip() + "\n"
            day_file.write_text(body, encoding="utf-8")
    return {
        "memory_path": str(MEMORY_FILE),
        "heartbeat_path": str(HEARTBEAT_FILE),
        "daily_note_path": str(day_file),
    }


def run(dry_run: bool) -> Dict[str, Any]:
    snapshot = build_snapshot()
    paths = write_files(snapshot, dry_run=dry_run)
    payload = {
        "updated_at": snapshot["updated_at"],
        "heartbeat_status": snapshot["heartbeat_status"],
        "unresolved_count": len(snapshot.get("unresolved", [])),
        "unresolved": snapshot.get("unresolved", []),
        "top_targets": snapshot.get("top_targets", []),
        "sources": snapshot.get("sources", {}),
        "quality": snapshot.get("quality", {}),
        "feedback_reason_counts": snapshot.get("feedback_reason_counts", []),
        "paths": paths,
        "dry_run": dry_run,
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        append_jsonl(RUN_LOG_PATH, payload)
        append_audit_event(
            "memory_sync.run",
            payload,
            source="roby-memory-sync",
            severity="warn" if snapshot["heartbeat_status"] != "HEARTBEAT_OK" else "info",
            run_id=f"memory:{snapshot['updated_at']}",
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(
            f"memory_sync: {result['heartbeat_status']} unresolved={result['unresolved_count']} daily={result['paths']['daily_note_path']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

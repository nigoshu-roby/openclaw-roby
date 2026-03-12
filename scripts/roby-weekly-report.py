#!/usr/bin/env python3
"""Generate weekly PBS ops report from local run artifacts."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from roby_audit import append_audit_event, verify_audit
from roby_ops_notifications import format_weekly_slack

JST = timezone(timedelta(hours=9))
ENV_PATH = Path.home() / ".openclaw" / ".env"
STATE_ROOT = Path.home() / ".openclaw" / "roby"
REPORT_DIR = STATE_ROOT / "reports"
LATEST_JSON = REPORT_DIR / "weekly_latest.json"
LATEST_MD = REPORT_DIR / "weekly_latest.md"
HISTORY_JSONL = REPORT_DIR / "weekly_history.jsonl"
PRECISION_METRICS_LATEST = STATE_ROOT / "precision_metrics_latest.json"

EVAL_HISTORY = STATE_ROOT / "evals" / "history.jsonl"
DRILL_HISTORY = STATE_ROOT / "drills" / "history.jsonl"
AB_HISTORY = STATE_ROOT / "ab_router_runs.jsonl"
AUDIT_FILE = STATE_ROOT / "audit" / "events.jsonl"
FEEDBACK_HISTORY = STATE_ROOT / "feedback_sync_runs.jsonl"
SELF_GROWTH_HISTORY = STATE_ROOT / "self_growth_runs.jsonl"
KEYCHAIN_SECRET_KEYS = {
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "NOTION_TOKEN",
    "NOTION_API_KEY",
    "SLACK_WEBHOOK_URL",
    "SLACK_SIGNING_SECRET",
    "SLACK_BOT_TOKEN",
    "NEURONIC_TOKEN",
    "OLLAMA_API_KEY",
}


def load_env() -> Dict[str, str]:
    env = dict(os.environ)
    env_file = Path(env.get("ROBY_ENV_FILE", str(ENV_PATH))).expanduser()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if key not in env or not str(env.get(key, "")).strip():
                env[key] = val
    keychain_service = env.get("ROBY_KEYCHAIN_SERVICE", "roby-pbs")
    for key in KEYCHAIN_SECRET_KEYS:
        if key in env and str(env.get(key, "")).strip():
            continue
        try:
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", keychain_service, "-a", key, "-w"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                value = (proc.stdout or "").strip()
                if value:
                    env[key] = value
        except Exception:
            continue
    return env


def parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def in_window(items: List[Dict[str, Any]], since: datetime) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in items:
        dt = parse_ts(row.get("ts") or row.get("timestamp"))
        if dt is None:
            continue
        if dt >= since:
            out.append(row)
    return out


def summarize_eval(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        return {"runs": 0}
    failed_runs = sum(1 for x in items if not bool(x.get("all_ok", False)))
    failure_rates = [float(x.get("failure_rate", 0.0) or 0.0) for x in items]
    p95s = [int((x.get("latency") or {}).get("p95_ms", 0) or 0) for x in items]
    return {
        "runs": len(items),
        "failed_runs": failed_runs,
        "pass_rate": round((len(items) - failed_runs) / len(items), 4),
        "avg_failure_rate": round(statistics.fmean(failure_rates), 4),
        "avg_p95_ms": int(statistics.fmean(p95s)) if p95s else 0,
        "latest": items[-1],
    }


def summarize_drill(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        return {"runs": 0}
    failed_runs = sum(1 for x in items if not bool(x.get("all_ok", False)))
    return {
        "runs": len(items),
        "failed_runs": failed_runs,
        "pass_rate": round((len(items) - failed_runs) / len(items), 4),
        "latest": items[-1],
    }


def summarize_freshness_from_drill(drill_summary: Dict[str, Any]) -> Dict[str, Any]:
    latest = drill_summary.get("latest")
    if not isinstance(latest, dict):
        return {"present": False, "ok": False, "stale_count": 0, "stale_components": [], "detail": ""}
    checks = latest.get("checks")
    if not isinstance(checks, list):
        return {"present": False, "ok": False, "stale_count": 0, "stale_components": [], "detail": ""}
    target: Optional[Dict[str, Any]] = None
    for row in checks:
        if isinstance(row, dict) and str(row.get("id") or "") == "pipeline_freshness":
            target = row
            break
    if not target:
        return {"present": False, "ok": False, "stale_count": 0, "stale_components": [], "detail": ""}

    detail = str(target.get("detail") or "")
    stale_components: List[str] = []
    if "/ stale:" in detail:
        stale_part = detail.split("/ stale:", 1)[1]
        if "/ remedy:" in stale_part:
            stale_part = stale_part.split("/ remedy:", 1)[0]
        parts = [x.strip() for x in stale_part.split(",") if x.strip()]
        for item in parts:
            stale_components.append(item.split(":", 1)[0])

    return {
        "present": True,
        "ok": bool(target.get("ok", False)),
        "stale_count": len(stale_components),
        "stale_components": stale_components,
        "detail": detail,
    }


def summarize_ab(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        return {"runs": 0, "arms": {}, "guard_applied_runs": 0}
    by_arm: Dict[str, Dict[str, Any]] = {}
    guard_applied_runs = 0
    for row in items:
        arm = str(row.get("arm_id") or "unknown")
        b = by_arm.setdefault(arm, {"runs": 0, "ok": 0, "avg_elapsed_ms": 0.0})
        b["runs"] += 1
        if bool(row.get("ok", False)):
            b["ok"] += 1
        b["avg_elapsed_ms"] += float(row.get("elapsed_ms", 0) or 0)
        if bool(row.get("guard_applied", False)):
            guard_applied_runs += 1
    for arm, b in by_arm.items():
        runs = int(b["runs"])
        b["ok_rate"] = round((int(b["ok"]) / runs), 4) if runs else 0.0
        b["avg_elapsed_ms"] = int(b["avg_elapsed_ms"] / runs) if runs else 0
    return {"runs": len(items), "arms": by_arm, "guard_applied_runs": guard_applied_runs}


def summarize_feedback(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        return {"runs": 0}
    latest = items[-1]
    summary = latest.get("summary") if isinstance(latest.get("summary"), dict) else {}
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    reason_counts = summary.get("actionable_reason_counts") if isinstance(summary.get("actionable_reason_counts"), dict) else {}
    improvement_targets = summary.get("improvement_targets") if isinstance(summary.get("improvement_targets"), list) else []
    recent_actionable = summary.get("recent_actionable") if isinstance(summary.get("recent_actionable"), list) else []
    return {
        "runs": len(items),
        "reviewed_count": int(summary.get("reviewed_count", 0)),
        "actionable_count": int(summary.get("actionable_count", 0)),
        "good": int(counts.get("good", 0)),
        "bad": int(counts.get("bad", 0)),
        "missed": int(counts.get("missed", 0)),
        "pending": int(counts.get("pending", 0)),
        "actionable_reason_counts": reason_counts,
        "improvement_targets": [row for row in improvement_targets if isinstance(row, dict)][:5],
        "latest": latest,
        "recent_actionable": [
            {
                "title": str((row or {}).get("title") or "").strip(),
                "feedback_state": str((row or {}).get("feedback_state") or "").strip(),
                "feedback_reason_code": str((row or {}).get("feedback_reason_code") or "").strip(),
            }
            for row in recent_actionable[:5]
            if isinstance(row, dict)
        ],
    }


def build_feedback_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    return {
        "ts": str(row.get("ts") or row.get("timestamp") or ""),
        "reviewed_count": int(summary.get("reviewed_count", 0)),
        "actionable_count": int(summary.get("actionable_count", 0)),
        "good": int(counts.get("good", 0)),
        "bad": int(counts.get("bad", 0)),
        "missed": int(counts.get("missed", 0)),
        "pending": int(counts.get("pending", 0)),
    }


def compute_feedback_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "before_ts": str(before.get("ts") or ""),
        "after_ts": str(after.get("ts") or ""),
        "reviewed_before": int(before.get("reviewed_count", 0)),
        "reviewed_after": int(after.get("reviewed_count", 0)),
        "reviewed_delta": int(after.get("reviewed_count", 0)) - int(before.get("reviewed_count", 0)),
        "actionable_before": int(before.get("actionable_count", 0)),
        "actionable_after": int(after.get("actionable_count", 0)),
        "actionable_delta": int(after.get("actionable_count", 0)) - int(before.get("actionable_count", 0)),
        "good_before": int(before.get("good", 0)),
        "good_after": int(after.get("good", 0)),
        "good_delta": int(after.get("good", 0)) - int(before.get("good", 0)),
        "bad_before": int(before.get("bad", 0)),
        "bad_after": int(after.get("bad", 0)),
        "bad_delta": int(after.get("bad", 0)) - int(before.get("bad", 0)),
        "missed_before": int(before.get("missed", 0)),
        "missed_after": int(after.get("missed", 0)),
        "missed_delta": int(after.get("missed", 0)) - int(before.get("missed", 0)),
        "improved": (
            int(after.get("bad", 0)) <= int(before.get("bad", 0))
            and int(after.get("missed", 0)) <= int(before.get("missed", 0))
            and int(after.get("good", 0)) >= int(before.get("good", 0))
        ),
        "worsened": (
            int(after.get("bad", 0)) > int(before.get("bad", 0))
            or int(after.get("missed", 0)) > int(before.get("missed", 0))
        ),
    }


def humanize_self_growth_patch_status(status: Any) -> str:
    value = str(status or "").strip()
    return {
        "no_change": "変更不要",
        "applied": "変更適用",
        "out_of_scope": "範囲外",
        "failed": "失敗",
        "agent_failed": "失敗",
        "apply_failed": "失敗",
        "invalid": "失敗",
        "invalid_response": "失敗",
        "skipped": "スキップ",
    }.get(value, value or "-")


def summarize_self_growth_targets(
    items: List[Dict[str, Any]], feedback_items: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    target_stats: Dict[str, Dict[str, Any]] = {}
    feedback_snapshots = [
        (parse_ts(row.get("ts") or row.get("timestamp")), build_feedback_snapshot(row))
        for row in (feedback_items or [])
        if isinstance(row, dict)
    ]
    feedback_snapshots = [(dt, snap) for dt, snap in feedback_snapshots if dt is not None]

    def ensure_target(label: str) -> Dict[str, Any]:
        return target_stats.setdefault(
            label,
            {
                "label": label,
                "runs": 0,
                "success_runs": 0,
                "measured_runs": 0,
                "improved_runs": 0,
                "worsened_runs": 0,
                "latest_ts": "",
                "latest_patch_status": "",
            },
        )

    for row in items:
        growth_focus = row.get("growth_focus") if isinstance(row.get("growth_focus"), dict) else {}
        labels = growth_focus.get("target_labels") if isinstance(growth_focus.get("target_labels"), list) else []
        normalized_labels = [
            str(label).strip()
            for label in labels
            if str(label).strip()
        ]
        if not normalized_labels:
            continue

        patch_status = str(row.get("patch_status") or "").strip()
        test_status = str(row.get("test_status") or "").strip()
        restart_status = str(row.get("restart_status") or "").strip()
        is_success = (
            patch_status in {"applied", "no_change"}
            and test_status != "failed"
            and restart_status != "failed"
        )

        delta: Optional[Dict[str, Any]] = None
        run_dt = parse_ts(row.get("ts") or row.get("timestamp"))
        if run_dt and feedback_snapshots:
            before = None
            after = None
            for snap_dt, snapshot in feedback_snapshots:
                if snap_dt <= run_dt:
                    before = snapshot
                elif snap_dt > run_dt and after is None:
                    after = snapshot
                    break
            if before and after:
                delta = compute_feedback_delta(before, after)

        timestamp = str(row.get("timestamp") or row.get("ts") or "")
        for label in normalized_labels:
            stats = ensure_target(label)
            stats["runs"] += 1
            if is_success:
                stats["success_runs"] += 1
            if delta:
                stats["measured_runs"] += 1
                if delta["improved"]:
                    stats["improved_runs"] += 1
                if delta["worsened"]:
                    stats["worsened_runs"] += 1
            stats["latest_ts"] = timestamp
            stats["latest_patch_status"] = patch_status

    out: List[Dict[str, Any]] = []
    for row in target_stats.values():
        runs = int(row["runs"])
        success_runs = int(row["success_runs"])
        measured_runs = int(row["measured_runs"])
        improved_runs = int(row["improved_runs"])
        worsened_runs = int(row["worsened_runs"])
        out.append(
            {
                "label": str(row["label"]),
                "runs": runs,
                "success_runs": success_runs,
                "success_rate": round(success_runs / runs, 4) if runs else 0.0,
                "measured_runs": measured_runs,
                "improved_runs": improved_runs,
                "worsened_runs": worsened_runs,
                "improved_rate": round(improved_runs / measured_runs, 4) if measured_runs else 0.0,
                "latest_ts": str(row["latest_ts"]),
                "latest_patch_status": str(row["latest_patch_status"]),
            }
        )
    return sorted(out, key=lambda row: (-int(row["runs"]), str(row["label"])))


def summarize_self_growth(items: List[Dict[str, Any]], feedback_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    if not items:
        return {"runs": 0}
    latest = items[-1]
    patch_status_counts: Dict[str, int] = {}
    scope_blocked_runs = 0
    success_runs = 0
    feedback_snapshots = [
        (parse_ts(row.get("ts") or row.get("timestamp")), build_feedback_snapshot(row))
        for row in (feedback_items or [])
        if isinstance(row, dict)
    ]
    feedback_snapshots = [(dt, snap) for dt, snap in feedback_snapshots if dt is not None]
    measured_runs = 0
    improved_runs = 0
    worsened_runs = 0
    latest_feedback_delta: Dict[str, Any] = {}
    for row in items:
        patch_status = str(row.get("patch_status") or "").strip() or "unknown"
        patch_status_counts[patch_status] = patch_status_counts.get(patch_status, 0) + 1
        if str(row.get("patch_scope_status") or "").strip() == "blocked":
            scope_blocked_runs += 1
        if (
            patch_status in {"applied", "no_change"}
            and str(row.get("test_status") or "").strip() != "failed"
            and str(row.get("restart_status") or "").strip() != "failed"
        ):
            success_runs += 1
        run_dt = parse_ts(row.get("ts") or row.get("timestamp"))
        if not run_dt or not feedback_snapshots:
            continue
        before = None
        after = None
        for snap_dt, snapshot in feedback_snapshots:
            if snap_dt <= run_dt:
                before = snapshot
            elif snap_dt > run_dt and after is None:
                after = snapshot
                break
        if before and after:
            measured_runs += 1
            delta = compute_feedback_delta(before, after)
            if delta["improved"]:
                improved_runs += 1
            if delta["worsened"]:
                worsened_runs += 1
            if row is latest:
                latest_feedback_delta = delta
    growth_focus = latest.get("growth_focus") if isinstance(latest.get("growth_focus"), dict) else {}
    return {
        "runs": len(items),
        "success_runs": success_runs,
        "scope_blocked_runs": scope_blocked_runs,
        "measured_runs": measured_runs,
        "improved_runs": improved_runs,
        "worsened_runs": worsened_runs,
        "patch_status_counts": patch_status_counts,
        "target_stats": summarize_self_growth_targets(items, feedback_items),
        "latest": {
            "timestamp": str(latest.get("timestamp") or ""),
            "patch_status": str(latest.get("patch_status") or "").strip(),
            "patch_scope_status": str(latest.get("patch_scope_status") or "").strip(),
            "test_status": str(latest.get("test_status") or "").strip(),
            "commit_status": str(latest.get("commit_status") or "").strip(),
            "restart_status": str(latest.get("restart_status") or "").strip(),
            "post_eval_status": str(latest.get("post_eval_status") or "").strip(),
            "post_memory_sync_status": str(latest.get("post_memory_sync_status") or "").strip(),
            "touched_files": latest.get("touched_files") if isinstance(latest.get("touched_files"), list) else [],
            "target_labels": growth_focus.get("target_labels") if isinstance(growth_focus.get("target_labels"), list) else [],
            "quality_delta": latest.get("quality_delta") if isinstance(latest.get("quality_delta"), dict) else {},
            "feedback_delta": latest_feedback_delta,
        },
    }


def summarize_ops_from_audit(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    tracked = {
        "minutes_sync.run": "minutes_sync",
        "gmail_triage.run": "gmail_triage",
        "notion_sync.run": "notion_sync",
        "feedback_sync.run": "feedback_sync",
        "self_growth.run": "self_growth",
        "evaluation_harness.run": "evaluation_harness",
        "runbook_drill.run": "runbook_drill",
        "weekly_report.run": "weekly_report",
    }
    out: Dict[str, Dict[str, Any]] = {
        name: {"runs": 0, "errors": 0, "last_ts": "", "last_run_id": ""}
        for name in tracked.values()
    }
    for row in items:
        event_type = str(row.get("event_type") or "")
        name = tracked.get(event_type)
        if not name:
            continue
        bucket = out[name]
        bucket["runs"] += 1
        severity = str(row.get("severity") or "").lower()
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if severity == "error" or (payload and payload.get("ok") is False):
            bucket["errors"] += 1
        ts = str(row.get("ts") or "")
        if ts:
            bucket["last_ts"] = ts
        run_id = str(row.get("run_id") or "")
        if run_id:
            bucket["last_run_id"] = run_id
    return out


def send_slack(webhook_url: str, text: str) -> None:
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def build_markdown(report: Dict[str, Any]) -> str:
    eval_s = report["eval"]
    drill_s = report["drill"]
    ab_s = report["ab"]
    feedback_s = report.get("feedback") or {}
    self_growth_s = report.get("self_growth") or {}
    precision_s = report.get("precision") or {}
    audit_s = report["audit"]
    lines = [
        "# PBS Weekly Ops Report",
        "",
        f"- generated_at: {report['generated_at']}",
        f"- window_days: {report['window_days']}",
        "",
        "## Evaluation Harness",
        f"- runs: {eval_s.get('runs', 0)}",
        f"- failed_runs: {eval_s.get('failed_runs', 0)}",
        f"- pass_rate: {eval_s.get('pass_rate', 0)}",
        f"- avg_failure_rate: {eval_s.get('avg_failure_rate', 0)}",
        f"- avg_p95_ms: {eval_s.get('avg_p95_ms', 0)}",
        "",
        "## Runbook Drill",
        f"- runs: {drill_s.get('runs', 0)}",
        f"- failed_runs: {drill_s.get('failed_runs', 0)}",
        f"- pass_rate: {drill_s.get('pass_rate', 0)}",
        "",
        "## AB Router",
        f"- runs: {ab_s.get('runs', 0)}",
        f"- guard_applied_runs: {ab_s.get('guard_applied_runs', 0)}",
    ]
    arms = (ab_s.get("arms") or {})
    if arms:
        lines.append("- arm breakdown:")
        for arm, row in arms.items():
            lines.append(
                f"  - {arm}: runs={row.get('runs',0)} ok_rate={row.get('ok_rate',0)} avg_elapsed_ms={row.get('avg_elapsed_ms',0)}"
            )
    lines.extend(
        [
            "",
            "## Feedback Loop",
            f"- runs: {feedback_s.get('runs', 0)}",
            f"- reviewed_count: {feedback_s.get('reviewed_count', 0)}",
            f"- actionable_count: {feedback_s.get('actionable_count', 0)}",
            f"- good / bad / missed / pending: {feedback_s.get('good', 0)} / {feedback_s.get('bad', 0)} / {feedback_s.get('missed', 0)} / {feedback_s.get('pending', 0)}",
            "",
        ]
    )
    reason_counts = feedback_s.get("actionable_reason_counts") or {}
    if isinstance(reason_counts, dict) and reason_counts:
        lines.append("- bad reasons:")
        for reason_code, count in sorted(reason_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))):
            lines.append(f"  - {reason_code}: {count}")
        lines.append("")
    improvement_targets = feedback_s.get("improvement_targets") or []
    if isinstance(improvement_targets, list) and improvement_targets:
        lines.append("- improvement targets:")
        for row in improvement_targets[:3]:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or row.get("target") or "-").strip()
            count = int(row.get("count", 0) or 0)
            recommendation = str(row.get("recommendation") or "").strip()
            lines.append(f"  - {label}: {count}")
            if recommendation:
                lines.append(f"    - {recommendation}")
        lines.append("")
    lines.extend(
        [
            "",
            "## Self Growth",
            f"- runs: {self_growth_s.get('runs', 0)}",
            f"- success_runs: {self_growth_s.get('success_runs', 0)}",
            f"- scope_blocked_runs: {self_growth_s.get('scope_blocked_runs', 0)}",
        ]
    )
    patch_counts = self_growth_s.get("patch_status_counts") or {}
    if isinstance(patch_counts, dict) and patch_counts:
        lines.append("- patch statuses:")
        for status, count in sorted(patch_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))):
            lines.append(f"  - {status}: {count}")
    target_stats = self_growth_s.get("target_stats") or []
    if isinstance(target_stats, list) and target_stats:
        lines.append("- target performance:")
        for row in target_stats[:5]:
            if not isinstance(row, dict):
                continue
            lines.append(
                "  - "
                f"{str(row.get('label') or '-').strip()}: "
                f"runs={int(row.get('runs', 0) or 0)} "
                f"success={int(row.get('success_runs', 0) or 0)} "
                f"({float(row.get('success_rate', 0.0) or 0.0):.0%}) "
                f"improved={int(row.get('improved_runs', 0) or 0)}/"
                f"{int(row.get('measured_runs', 0) or 0)} "
                f"latest={humanize_self_growth_patch_status(row.get('latest_patch_status'))}"
            )
    latest_self_growth = self_growth_s.get("latest") or {}
    if isinstance(latest_self_growth, dict) and latest_self_growth:
        lines.append("- latest:")
        lines.append(
            "  - patch/test/restart: "
            f"{latest_self_growth.get('patch_status', '-')} / "
            f"{latest_self_growth.get('test_status', '-')} / "
            f"{latest_self_growth.get('restart_status', '-')}"
        )
        target_labels = latest_self_growth.get("target_labels") or []
        if isinstance(target_labels, list) and target_labels:
            lines.append(f"  - targets: {', '.join(str(item) for item in target_labels[:4])}")
        touched_files = latest_self_growth.get("touched_files") or []
        if isinstance(touched_files, list) and touched_files:
            lines.append(f"  - touched_files: {', '.join(str(item) for item in touched_files[:4])}")
        quality_delta = latest_self_growth.get("quality_delta") or {}
        if isinstance(quality_delta, dict) and quality_delta:
            lines.append(
                "  - quality_delta: "
                f"eval {quality_delta.get('evaluation_failed_before', 0)}→{quality_delta.get('evaluation_failed_after', 0)}, "
                f"drill {quality_delta.get('drill_failed_before', 0)}→{quality_delta.get('drill_failed_after', 0)}, "
                f"unresolved {quality_delta.get('unresolved_before', 0)}→{quality_delta.get('unresolved_after', 0)}"
            )
        feedback_delta = latest_self_growth.get("feedback_delta") or {}
        if isinstance(feedback_delta, dict) and feedback_delta:
            lines.append(
                "  - feedback_delta: "
                f"good {feedback_delta.get('good_before', 0)}→{feedback_delta.get('good_after', 0)}, "
                f"bad {feedback_delta.get('bad_before', 0)}→{feedback_delta.get('bad_after', 0)}, "
                f"missed {feedback_delta.get('missed_before', 0)}→{feedback_delta.get('missed_after', 0)}"
            )
            lines.append(
                "  - feedback_effect: "
                f"improved={feedback_delta.get('improved', False)} "
                f"worsened={feedback_delta.get('worsened', False)}"
            )
    precision_overall = precision_s.get("overall") or {}
    precision_gmail = precision_s.get("gmail") or {}
    precision_minutes = precision_s.get("minutes") or {}
    if precision_overall or precision_gmail or precision_minutes:
        lines.extend(
            [
                "",
                "## Precision Metrics",
                f"- overall precision: {precision_overall.get('precision', 0)}",
                f"- overall recall: {precision_overall.get('recall', 0)}"
                + (" (暫定)" if precision_overall.get("recall_provisional") else ""),
                f"- overall usefulness: {precision_overall.get('usefulness', 0)}",
                f"- overall review_coverage: {precision_overall.get('review_coverage', 0)}",
                "",
                "### Gmail",
                f"- precision: {precision_gmail.get('precision', 0)}",
                f"- recall: {precision_gmail.get('recall', 0)}"
                + (" (暫定)" if precision_gmail.get("recall_provisional") else ""),
                f"- usefulness: {precision_gmail.get('usefulness', 0)}",
                f"- reviewed_items: {precision_gmail.get('reviewed_items', 0)}",
                f"- curated_coverage: {precision_gmail.get('curated_coverage', 0)}",
                "",
                "### Minutes",
                f"- precision: {precision_minutes.get('precision', 0)}",
                f"- recall: {precision_minutes.get('recall', 0)}"
                + (" (暫定)" if precision_minutes.get("recall_provisional") else ""),
                f"- usefulness: {precision_minutes.get('usefulness', 0)}",
                f"- reviewed_items: {precision_minutes.get('reviewed_items', 0)}",
                f"- curated_coverage: {precision_minutes.get('curated_coverage', 0)}",
                "",
            ]
        )
    lines.append("")
    lines.extend(
        [
            "",
            "## Immutable Audit",
            f"- ok: {audit_s.get('ok', False)}",
            f"- files: {audit_s.get('files', 0)}",
            f"- errors: {audit_s.get('errors', 0)}",
            "",
        ]
    )
    freshness = report.get("freshness", {})
    if freshness.get("present"):
        lines.extend(
            [
                "## Pipeline Freshness",
                f"- ok: {freshness.get('ok', False)}",
                f"- stale_count: {freshness.get('stale_count', 0)}",
                f"- stale_components: {', '.join(freshness.get('stale_components', [])) or '-'}",
                "",
            ]
        )
    ops = report.get("ops", {})
    if ops:
        lines.extend(["## Pipeline Operations (from audit)"])
        for name, row in ops.items():
            lines.append(
                f"- {name}: runs={row.get('runs',0)} errors={row.get('errors',0)} "
                f"last_ts={row.get('last_ts','-')}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()

    env = load_env()
    since = datetime.now(timezone.utc) - timedelta(days=max(args.days, 1))

    eval_items = in_window(read_jsonl(EVAL_HISTORY), since)
    drill_items = in_window(read_jsonl(DRILL_HISTORY), since)
    ab_items = in_window(read_jsonl(AB_HISTORY), since)
    feedback_items = in_window(read_jsonl(FEEDBACK_HISTORY), since)
    self_growth_items = in_window(read_jsonl(SELF_GROWTH_HISTORY), since)
    audit_events = in_window(read_jsonl(AUDIT_FILE), since)
    audit_report = verify_audit([AUDIT_FILE])
    precision_latest = read_json_file(PRECISION_METRICS_LATEST)

    report = {
        "generated_at": datetime.now(JST).isoformat(),
        "window_days": max(args.days, 1),
        "eval": summarize_eval(eval_items),
        "drill": summarize_drill(drill_items),
        "ab": summarize_ab(ab_items),
        "feedback": summarize_feedback(feedback_items),
        "self_growth": summarize_self_growth(self_growth_items, feedback_items),
        "precision": precision_latest or {},
        "audit": audit_report,
        "ops": summarize_ops_from_audit(audit_events),
    }
    report["freshness"] = summarize_freshness_from_drill(report["drill"])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_MD.write_text(build_markdown(report), encoding="utf-8")
    with HISTORY_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")

    webhook = env.get("SLACK_WEBHOOK_URL", "").strip()
    notify_on_schedule = str(env.get("ROBY_WEEKLY_REPORT_NOTIFY", "1")).strip() == "1"
    if webhook and (args.notify or notify_on_schedule):
        text = format_weekly_slack(report)
        try:
            send_slack(webhook, text[:3500])
            report["slack_notified"] = True
        except Exception as exc:
            report["slack_notified"] = False
            report["slack_error"] = str(exc)
            LATEST_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        report["slack_notified"] = False

    if env.get("ROBY_IMMUTABLE_AUDIT", "1") == "1":
        try:
            eval_latest_ok = bool((report.get("eval", {}).get("latest") or {}).get("all_ok", True))
            drill_latest_ok = bool((report.get("drill", {}).get("latest") or {}).get("all_ok", True))
            audit_ok = bool(report.get("audit", {}).get("ok", False))
            append_audit_event(
                "weekly_report.run",
                {
                    "window_days": int(report["window_days"]),
                    "eval_runs": int(report["eval"].get("runs", 0)),
                    "eval_failed_runs": int(report["eval"].get("failed_runs", 0)),
                    "drill_runs": int(report["drill"].get("runs", 0)),
                    "drill_failed_runs": int(report["drill"].get("failed_runs", 0)),
                    "audit_ok": bool(report["audit"].get("ok", False)),
                    "audit_errors": int(report["audit"].get("errors", 0)),
                    "freshness": {
                        "present": bool(report.get("freshness", {}).get("present", False)),
                        "ok": bool(report.get("freshness", {}).get("ok", False)),
                        "stale_count": int(report.get("freshness", {}).get("stale_count", 0)),
                        "stale_components": list(report.get("freshness", {}).get("stale_components", [])),
                    },
                    "ops": {
                        "minutes_sync_runs": int(report["ops"].get("minutes_sync", {}).get("runs", 0)),
                        "gmail_triage_runs": int(report["ops"].get("gmail_triage", {}).get("runs", 0)),
                        "notion_sync_runs": int(report["ops"].get("notion_sync", {}).get("runs", 0)),
                        "feedback_sync_runs": int(report["ops"].get("feedback_sync", {}).get("runs", 0)),
                        "self_growth_runs": int(report["ops"].get("self_growth", {}).get("runs", 0)),
                        "evaluation_harness_runs": int(report["ops"].get("evaluation_harness", {}).get("runs", 0)),
                        "runbook_drill_runs": int(report["ops"].get("runbook_drill", {}).get("runs", 0)),
                    },
                    "feedback": {
                        "runs": int(report.get("feedback", {}).get("runs", 0)),
                        "reviewed_count": int(report.get("feedback", {}).get("reviewed_count", 0)),
                        "actionable_count": int(report.get("feedback", {}).get("actionable_count", 0)),
                    },
                    "self_growth": {
                        "runs": int(report.get("self_growth", {}).get("runs", 0)),
                        "success_runs": int(report.get("self_growth", {}).get("success_runs", 0)),
                        "scope_blocked_runs": int(report.get("self_growth", {}).get("scope_blocked_runs", 0)),
                    },
                    "ab": {
                        "runs": int(report.get("ab", {}).get("runs", 0)),
                        "guard_applied_runs": int(report.get("ab", {}).get("guard_applied_runs", 0)),
                    },
                    "slack_notified": bool(report.get("slack_notified", False)),
                    "slack_error": str(report.get("slack_error", "")),
                },
                source="roby-weekly-report",
                run_id=str(report["generated_at"]),
                severity="error" if (not eval_latest_ok or not drill_latest_ok or not audit_ok) else "info",
            )
        except Exception:
            pass

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(
            f"[weekly] eval_runs={report['eval'].get('runs',0)} "
            f"drill_runs={report['drill'].get('runs',0)} "
            f"audit_ok={report['audit'].get('ok', False)}"
        )
        print(f"[weekly] latest_json={LATEST_JSON}")
        print(f"[weekly] latest_md={LATEST_MD}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

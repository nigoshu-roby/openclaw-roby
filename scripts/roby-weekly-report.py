#!/usr/bin/env python3
"""Generate weekly PBS ops report from local run artifacts."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from roby_audit import append_audit_event, verify_audit

JST = timezone(timedelta(hours=9))
ENV_PATH = Path.home() / ".openclaw" / ".env"
STATE_ROOT = Path.home() / ".openclaw" / "roby"
REPORT_DIR = STATE_ROOT / "reports"
LATEST_JSON = REPORT_DIR / "weekly_latest.json"
LATEST_MD = REPORT_DIR / "weekly_latest.md"
HISTORY_JSONL = REPORT_DIR / "weekly_history.jsonl"

EVAL_HISTORY = STATE_ROOT / "evals" / "history.jsonl"
DRILL_HISTORY = STATE_ROOT / "drills" / "history.jsonl"
AB_HISTORY = STATE_ROOT / "ab_router_runs.jsonl"
AUDIT_FILE = STATE_ROOT / "audit" / "events.jsonl"


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


def summarize_ops_from_audit(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    tracked = {
        "minutes_sync.run": "minutes_sync",
        "gmail_triage.run": "gmail_triage",
        "notion_sync.run": "notion_sync",
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
    audit_events = in_window(read_jsonl(AUDIT_FILE), since)
    audit_report = verify_audit([AUDIT_FILE])

    report = {
        "generated_at": datetime.now(JST).isoformat(),
        "window_days": max(args.days, 1),
        "eval": summarize_eval(eval_items),
        "drill": summarize_drill(drill_items),
        "ab": summarize_ab(ab_items),
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
        text = "\n".join(
            [
                "【PBS 週次運用レポート】",
                f"・生成時刻: {report['generated_at']}",
                "",
                "■品質ゲート",
                f"・Evaluation: runs={report['eval'].get('runs',0)} / failed={report['eval'].get('failed_runs',0)}",
                f"・Runbook Drill: runs={report['drill'].get('runs',0)} / failed={report['drill'].get('failed_runs',0)}",
                f"・監査整合性: ok={report['audit'].get('ok', False)} / errors={report['audit'].get('errors', 0)}",
                f"・鮮度stale件数: {report.get('freshness',{}).get('stale_count',0)}",
                "",
                "■運用実行数（7日）",
                f"・minutes_sync: {report['ops'].get('minutes_sync',{}).get('runs',0)}",
                f"・gmail_triage: {report['ops'].get('gmail_triage',{}).get('runs',0)}",
                f"・notion_sync: {report['ops'].get('notion_sync',{}).get('runs',0)}",
                f"・self_growth: {report['ops'].get('self_growth',{}).get('runs',0)}",
                "",
                "■AB Router",
                f"・runs: {report.get('ab',{}).get('runs',0)}",
                f"・guard_applied_runs: {report.get('ab',{}).get('guard_applied_runs',0)}",
            ]
        )
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
                        "self_growth_runs": int(report["ops"].get("self_growth", {}).get("runs", 0)),
                        "evaluation_harness_runs": int(report["ops"].get("evaluation_harness", {}).get("runs", 0)),
                        "runbook_drill_runs": int(report["ops"].get("runbook_drill", {}).get("runs", 0)),
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

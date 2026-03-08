#!/usr/bin/env python3
"""Sync Neuronic feedback signals into local PBS state."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from roby_audit import append_audit_event

JST = timezone(timedelta(hours=9))
ENV_PATH = Path.home() / ".openclaw" / ".env"
STATE_ROOT = Path.home() / ".openclaw" / "roby"
STATE_PATH = STATE_ROOT / "feedback_sync_state.json"
RUN_LOG_PATH = STATE_ROOT / "feedback_sync_runs.jsonl"
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
REVIEWED_STATES = {"good", "bad", "missed"}
ACTIONABLE_STATES = {"bad", "missed"}


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


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except Exception:
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


def build_neuronic_base_url(env: Dict[str, str]) -> str:
    base = (env.get("NEURONIC_API_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/")
    for key in ("NEURONIC_URL", "NEURONIC_FALLBACK_URL", "ROBY_NEURONIC_URL"):
        raw = (env.get(key) or "").strip()
        if not raw:
            continue
        if "/api/v1/tasks/" in raw:
            return raw.split("/api/v1/tasks/", 1)[0] + "/api/v1"
        if raw.endswith("/api/v1/tasks"):
            return raw.rstrip("/")
    return "http://127.0.0.1:5174/api/v1"


def build_headers(env: Dict[str, str]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = (env.get("NEURONIC_TOKEN") or env.get("TASKD_AUTH_TOKEN") or "").strip()
    if token:
        header_name = (env.get("NEURONIC_AUTH_HEADER") or "Authorization").strip() or "Authorization"
        if header_name.lower() == "authorization":
            headers[header_name] = f"Bearer {token}"
        else:
            headers[header_name] = token
    return headers


def fetch_tasks_page(
    base_url: str,
    headers: Dict[str, str],
    *,
    limit: int,
    offset: int,
) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    query = urllib.parse.urlencode({"limit": limit, "offset": offset})
    req = urllib.request.Request(f"{base_url}/tasks?{query}", headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", "ignore")
    payload = json.loads(body) if body else {}
    if not isinstance(payload, dict):
        return [], None
    items = payload.get("items")
    if not isinstance(items, list):
        items = []
    normalized = [row for row in items if isinstance(row, dict)]
    total = payload.get("total")
    try:
        total_int = int(total) if total is not None else None
    except Exception:
        total_int = None
    return normalized, total_int


def fetch_all_roby_tasks(
    env: Dict[str, str],
    *,
    limit: int,
    max_pages: int,
) -> Tuple[List[Dict[str, Any]], str]:
    base_url = build_neuronic_base_url(env)
    headers = build_headers(env)
    collected: List[Dict[str, Any]] = []
    offset = 0
    page = 0
    total_hint: Optional[int] = None
    while page < max_pages:
        items, total = fetch_tasks_page(base_url, headers, limit=limit, offset=offset)
        if total is not None:
            total_hint = total
        if not items:
            break
        for row in items:
            if str(row.get("source") or "").strip() == "roby":
                collected.append(row)
        offset += len(items)
        page += 1
        if len(items) < limit:
            break
        if total_hint is not None and offset >= total_hint:
            break
    return collected, base_url


def normalize_feedback_state(task: Dict[str, Any]) -> str:
    state = str(task.get("feedback_state") or task.get("feedbackState") or "pending").strip().lower()
    return state or "pending"


def summarize_feedback(tasks: List[Dict[str, Any]], recent_limit: int) -> Dict[str, Any]:
    counts = {"good": 0, "bad": 0, "missed": 0, "pending": 0, "other": 0}
    actionable_reasons: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []
    for task in tasks:
        state = normalize_feedback_state(task)
        reason_code = str(task.get("feedback_reason_code") or task.get("feedbackReasonCode") or "").strip().lower()
        if state in counts:
            counts[state] += 1
        else:
            counts["other"] += 1
        if state in ACTIONABLE_STATES and reason_code:
            actionable_reasons[reason_code] = actionable_reasons.get(reason_code, 0) + 1
        rows.append(
            {
                "id": str(task.get("id") or "").strip(),
                "title": str(task.get("title") or "").strip(),
                "status": str(task.get("status") or "").strip(),
                "origin_id": str(task.get("origin_id") or "").strip(),
                "feedback_state": state,
                "feedback_reason_code": reason_code,
                "updated_at": str(task.get("updated_at") or "").strip(),
                "created_at": str(task.get("created_at") or "").strip(),
            }
        )

    def _sort_key(row: Dict[str, Any]) -> Tuple[int, str]:
        dt = parse_timestamp(row.get("updated_at")) or parse_timestamp(row.get("created_at"))
        return (int(dt.timestamp()) if dt else 0, str(row.get("id") or ""))

    rows.sort(key=_sort_key, reverse=True)
    recent_reviewed = [row for row in rows if row["feedback_state"] in REVIEWED_STATES][:recent_limit]
    recent_actionable = [row for row in rows if row["feedback_state"] in ACTIONABLE_STATES][:recent_limit]
    reviewed_count = counts["good"] + counts["bad"] + counts["missed"]
    actionable_count = counts["bad"] + counts["missed"]

    return {
        "total_tasks": len(tasks),
        "reviewed_count": reviewed_count,
        "actionable_count": actionable_count,
        "counts": counts,
        "actionable_reason_counts": dict(sorted(actionable_reasons.items(), key=lambda item: (-item[1], item[0]))),
        "recent_reviewed": recent_reviewed,
        "recent_actionable": recent_actionable,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--recent", type=int, default=8)
    args = parser.parse_args()

    env = load_env()
    now = datetime.now(JST).isoformat()
    run: Dict[str, Any] = {
        "ts": now,
        "ok": False,
        "dry_run": bool(args.dry_run),
        "summary": {},
    }

    try:
        tasks, base_url = fetch_all_roby_tasks(env, limit=max(args.limit, 1), max_pages=max(args.max_pages, 1))
        summary = summarize_feedback(tasks, max(args.recent, 1))
        run["ok"] = True
        run["base_url"] = base_url
        run["summary"] = summary
        state_payload = {
            "updated_at": now,
            "base_url": base_url,
            "summary": summary,
        }
        if not args.dry_run:
            STATE_ROOT.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            append_jsonl(RUN_LOG_PATH, run)
        append_audit_event(
            "feedback_sync.run",
            {
                "ok": True,
                "dry_run": bool(args.dry_run),
                "total_tasks": int(summary.get("total_tasks", 0)),
                "reviewed_count": int(summary.get("reviewed_count", 0)),
                "actionable_count": int(summary.get("actionable_count", 0)),
                "counts": summary.get("counts", {}),
            },
            source="roby-feedback-sync",
            run_id=now,
            severity="info",
        )
        if args.json:
            print(json.dumps({"updated_at": now, "summary": summary}, ensure_ascii=False))
        else:
            print(
                f"[feedback_sync] total={summary.get('total_tasks', 0)} "
                f"reviewed={summary.get('reviewed_count', 0)} actionable={summary.get('actionable_count', 0)}"
            )
            print(f"[feedback_sync] state={STATE_PATH}")
        return 0
    except Exception as exc:
        run["error"] = str(exc)
        if not args.dry_run:
            append_jsonl(RUN_LOG_PATH, run)
        append_audit_event(
            "feedback_sync.run",
            {"ok": False, "dry_run": bool(args.dry_run), "error": str(exc)},
            source="roby-feedback-sync",
            run_id=now,
            severity="error",
        )
        if args.json:
            print(json.dumps(run, ensure_ascii=False))
        else:
            print(f"[feedback_sync] error={exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

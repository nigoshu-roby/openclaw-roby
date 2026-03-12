#!/usr/bin/env python3
"""Build local Gmail golden/missed sets from Neuronic feedback and manifest history."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from roby_audit import append_audit_event

ENV_PATH = Path.home() / ".openclaw" / ".env"
STATE_ROOT = Path.home() / ".openclaw" / "roby"
CANDIDATES_PATH = STATE_ROOT / "feedback_candidates.jsonl"
GOLDEN_PATH = STATE_ROOT / "gmail_golden_set.json"
MISSED_PATH = STATE_ROOT / "gmail_missed_set.json"
SUMMARY_PATH = STATE_ROOT / "gmail_eval_corpus_summary.json"
RUN_LOG_PATH = STATE_ROOT / "gmail_eval_corpus_runs.jsonl"
MANUAL_MISSED_PATH = STATE_ROOT / "gmail_missed_manual.jsonl"
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


def load_env() -> Dict[str, str]:
    env = dict(os.environ)
    env_file = Path(env.get("ROBY_ENV_FILE", str(ENV_PATH))).expanduser()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key and key not in env:
                env[key] = value
    for key in KEYCHAIN_SECRET_KEYS:
        if env.get(key):
            continue
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "roby-pbs", "-a", key, "-w"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                env[key] = result.stdout.strip()
        except Exception:
            pass
    return env


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


def fetch_tasks_page(base_url: str, headers: Dict[str, str], *, limit: int, offset: int) -> Tuple[List[Dict[str, Any]], Optional[int]]:
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


def fetch_all_roby_tasks(env: Dict[str, str], *, limit: int, max_pages: int) -> Tuple[List[Dict[str, Any]], str]:
    base_url = build_neuronic_base_url(env)
    headers = build_headers(env)
    collected: List[Dict[str, Any]] = []
    offset = 0
    total_hint: Optional[int] = None
    for _page in range(max_pages):
        items, total = fetch_tasks_page(base_url, headers, limit=limit, offset=offset)
        if total is not None:
            total_hint = total
        if not items:
            break
        for row in items:
            if str(row.get("source") or "").strip() == "roby":
                collected.append(row)
        offset += len(items)
        if len(items) < limit:
            break
        if total_hint is not None and offset >= total_hint:
            break
    return collected, base_url


def normalize_feedback_state(task: Dict[str, Any]) -> str:
    state = str(task.get("feedback_state") or task.get("feedbackState") or "pending").strip().lower()
    return state or "pending"


def read_feedback_candidate_index(path: Path) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return latest
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except Exception:
            continue
        if row.get("event") != "feedback_candidates":
            continue
        run_id = str(row.get("run_id") or "")
        ts = row.get("timestamp") or row.get("ts")
        for item in row.get("items") or []:
            if not isinstance(item, dict):
                continue
            origin_id = str(item.get("origin_id") or "").strip()
            if not origin_id:
                continue
            payload = dict(item)
            payload["run_id"] = run_id
            payload["timestamp"] = ts
            latest[origin_id] = payload
    return latest


def is_gmail_candidate(candidate: Dict[str, Any]) -> bool:
    run_id = str(candidate.get("run_id") or "")
    if run_id.startswith("roby:gmail:"):
        return True
    project = str(candidate.get("project") or "").strip().lower()
    return project == "email"


def parse_sender_label(title: str) -> str:
    text = str(title or "").strip()
    if text.startswith("【") and "】" in text:
        return text[1 : text.index("】")].strip()
    return ""


def extract_task_type(task: Dict[str, Any]) -> str:
    tags = task.get("tags") or []
    if not isinstance(tags, list):
        return ""
    for tag in tags:
        value = str(tag or "").strip().lower()
        if value.startswith("task_type:"):
            return value.split(":", 1)[1]
    return ""


def extract_bucket(task: Dict[str, Any]) -> str:
    tags = task.get("tags") or []
    if not isinstance(tags, list):
        return ""
    for tag in tags:
        value = str(tag or "").strip().lower()
        if value.startswith("category:"):
            return value.split(":", 1)[1]
    return ""


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_gmail_review_entries(tasks: List[Dict[str, Any]], candidate_index: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for task in tasks:
        origin_id = str(task.get("origin_id") or "").strip()
        if not origin_id:
            continue
        candidate = candidate_index.get(origin_id)
        if not candidate or not is_gmail_candidate(candidate):
            continue
        feedback_state = normalize_feedback_state(task)
        entry = {
            "origin_id": origin_id,
            "task_id": task.get("id"),
            "title": task.get("title") or candidate.get("title") or "",
            "sender_label": parse_sender_label(task.get("title") or candidate.get("title") or ""),
            "project": candidate.get("project") or task.get("project") or "",
            "parent_origin_id": candidate.get("parent_origin_id"),
            "source_doc_id": candidate.get("source_doc_id") or "",
            "source_doc_title": candidate.get("source_doc_title") or "",
            "source_run_id": candidate.get("run_id") or "",
            "feedback_state": feedback_state,
            "feedback_reason_code": task.get("feedback_reason_code") or task.get("feedbackReasonCode") or None,
            "updated_at": task.get("updated_at") or task.get("updatedAt"),
            "created_at": task.get("created_at") or task.get("createdAt"),
            "status": task.get("status") or "",
            "task_type": extract_task_type(task),
            "work_bucket": extract_bucket(task),
        }
        entries.append(entry)
    entries.sort(key=lambda row: (str(row.get("source_doc_title") or ""), str(row.get("parent_origin_id") or ""), str(row.get("title") or "")))
    return entries


def build_golden_payload(entries: List[Dict[str, Any]], *, base_url: str) -> Dict[str, Any]:
    good_items = [row for row in entries if row.get("feedback_state") == "good"]
    senders = sorted({str(row.get("sender_label") or "") for row in good_items if row.get("sender_label")})
    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "kind": "gmail_golden_set",
        "summary": {
            "items": len(good_items),
            "senders": len(senders),
            "note": "good 評価のメール由来タスクを初期 golden set として収集。後続の C1 で代表ケースに絞り込む。",
        },
        "source": {
            "neuronic_base_url": base_url,
            "feedback_candidates": str(CANDIDATES_PATH),
        },
        "items": good_items,
    }


def build_missed_payload(entries: List[Dict[str, Any]], *, base_url: str) -> Dict[str, Any]:
    missed_items = [row for row in entries if row.get("feedback_state") == "missed"]
    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "kind": "gmail_missed_set",
        "summary": {
            "items": len(missed_items),
            "note": "missed は『本来タスク化すべきだったが漏れたメール task』の集合。A6/C1 で false negative を育てる。",
        },
        "source": {
            "neuronic_base_url": base_url,
            "feedback_candidates": str(CANDIDATES_PATH),
        },
        "manual_entry_template": {
            "source_doc_id": "",
            "source_doc_title": "",
            "sender_label": "",
            "expected_bucket": "task",
            "expected_title": "",
            "expected_task_type": "reply",
            "reason": "",
        },
        "items": missed_items,
    }


def read_manual_missed_entries(path: Path) -> List[Dict[str, Any]]:
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
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "origin_id": str(row.get("origin_id") or "").strip() or f"manual:{len(rows)+1}",
                "task_id": row.get("task_id"),
                "title": str(row.get("expected_title") or row.get("title") or "").strip(),
                "sender_label": str(row.get("sender_label") or "").strip(),
                "project": str(row.get("project") or "email").strip() or "email",
                "parent_origin_id": row.get("parent_origin_id"),
                "source_doc_id": str(row.get("source_doc_id") or "").strip(),
                "source_doc_title": str(row.get("source_doc_title") or "").strip(),
                "source_run_id": str(row.get("source_run_id") or "manual").strip(),
                "feedback_state": "missed",
                "feedback_reason_code": str(row.get("feedback_reason_code") or row.get("reason_code") or "manual_missed_capture").strip(),
                "updated_at": row.get("updated_at"),
                "created_at": row.get("created_at"),
                "status": str(row.get("status") or "manual").strip(),
                "task_type": str(row.get("expected_task_type") or row.get("task_type") or "").strip(),
                "work_bucket": str(row.get("expected_bucket") or row.get("work_bucket") or "task").strip(),
                "capture_source": "manual",
                "reason": str(row.get("reason") or "").strip(),
            }
        )
    return rows


def merge_missed_entries(entries: List[Dict[str, Any]], manual_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*entries, *manual_entries]:
        key = str(row.get("origin_id") or "").strip()
        if not key:
            key = f"{row.get('source_doc_id','')}|{row.get('title','')}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def build_summary(entries: List[Dict[str, Any]], *, base_url: str) -> Dict[str, Any]:
    counts = Counter(str(row.get("feedback_state") or "pending") for row in entries)
    reason_counts = Counter(str(row.get("feedback_reason_code") or "") for row in entries if row.get("feedback_reason_code"))
    bucket_counts = Counter(str(row.get("work_bucket") or "") for row in entries if row.get("work_bucket"))
    task_type_counts = Counter(str(row.get("task_type") or "") for row in entries if row.get("task_type"))
    senders = Counter(str(row.get("sender_label") or "") for row in entries if row.get("sender_label"))
    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "kind": "gmail_eval_corpus_summary",
        "reviewed_items": len(entries),
        "counts": dict(counts),
        "work_bucket_counts": dict(bucket_counts),
        "task_type_counts": dict(task_type_counts),
        "top_feedback_reasons": [{"reason_code": key, "count": value} for key, value in reason_counts.most_common(10)],
        "top_senders": [{"sender_label": key, "count": value} for key, value in senders.most_common(10)],
        "paths": {
            "golden": str(GOLDEN_PATH),
            "missed": str(MISSED_PATH),
            "summary": str(SUMMARY_PATH),
            "feedback_candidates": str(CANDIDATES_PATH),
        },
        "source": {
            "neuronic_base_url": base_url,
        },
    }


def log_run(entry: Dict[str, Any]) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def build_run_entry(summary: Dict[str, Any], *, base_url: str) -> Dict[str, Any]:
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    return {
        "ts": iso_now(),
        "event": "gmail_eval_corpus",
        "reviewed_items": summary.get("reviewed_items", 0),
        "good_items": counts.get("good", 0),
        "missed_items": counts.get("missed", 0),
        "bad_items": counts.get("bad", 0),
        "neuronic_base_url": base_url,
        "summary_path": str(SUMMARY_PATH),
        "golden_path": str(GOLDEN_PATH),
        "missed_path": str(MISSED_PATH),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    env = load_env()
    tasks, base_url = fetch_all_roby_tasks(env, limit=max(1, args.limit), max_pages=max(1, args.max_pages))
    candidate_index = read_feedback_candidate_index(CANDIDATES_PATH)
    entries = build_gmail_review_entries(tasks, candidate_index)
    manual_missed_entries = read_manual_missed_entries(MANUAL_MISSED_PATH)
    golden = build_golden_payload(entries, base_url=base_url)
    missed = build_missed_payload(merge_missed_entries(entries, manual_missed_entries), base_url=base_url)
    summary = build_summary(entries, base_url=base_url)
    summary["manual_missed_entries"] = len(manual_missed_entries)
    summary["paths"]["manual_missed"] = str(MANUAL_MISSED_PATH)

    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(golden, ensure_ascii=False, indent=2), encoding="utf-8")
    MISSED_PATH.write_text(json.dumps(missed, ensure_ascii=False, indent=2), encoding="utf-8")
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    run_entry = build_run_entry(summary, base_url=base_url)
    log_run(run_entry)
    append_audit_event(
        "gmail_eval_corpus.run",
        {
            "status": "ok",
            "reviewed_items": summary.get("reviewed_items", 0),
            "good_items": summary.get("counts", {}).get("good", 0),
            "missed_items": summary.get("counts", {}).get("missed", 0),
        },
        source="roby-gmail-eval-corpus",
        severity="info",
    )

    payload = {
        "reviewed_items": summary.get("reviewed_items", 0),
        "golden_items": golden.get("summary", {}).get("items", 0),
        "missed_items": missed.get("summary", {}).get("items", 0),
        "top_feedback_reasons": summary.get("top_feedback_reasons", [])[:5],
        "top_senders": summary.get("top_senders", [])[:5],
        "manual_missed_entries": summary.get("manual_missed_entries", 0),
        "summary_path": str(SUMMARY_PATH),
        "golden_path": str(GOLDEN_PATH),
        "missed_path": str(MISSED_PATH),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

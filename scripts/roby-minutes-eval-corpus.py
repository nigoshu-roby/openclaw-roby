#!/usr/bin/env python3
"""Build local minutes golden/missed sets from Neuronic feedback and manifest history."""

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
GOLDEN_PATH = STATE_ROOT / "minutes_golden_set.json"
MISSED_PATH = STATE_ROOT / "minutes_missed_set.json"
MANUAL_MISSED_PATH = STATE_ROOT / "minutes_missed_manual.jsonl"
SUMMARY_PATH = STATE_ROOT / "minutes_eval_corpus_summary.json"
RUN_LOG_PATH = STATE_ROOT / "minutes_eval_corpus_runs.jsonl"
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


def is_minutes_candidate(candidate: Dict[str, Any]) -> bool:
    run_id = str(candidate.get("run_id") or "")
    if run_id.startswith("roby:minutes:"):
        return True
    source_title = str(candidate.get("source_doc_title") or "")
    if "社内定例" in source_title or "Gemini によるメモ" in source_title:
        return True
    return False


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def build_minutes_review_entries(tasks: List[Dict[str, Any]], candidate_index: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for task in tasks:
        origin_id = str(task.get("origin_id") or "").strip()
        if not origin_id:
            continue
        candidate = candidate_index.get(origin_id)
        if not candidate or not is_minutes_candidate(candidate):
            continue
        feedback_state = normalize_feedback_state(task)
        entries.append(
            {
                "origin_id": origin_id,
                "task_id": task.get("id"),
                "title": task.get("title") or candidate.get("title") or "",
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
            }
        )
    entries.sort(key=lambda row: (str(row.get("source_doc_title") or ""), str(row.get("project") or ""), str(row.get("title") or "")))
    return entries


def build_golden_payload(entries: List[Dict[str, Any]], *, base_url: str) -> Dict[str, Any]:
    good_items = [row for row in entries if row.get("feedback_state") == "good"]
    source_docs = sorted({str(row.get("source_doc_id") or "") for row in good_items if row.get("source_doc_id")})
    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "kind": "minutes_golden_set",
        "summary": {
            "items": len(good_items),
            "source_docs": len(source_docs),
            "note": "good 評価の議事録タスクを初期 golden set として収集。後続の C2 で代表ケースに絞り込む。",
        },
        "source": {
            "neuronic_base_url": base_url,
            "feedback_candidates": str(CANDIDATES_PATH),
        },
        "items": good_items,
    }


def build_manual_missed_items(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in read_rows(path):
        expected_title = str(row.get("expected_title") or "").strip()
        if not expected_title:
            continue
        items.append(
            {
                "origin_id": str(row.get("origin_id") or row.get("id") or "").strip(),
                "task_id": None,
                "title": expected_title,
                "project": str(row.get("project") or "").strip(),
                "parent_origin_id": None,
                "source_doc_id": str(row.get("source_doc_id") or "").strip(),
                "source_doc_title": str(row.get("source_doc_title") or "").strip(),
                "source_run_id": "manual:minutes:missed",
                "feedback_state": "missed",
                "feedback_reason_code": str(row.get("reason_code") or "manual_missed_capture").strip(),
                "updated_at": row.get("ts"),
                "created_at": row.get("ts"),
                "status": "manual_missed",
                "expected_subtasks": row.get("expected_subtasks") or [],
                "reason": str(row.get("reason") or "").strip(),
                "manual": True,
            }
        )
    return items


def build_missed_payload(entries: List[Dict[str, Any]], *, base_url: str, manual_missed_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    missed_items = [row for row in entries if row.get("feedback_state") == "missed"]
    if manual_missed_items:
        missed_items = missed_items + manual_missed_items
    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "kind": "minutes_missed_set",
        "summary": {
            "items": len(missed_items),
            "note": "missed は『本来抽出すべきだったが漏れたタスク』の集合。現状は手動追加 or missed 評価の蓄積で増やす。",
        },
        "source": {
            "neuronic_base_url": base_url,
            "feedback_candidates": str(CANDIDATES_PATH),
            "manual_missed": str(MANUAL_MISSED_PATH),
        },
        "manual_entry_template": {
            "source_doc_id": "",
            "source_doc_title": "",
            "project": "",
            "expected_title": "",
            "expected_subtasks": [],
            "reason": "",
        },
        "items": missed_items,
    }


def build_summary(entries: List[Dict[str, Any]], *, base_url: str, manual_missed_count: int) -> Dict[str, Any]:
    counts = Counter(str(row.get("feedback_state") or "pending") for row in entries)
    by_project = Counter(str(row.get("project") or "") for row in entries if row.get("project"))
    by_reason = Counter(str(row.get("feedback_reason_code") or "") for row in entries if row.get("feedback_reason_code"))
    source_docs = Counter(str(row.get("source_doc_title") or "") for row in entries if row.get("source_doc_title"))
    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "reviewed_minutes_tasks": len(entries),
        "counts": dict(counts),
        "top_projects": [{"project": key, "count": value} for key, value in by_project.most_common(10)],
        "top_feedback_reasons": [{"reason_code": key, "count": value} for key, value in by_reason.most_common(10)],
        "top_source_docs": [{"title": key, "count": value} for key, value in source_docs.most_common(10)],
        "manual_missed_count": manual_missed_count,
        "paths": {
            "golden": str(GOLDEN_PATH),
            "missed": str(MISSED_PATH),
        },
        "source": {
            "neuronic_base_url": base_url,
            "feedback_candidates": str(CANDIDATES_PATH),
        },
        "next_step": "Sprint C で representative 20〜30件へ絞り込み、false negative を missed set に手動追加する。",
    }


def write_json(path: Path, payload: Dict[str, Any], *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_run_entry(*, base_url: str, entries: List[Dict[str, Any]], golden: Dict[str, Any], missed: Dict[str, Any], dry_run: bool, manual_missed_count: int) -> Dict[str, Any]:
    counts = Counter(str(row.get("feedback_state") or "pending") for row in entries)
    return {
        "event": "minutes_eval_corpus",
        "timestamp": iso_now(),
        "dry_run": dry_run,
        "neuronic_base_url": base_url,
        "reviewed_minutes_tasks": len(entries),
        "counts": dict(counts),
        "golden_items": len(golden.get("items") or []),
        "missed_items": len(missed.get("items") or []),
        "manual_missed_items": manual_missed_count,
        "outputs": {
            "golden": str(GOLDEN_PATH),
            "missed": str(MISSED_PATH),
            "summary": str(SUMMARY_PATH),
        },
    }


def append_run_log(entry: Dict[str, Any], *, dry_run: bool) -> None:
    if dry_run:
        return
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build minutes golden/missed sets from current feedback.")
    parser.add_argument("--json", action="store_true", help="print run summary as JSON")
    parser.add_argument("--dry-run", action="store_true", help="do not write local corpus files")
    parser.add_argument("--limit", type=int, default=200, help="task API page size")
    parser.add_argument("--max-pages", type=int, default=20, help="task API max pages")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = load_env()
    tasks, base_url = fetch_all_roby_tasks(env, limit=args.limit, max_pages=args.max_pages)
    candidate_index = read_feedback_candidate_index(CANDIDATES_PATH)
    entries = build_minutes_review_entries(tasks, candidate_index)
    manual_missed_items = build_manual_missed_items(MANUAL_MISSED_PATH)
    golden = build_golden_payload(entries, base_url=base_url)
    missed = build_missed_payload(entries, base_url=base_url, manual_missed_items=manual_missed_items)
    summary = build_summary(entries, base_url=base_url, manual_missed_count=len(manual_missed_items))
    write_json(GOLDEN_PATH, golden, dry_run=args.dry_run)
    write_json(MISSED_PATH, missed, dry_run=args.dry_run)
    write_json(SUMMARY_PATH, summary, dry_run=args.dry_run)
    run_entry = build_run_entry(
        base_url=base_url,
        entries=entries,
        golden=golden,
        missed=missed,
        dry_run=args.dry_run,
        manual_missed_count=len(manual_missed_items),
    )
    append_run_log(run_entry, dry_run=args.dry_run)
    append_audit_event(
        "minutes_eval_corpus.build",
        {
            "reviewed_minutes_tasks": len(entries),
            "golden_items": len(golden.get("items") or []),
            "missed_items": len(missed.get("items") or []),
            "manual_missed_items": len(manual_missed_items),
            "dry_run": args.dry_run,
        },
        source="roby-minutes-eval-corpus",
        severity="info",
    )
    if args.json:
        print(json.dumps(run_entry, ensure_ascii=False))
    else:
        print(f"minutes reviewed={len(entries)} golden={len(golden.get('items') or [])} missed={len(missed.get('items') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

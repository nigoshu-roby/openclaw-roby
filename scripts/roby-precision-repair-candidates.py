#!/usr/bin/env python3
"""Build dry-run repair candidates for existing Roby tasks in Neuronic.

This script does not mutate Neuronic. It turns the precision diagnostics into a
reviewable repair queue for semantic parent misnesting and duplicate minutes
parents/actions.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from roby_audit import append_audit_event

STATE_ROOT = Path.home() / ".openclaw" / "roby"
OUTPUT_PATH = STATE_ROOT / "precision_repair_candidates_latest.json"
RUN_LOG_PATH = STATE_ROOT / "precision_repair_candidates_runs.jsonl"
SCRIPTS_DIR = Path(__file__).resolve().parent


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


diagnostics = load_module(SCRIPTS_DIR / "roby-precision-diagnostics.py", "roby_precision_diagnostics_for_repair")


def parse_dt(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return datetime.max.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def task_identity(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "task_id": row.get("task_id"),
        "origin_id": row.get("origin_id") or "",
        "title": row.get("title") or "",
        "project": row.get("project") or "",
        "parent_origin_id": row.get("parent_origin_id"),
        "source_doc_id": row.get("source_doc_id") or "",
        "source_doc_title": row.get("source_doc_title") or "",
        "source_run_id": row.get("source_run_id") or "",
        "status": row.get("status") or "",
        "feedback_state": row.get("feedback_state") or "",
        "feedback_reason_code": row.get("feedback_reason_code"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def build_semantic_parent_repairs(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    repairs: List[Dict[str, Any]] = []
    for row in entries:
        if diagnostics.detect_domain(row) != "minutes":
            continue
        if diagnostics.normalize_feedback_state(row) != "bad":
            continue
        if str(row.get("feedback_reason_code") or "") != "wrong_project":
            continue
        parent_origin = str(row.get("parent_origin_id") or "").strip()
        if not parent_origin:
            continue
        text = " ".join(
            str(row.get(key) or "")
            for key in ("title", "note", "source_doc_title", "reason")
        )
        current_project = str(row.get("project") or "").strip()
        suggested = [
            project
            for project in diagnostics.detect_meeting_term_projects(text)
            if project and project != current_project
        ]
        if not suggested:
            continue
        repairs.append(
            {
                "type": "semantic_parent_misnested",
                "confidence": "high" if len(suggested) == 1 else "review",
                "recommended_action": "move_to_project_parent_or_recreate_under_suggested_project",
                "current": task_identity(row),
                "suggested_project": suggested[0],
                "suggested_projects": suggested,
                "reason": "child task has strong project-specific terms that conflict with its current parent project",
            }
        )
    repairs.sort(key=lambda item: (item["current"]["source_doc_title"], item["current"]["project"], item["current"]["title"]))
    return repairs


def _duplicate_group_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    source = str(row.get("source_doc_id") or row.get("source_doc_title") or "")
    project = str(row.get("project") or "")
    key = diagnostics.duplicate_similarity_key(row)
    return source, project, key


def build_duplicate_repairs(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in entries:
        if diagnostics.detect_domain(row) != "minutes":
            continue
        key = _duplicate_group_key(row)
        if len(key[2]) < 8:
            continue
        buckets[key].append(row)

    repairs: List[Dict[str, Any]] = []
    for (_source, _project, similarity_key), rows in buckets.items():
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda row: (parse_dt(row.get("created_at")), str(row.get("origin_id") or "")))
        keep = ordered[0]
        duplicates = ordered[1:]
        kind = "parent_group_duplicate" if diagnostics.looks_like_auto_parent_title(keep) else "child_action_duplicate"
        repairs.append(
            {
                "type": kind,
                "confidence": "review" if kind == "child_action_duplicate" else "high",
                "recommended_action": "keep_oldest_and_archive_or_merge_duplicates",
                "similarity_key": similarity_key,
                "keep": task_identity(keep),
                "duplicates": [task_identity(row) for row in duplicates],
                "count": len(rows),
            }
        )
    repairs.sort(key=lambda item: (-int(item["count"]), item["type"], item["keep"]["project"], item["similarity_key"]))
    return repairs


def get_any(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def is_minutes_live_task(row: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    run_id = str(get_any(candidate, "run_id", "source_run_id") or get_any(row, "run_id", "runId") or "")
    if run_id.startswith("roby:minutes:"):
        return True
    source_title = str(
        get_any(candidate, "source_doc_title", "sourceDocTitle")
        or get_any(row, "source_doc_title", "sourceDocTitle")
        or ""
    )
    if "社内定例" in source_title or "Gemini によるメモ" in source_title:
        return True
    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    return any(str(tag).strip().lower() == "source:gdocs" for tag in tags)


def build_live_minutes_entries(tasks: List[Dict[str, Any]], candidate_index: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for task in tasks:
        origin_id = str(get_any(task, "origin_id", "originId") or "").strip()
        if not origin_id:
            continue
        candidate = candidate_index.get(origin_id) or {}
        if not is_minutes_live_task(task, candidate):
            continue
        entries.append(
            {
                "origin_id": origin_id,
                "task_id": get_any(task, "id", "task_id", "taskId"),
                "title": get_any(task, "title") or candidate.get("title") or "",
                "project": get_any(candidate, "project") or get_any(task, "project") or "",
                "parent_origin_id": get_any(candidate, "parent_origin_id", "parentOriginId")
                or get_any(task, "parent_origin_id", "parentOriginId"),
                "source_doc_id": get_any(candidate, "source_doc_id", "sourceDocId")
                or get_any(task, "source_doc_id", "sourceDocId")
                or "",
                "source_doc_title": get_any(candidate, "source_doc_title", "sourceDocTitle")
                or get_any(task, "source_doc_title", "sourceDocTitle")
                or "",
                "source_run_id": get_any(candidate, "run_id", "source_run_id", "sourceRunId")
                or get_any(task, "run_id", "runId")
                or "",
                "feedback_state": diagnostics.normalize_feedback_state(task),
                "feedback_reason_code": get_any(task, "feedback_reason_code", "feedbackReasonCode"),
                "updated_at": get_any(task, "updated_at", "updatedAt"),
                "created_at": get_any(task, "created_at", "createdAt"),
                "status": get_any(task, "status") or "",
            }
        )
    return entries


def build_payload(entries: List[Dict[str, Any]], *, base_url: str = "", duplicate_entries: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    annotated = diagnostics.apply_annotations(entries)
    duplicate_source = diagnostics.apply_annotations(duplicate_entries or entries)
    semantic = build_semantic_parent_repairs(annotated)
    duplicates = build_duplicate_repairs(duplicate_source)
    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "kind": "precision_repair_candidates",
        "mode": "dry_run",
        "notes": {
            "safety": "This report does not mutate Neuronic. Review candidates before applying any move/archive/update.",
            "scope": "Existing Roby minutes tasks only; future prevention lives in roby-minutes gates.",
        },
        "source": {"neuronic_base_url": base_url},
        "summary": {
            "entries": len(entries),
            "duplicate_scan_entries": len(duplicate_source),
            "semantic_parent_misnested": len(semantic),
            "duplicate_groups": len(duplicates),
            "duplicate_items": sum(len(row.get("duplicates") or []) for row in duplicates),
        },
        "semantic_parent_misnested": semantic,
        "duplicates": duplicates,
    }


def collect_entries(limit: int, max_pages: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    minutes_mod = load_module(SCRIPTS_DIR / "roby-minutes-eval-corpus.py", "roby_minutes_eval_for_repair")
    env = minutes_mod.load_env()
    tasks, base_url = minutes_mod.fetch_all_roby_tasks(env, limit=limit, max_pages=max_pages)
    candidate_index = minutes_mod.read_feedback_candidate_index(minutes_mod.CANDIDATES_PATH)
    reviewed_entries = minutes_mod.build_minutes_review_entries(tasks, candidate_index)
    live_entries = build_live_minutes_entries(tasks, candidate_index)
    return reviewed_entries, live_entries, base_url


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_run_log(payload: Dict[str, Any]) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "ts": iso_now(),
        "event": "precision_repair_candidates",
        "mode": "dry_run",
        **(payload.get("summary") or {}),
    }
    with RUN_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build dry-run repair candidates for existing precision issues.")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    entries, live_entries, base_url = collect_entries(limit=max(1, args.limit), max_pages=max(1, args.max_pages))
    payload = build_payload(entries, base_url=base_url, duplicate_entries=live_entries)
    payload["paths"] = {"output": str(OUTPUT_PATH), "run_log": str(RUN_LOG_PATH)}
    if not args.no_write:
        write_json(OUTPUT_PATH, payload)
        append_run_log(payload)
        append_audit_event(
            "precision.repair_candidates",
            {"status": "ok", **payload["summary"]},
            source="roby-precision-repair-candidates",
        )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

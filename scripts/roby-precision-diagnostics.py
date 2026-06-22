#!/usr/bin/env python3
"""Build precision diagnostics from current Roby feedback entries.

This report intentionally sits one level below the headline precision metric:
it turns raw feedback reason codes into reusable failure modes such as weak
project evidence, project alias collision, and broadcast mail over-capture.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from roby_audit import append_audit_event

STATE_ROOT = Path.home() / ".openclaw" / "roby"
OUTPUT_PATH = STATE_ROOT / "precision_diagnostics_latest.json"
RUN_LOG_PATH = STATE_ROOT / "precision_diagnostics_runs.jsonl"
SCRIPTS_DIR = Path(__file__).resolve().parent

REVIEWED_STATES = {"good", "bad", "missed"}
ACTIONABLE_STATES = {"good", "bad", "missed", "pending"}
BUSINESS_WORDS = ("請求", "契約", "発注", "見積", "支払", "確認", "回答", "至急", "重要", "納品")
PROMO_WORDS = ("通信", "ニュース", "メルマガ", "newsletter", "キャンペーン", "セミナー", "ウェビナー", "お知らせ")
AUTOMATED_WORDS = ("no-reply", "noreply", "自動送信", "notification", "通知", "wordpress", "google calendar")
MEETING_PROJECT_TERMS = {
    "SNW様-777BEACON": ("777beacon", "ssbp", "スイッチスマイル", "スイッチスマイルビーコンプラットフォーム", "ピナブル", "サミネ", "サミーネットワークス"),
    "BT振興会-チケットショップ": ("チケットショップ", "予約", "bt振興会"),
    "BT振興会-Mooovi": ("mooovi", "モーヴィ", "リンク挿入"),
    "MIDジャパン-パチンコレポート": ("mid", "ミッド", "堀之内", "パチンコ", "差分資料"),
    "ボーネルンド": ("ボーネルンド", "スマレジ", "obic", "bornelund"),
    "LINE広告配信": ("line広告", "一広", "運営会社一覧", "販売ルート", "広告商品", "ブログウォッチャー", "bw", "ビーコン管理システム", "広告識別子", "gas", "本番移行", "マイナー番号"),
}


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


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def normalize_feedback_state(row: Dict[str, Any]) -> str:
    state = str(row.get("feedback_state") or row.get("feedbackState") or "pending").strip().lower()
    return state or "pending"


def safe_div(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def parse_dt(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def detect_domain(entry: Dict[str, Any]) -> str:
    run_id = str(entry.get("source_run_id") or entry.get("run_id") or "")
    if run_id.startswith("roby:gmail:"):
        return "gmail"
    if run_id.startswith("roby:minutes:") or entry.get("source_doc_title"):
        return "minutes"
    project = str(entry.get("project") or "").strip().lower()
    if project == "email":
        return "gmail"
    return "unknown"


def has_any(text: str, needles: Iterable[str]) -> bool:
    folded = normalize_text(text)
    return any(normalize_text(needle) in folded for needle in needles if str(needle).strip())


def extract_section_project(note: str) -> str:
    match = re.search(r"(?:^|\n)section_project:([^\n]+)", str(note or ""))
    return match.group(1).strip() if match else ""


def detect_meeting_term_projects(text: str) -> List[str]:
    folded = normalize_text(text)
    hits: List[str] = []
    for project, terms in MEETING_PROJECT_TERMS.items():
        if has_any(folded, terms):
            hits.append(project)
    return hits


def classify_gmail_cause(entry: Dict[str, Any]) -> str:
    reason = str(entry.get("feedback_reason_code") or "").strip()
    text = " ".join(
        str(entry.get(key) or "")
        for key in ("title", "sender_label", "source_doc_title", "note", "reason")
    )
    folded = normalize_text(text)
    if reason == "newsletter_false_positive":
        if has_any(folded, BUSINESS_WORDS) and has_any(folded, PROMO_WORDS):
            return "promo_mail_with_business_words"
        return "broadcast_mail_overcaptured"
    if has_any(folded, AUTOMATED_WORDS):
        return "automated_notice_overcaptured"
    if "calendar" in folded or "予定" in folded or "招待" in folded:
        return "calendar_notice_overcaptured"
    if reason == "should_be_review_only":
        return "human_mail_without_user_action"
    if reason == "should_be_reply":
        return "reply_needed_but_task_shape_wrong"
    return reason or "unlabeled"


def classify_minutes_cause(entry: Dict[str, Any]) -> str:
    reason = str(entry.get("feedback_reason_code") or "").strip()
    project = str(entry.get("project") or "").strip()
    text = " ".join(
        str(entry.get(key) or "")
        for key in ("title", "note", "source_doc_title", "reason")
    )
    section_project = extract_section_project(str(entry.get("note") or ""))
    term_projects = detect_meeting_term_projects(text)
    conflicting_terms = [hit for hit in term_projects if project and hit != project]
    if reason == "duplicate":
        return "duplicate_same_doc_action"
    if section_project and project and section_project != project:
        return "section_context_ignored"
    if reason == "wrong_project" and entry.get("parent_origin_id") and conflicting_terms:
        return "semantic_parent_misnested"
    if reason == "wrong_project" and conflicting_terms:
        return "cross_project_topic_collision"
    if reason == "wrong_project" and len(term_projects) > 1:
        return "project_alias_collision"
    if reason == "wrong_project":
        return "weak_project_evidence"
    if reason == "too_broad":
        return "parent_or_summary_too_broad"
    if reason == "should_be_review_only":
        return "status_note_should_not_task"
    return reason or "unlabeled"


def classify_refined_cause(entry: Dict[str, Any]) -> str:
    domain = detect_domain(entry)
    if domain == "gmail":
        return classify_gmail_cause(entry)
    if domain == "minutes":
        return classify_minutes_cause(entry)
    return str(entry.get("feedback_reason_code") or "unlabeled")


def duplicate_similarity_key(entry: Dict[str, Any]) -> str:
    title = str(entry.get("title") or "")
    title = re.sub(r"^[^/]{1,40}\s*/\s*", "", title)
    title = re.sub(r"[【】\[\]（）()「」『』:：,，.。・/\-\s]+", "", title)
    title = re.sub(r"(する|してください|お願いします|確認する|共有する)$", "", title)
    return title.lower()


def looks_like_auto_parent_title(entry: Dict[str, Any]) -> bool:
    title = str(entry.get("title") or "")
    project = str(entry.get("project") or "")
    source_title = str(entry.get("source_doc_title") or "")
    if not title or not project or not source_title:
        return False
    normalized_title = normalize_text(title).replace("／", "/")
    normalized_project = normalize_text(project)
    normalized_source = normalize_text(source_title).replace("　", " ")
    return normalized_title.startswith(f"{normalized_project} /") and normalized_source in normalized_title


def annotate_duplicate_clusters(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in entries:
        domain = detect_domain(row)
        if domain != "minutes":
            continue
        key = duplicate_similarity_key(row)
        if len(key) < 8:
            continue
        buckets[(str(row.get("source_doc_id") or row.get("source_doc_title") or ""), str(row.get("project") or ""), key)].append(row)
    annotated: List[Dict[str, Any]] = []
    for rows in buckets.values():
        if len(rows) < 2:
            continue
        annotated.append(
            {
                "kind": "parent_group_duplicate" if looks_like_auto_parent_title(rows[0]) else "child_action_duplicate",
                "source_doc_title": rows[0].get("source_doc_title") or "",
                "project": rows[0].get("project") or "",
                "similarity_key": duplicate_similarity_key(rows[0]),
                "count": len(rows),
                "examples": [str(row.get("title") or "") for row in rows[:5]],
            }
        )
    annotated.sort(key=lambda row: (-int(row["count"]), str(row["project"]), str(row["similarity_key"])))
    return annotated[:20]


def annotate_semantic_parent_misnesting(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for row in entries:
        if detect_domain(row) != "minutes":
            continue
        if normalize_feedback_state(row) != "bad":
            continue
        if str(row.get("feedback_reason_code") or "") != "wrong_project":
            continue
        parent_origin = str(row.get("parent_origin_id") or "").strip()
        if not parent_origin:
            continue
        project = str(row.get("project") or "").strip()
        text = " ".join(
            str(row.get(key) or "")
            for key in ("title", "note", "source_doc_title", "reason")
        )
        suggested = [hit for hit in detect_meeting_term_projects(text) if hit != project]
        if not suggested:
            continue
        candidates.append(
            {
                "title": row.get("title") or "",
                "project": project,
                "suggested_projects": suggested,
                "source_doc_title": row.get("source_doc_title") or "",
                "parent_origin_id": parent_origin,
            }
        )
    candidates.sort(key=lambda row: (str(row["source_doc_title"]), str(row["project"]), str(row["title"])))
    return candidates[:20]


def metric_summary(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = Counter(normalize_feedback_state(row) for row in entries)
    good = counts.get("good", 0)
    bad = counts.get("bad", 0)
    missed = counts.get("missed", 0)
    reviewed = good + bad + missed
    reasons = Counter(str(row.get("feedback_reason_code") or "") for row in entries if row.get("feedback_reason_code"))
    refined = Counter(str(row.get("refined_cause") or "") for row in entries if row.get("refined_cause"))
    projects = Counter(str(row.get("project") or "") for row in entries if row.get("project"))
    return {
        "items": len(entries),
        "reviewed": reviewed,
        "good": good,
        "bad": bad,
        "missed": missed,
        "pending": counts.get("pending", 0),
        "precision": safe_div(good, good + bad),
        "usefulness": safe_div(good, good + bad + missed),
        "counts": dict(counts),
        "top_feedback_reasons": [{"reason_code": key, "count": value} for key, value in reasons.most_common(10)],
        "top_refined_causes": [{"cause": key, "count": value} for key, value in refined.most_common(10)],
        "top_projects": [{"project": key, "count": value} for key, value in projects.most_common(10)],
    }


def apply_annotations(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    annotated: List[Dict[str, Any]] = []
    for row in entries:
        copied = dict(row)
        copied["domain"] = detect_domain(copied)
        copied["feedback_state"] = normalize_feedback_state(copied)
        if copied["feedback_state"] in {"bad", "missed"}:
            copied["refined_cause"] = classify_refined_cause(copied)
        annotated.append(copied)
    return annotated


def build_diagnostics(entries: List[Dict[str, Any]], *, generated_at: Optional[str] = None) -> Dict[str, Any]:
    annotated = apply_annotations(entries)
    now = parse_dt(generated_at) or datetime.now(timezone.utc)
    cutoffs = {
        "all": None,
        "created_last_30d": now - timedelta(days=30),
        "updated_last_30d": now - timedelta(days=30),
        "since_2026_06_02": datetime(2026, 6, 2, tzinfo=timezone.utc),
    }
    cohorts: Dict[str, Dict[str, Any]] = {}
    for domain in ("gmail", "minutes", "unknown"):
        domain_entries = [row for row in annotated if row.get("domain") == domain]
        if not domain_entries:
            continue
        for cohort, cutoff in cutoffs.items():
            rows = domain_entries
            if cutoff is not None:
                field = "updated_at" if cohort == "updated_last_30d" else "created_at"
                if cohort == "since_2026_06_02":
                    field = "created_at"
                rows = [row for row in domain_entries if (parse_dt(row.get(field)) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
            cohorts[f"{domain}:{cohort}"] = metric_summary(rows)
    return {
        "schema_version": 1,
        "generated_at": generated_at or iso_now(),
        "kind": "precision_diagnostics",
        "notes": {
            "purpose": "raw feedback reason codes are regrouped into reusable failure modes for generic precision work",
            "since_2026_06_02": "tracks outcomes after the latest handoff/precision sprint baseline",
        },
        "overall": metric_summary(annotated),
        "cohorts": cohorts,
        "duplicate_clusters": annotate_duplicate_clusters(annotated),
        "semantic_parent_misnesting_candidates": annotate_semantic_parent_misnesting(annotated),
    }


def collect_entries(limit: int, max_pages: int) -> Tuple[List[Dict[str, Any]], str]:
    gmail_mod = load_module(SCRIPTS_DIR / "roby-gmail-eval-corpus.py", "roby_gmail_eval_for_diagnostics")
    minutes_mod = load_module(SCRIPTS_DIR / "roby-minutes-eval-corpus.py", "roby_minutes_eval_for_diagnostics")
    env = gmail_mod.load_env()
    tasks, base_url = gmail_mod.fetch_all_roby_tasks(env, limit=limit, max_pages=max_pages)
    candidate_index = gmail_mod.read_feedback_candidate_index(gmail_mod.CANDIDATES_PATH)
    gmail_entries = gmail_mod.build_gmail_review_entries(tasks, candidate_index)
    minutes_entries = minutes_mod.build_minutes_review_entries(tasks, candidate_index)
    return [*gmail_entries, *minutes_entries], base_url


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_run_log(payload: Dict[str, Any]) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "ts": iso_now(),
        "event": "precision_diagnostics",
        "overall_precision": payload.get("overall", {}).get("precision"),
        "overall_reviewed": payload.get("overall", {}).get("reviewed"),
        "top_refined_causes": payload.get("overall", {}).get("top_refined_causes", [])[:5],
    }
    with RUN_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build reusable precision diagnostics from current feedback.")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries, base_url = collect_entries(limit=max(1, args.limit), max_pages=max(1, args.max_pages))
    payload = build_diagnostics(entries)
    payload["paths"] = {
        "diagnostics": str(OUTPUT_PATH),
        "run_log": str(RUN_LOG_PATH),
    }
    payload["source"] = {"neuronic_base_url": base_url}
    if not args.dry_run:
        write_json(OUTPUT_PATH, payload)
        append_run_log(payload)
        append_audit_event(
            "precision.diagnostics",
            {
                "status": "ok",
                "reviewed": payload["overall"]["reviewed"],
                "precision": payload["overall"]["precision"],
                "top_refined_causes": payload["overall"]["top_refined_causes"][:5],
            },
            source="roby-precision-diagnostics",
        )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

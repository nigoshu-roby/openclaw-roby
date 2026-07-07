#!/usr/bin/env python3
"""Build a personal Morning Command 15 brief from PBS/Neuronic state.

This is the first PBS-as-secretary MVP surface. It does not mutate Neuronic.
It reads live Roby-created tasks plus recent PBS run artifacts and writes a
small JSON/Markdown brief for the user's first 15 minutes of work.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

JST = timezone(timedelta(hours=9))
ENV_PATH = Path.home() / ".openclaw" / ".env"
STATE_ROOT = Path.home() / ".openclaw" / "roby"
OUTPUT_DIR = STATE_ROOT / "morning_command"
LATEST_JSON = OUTPUT_DIR / "latest.json"
LATEST_MD = OUTPUT_DIR / "latest.md"
HISTORY_JSONL = OUTPUT_DIR / "history.jsonl"
CANDIDATES_PATH = STATE_ROOT / "feedback_candidates.jsonl"
RUN_LOG_PATHS = {
    "gmail_triage": STATE_ROOT / "gmail_triage_runs.jsonl",
    "minutes_sync": STATE_ROOT / "minutes_runs.jsonl",
    "feedback_sync": STATE_ROOT / "feedback_sync_runs.jsonl",
    "orchestrator": STATE_ROOT / "orchestrator_runs.jsonl",
    "repair_candidates": STATE_ROOT / "precision_repair_candidates_runs.jsonl",
}
REPAIR_CANDIDATES_LATEST = STATE_ROOT / "precision_repair_candidates_latest.json"
KEYCHAIN_SECRET_KEYS = {
    "NEURONIC_TOKEN",
    "TASKD_AUTH_TOKEN",
}

PROJECT_ALIASES: Dict[str, Tuple[str, ...]] = {
    "ボーネルンド": ("ボーネルンド", "スマレジ", "OBIC", "DIPRO", "予約システム", "通信環境", "検収"),
    "LINE広告配信": ("LINE広告", "LINEヤフー", "一広", "ブログウォッチャー", "BW", "ビーコン", "IDFA", "ビジットサーチ", "平和島"),
    "瑞鳳": ("瑞鳳", "ABP", "AURORA", "Yellowfin", "AI Business Platform", "飯海"),
    "BT振興会-Mooovi": ("Mooovi", "BT振興会", "芦屋", "顧客管理システム"),
    "MID": ("MID", "ミッド", "三井", "ミッド・ガーデン"),
}
DECISION_HINTS = ("判断", "方針", "合意", "確認", "相談", "契約", "通信環境", "MVP", "スケジュール")
WAITING_HINTS = ("待ち", "確認中", "連絡があり次第", "依頼中", "回答", "ジャッジ", "先方", "クライアント側")
URGENT_HINTS = ("本日", "今日", "明日", "至急", "期限", "リリース", "請求", "検収", "提出")
FINANCE_HINTS = ("請求書", "請求", "支払", "支払い", "入金", "未受領", "見積書")
GENERIC_EMAIL_TITLES = (
    "依頼内容を確認して対応する",
    "メールを確認して対応する",
    "内容を確認して対応する",
)
PROJECT_PRIORITY_WEIGHTS: Dict[str, int] = {
    "ボーネルンド": 6,
    "LINE広告配信": 5,
    "瑞鳳": 4,
    "BT振興会-Mooovi": 3,
    "MID": 2,
    "請求・経理": -4,
    "email": -5,
    "未分類": -4,
}


def now_jst() -> datetime:
    return datetime.now(tz=JST)


def iso_now() -> str:
    return now_jst().isoformat()


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(JST)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST)


def load_env() -> Dict[str, str]:
    env = dict(os.environ)
    env_file = Path(env.get("ROBY_ENV_FILE", str(ENV_PATH))).expanduser()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            if key and not str(env.get(key, "")).strip():
                env[key] = value
    keychain_service = env.get("ROBY_KEYCHAIN_SERVICE", "roby-pbs")
    for key in KEYCHAIN_SECRET_KEYS:
        if str(env.get(key, "")).strip():
            continue
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", keychain_service, "-a", key, "-w"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                env[key] = result.stdout.strip()
        except Exception:
            pass
    return env


def build_neuronic_base_url(env: Dict[str, str]) -> str:
    base = str(env.get("NEURONIC_API_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/")
    for key in ("NEURONIC_URL", "NEURONIC_FALLBACK_URL", "ROBY_NEURONIC_URL"):
        raw = str(env.get(key) or "").strip()
        if not raw:
            continue
        if "/api/v1/tasks/" in raw:
            return raw.split("/api/v1/tasks/", 1)[0] + "/api/v1"
        if raw.endswith("/api/v1/tasks"):
            return raw.rsplit("/tasks", 1)[0]
    return "http://127.0.0.1:5174/api/v1"


def build_headers(env: Dict[str, str]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = str(env.get("NEURONIC_TOKEN") or env.get("TASKD_AUTH_TOKEN") or "").strip()
    if token:
        header_name = str(env.get("NEURONIC_AUTH_HEADER") or "Authorization").strip() or "Authorization"
        headers[header_name] = f"Bearer {token}" if header_name.lower() == "authorization" else token
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
    total = payload.get("total")
    try:
        total_int = int(total) if total is not None else None
    except Exception:
        total_int = None
    return [row for row in items if isinstance(row, dict)], total_int


def fetch_roby_tasks(env: Dict[str, str], *, limit: int = 1000, max_pages: int = 50) -> Tuple[List[Dict[str, Any]], str]:
    base_url = build_neuronic_base_url(env)
    headers = build_headers(env)
    out: List[Dict[str, Any]] = []
    offset = 0
    total_hint: Optional[int] = None
    for _ in range(max_pages):
        items, total = fetch_tasks_page(base_url, headers, limit=limit, offset=offset)
        if total is not None:
            total_hint = total
        if not items:
            break
        out.extend([row for row in items if str(row.get("source") or "").strip() == "roby"])
        offset += len(items)
        if len(items) < limit and (total_hint is None or offset >= total_hint):
            break
        if total_hint is not None and offset >= total_hint:
            break
    return out, base_url


def read_jsonl(path: Path, *, max_lines: int = 2000) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if max_lines > 0:
        lines = lines[-max_lines:]
    out: List[Dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def read_feedback_candidate_index(path: Path = CANDIDATES_PATH) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in read_jsonl(path, max_lines=10000):
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


def read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def duplicate_origin_ids_from_repair_candidates(path: Path = REPAIR_CANDIDATES_LATEST) -> set[str]:
    data = read_json_file(path)
    duplicates = data.get("duplicates")
    if not isinstance(duplicates, list):
        return set()
    out: set[str] = set()
    for group in duplicates:
        if not isinstance(group, dict):
            continue
        for row in group.get("duplicates") or []:
            if not isinstance(row, dict):
                continue
            origin_id = str(row.get("origin_id") or "").strip()
            if origin_id:
                out.add(origin_id)
    return out


def get_any(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def merge_candidate_metadata(task: Dict[str, Any], candidate_index: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    origin_id = str(get_any(task, "origin_id", "originId") or "")
    candidate = candidate_index.get(origin_id) or {}
    merged = dict(task)
    for key in ("project", "source_doc_id", "source_doc_title", "parent_origin_id", "run_id"):
        if not merged.get(key) and candidate.get(key):
            merged[key] = candidate.get(key)
    if candidate.get("project"):
        merged["project"] = candidate.get("project")
    return merged


def infer_project(row: Dict[str, Any]) -> str:
    explicit = str(get_any(row, "project") or "").strip()
    run_id = str(get_any(row, "run_id", "runId") or "")
    title_blob = str(get_any(row, "title") or "")
    if run_id.startswith("roby:gmail:") and any(hint in title_blob for hint in FINANCE_HINTS):
        return "請求・経理"
    if explicit and explicit.lower() != "email":
        return explicit
    blob = "\n".join(
        str(get_any(row, key) or "")
        for key in ("title", "note", "source_doc_title", "sourceDocTitle")
    )
    for project, aliases in PROJECT_ALIASES.items():
        if any(alias and alias in blob for alias in aliases):
            return project
    if explicit:
        return explicit
    return "未分類"


def task_identity(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": get_any(row, "id", "task_id", "taskId"),
        "origin_id": get_any(row, "origin_id", "originId") or "",
        "title": get_any(row, "title") or "",
        "project": infer_project(row),
        "status": str(get_any(row, "status") or ""),
        "due_date": get_any(row, "due_date", "dueDate") or "",
        "created_at": get_any(row, "created_at", "createdAt") or "",
        "updated_at": get_any(row, "updated_at", "updatedAt") or "",
        "source_doc_title": get_any(row, "source_doc_title", "sourceDocTitle") or "",
        "run_id": get_any(row, "run_id", "runId") or "",
    }


def is_done(row: Dict[str, Any]) -> bool:
    status = str(get_any(row, "status") or "").strip().lower()
    return status in {"done", "completed", "archived", "cancelled"}


def due_score(row: Dict[str, Any], today: datetime) -> int:
    due = str(get_any(row, "due_date", "dueDate") or "").strip()
    if not due:
        return 0
    try:
        due_dt = datetime.fromisoformat(due).replace(tzinfo=JST)
    except Exception:
        return 0
    delta = (due_dt.date() - today.date()).days
    if delta < 0:
        return 8
    if delta == 0:
        return 7
    if delta <= 2:
        return 5
    if delta <= 7:
        return 3
    return 1


def task_score(row: Dict[str, Any], today: datetime) -> int:
    text = f"{get_any(row, 'title') or ''}\n{get_any(row, 'note') or ''}"
    score = due_score(row, today)
    project = infer_project(row)
    score += PROJECT_PRIORITY_WEIGHTS.get(project, 0)
    if any(hint in text for hint in URGENT_HINTS):
        score += 3
    if any(hint in text for hint in DECISION_HINTS):
        score += 2
    updated = parse_dt(get_any(row, "updated_at", "updatedAt"))
    if updated and (today - updated).days <= 2:
        score += 1
    if str(get_any(row, "status") or "").lower() == "inbox":
        score += 1
    if project == "請求・経理" and due_score(row, today) == 0:
        score -= 3
    if is_generic_email_task(row):
        score -= 5
    if "メール対応:" in str(get_any(row, "title") or ""):
        score -= 2
    return score


def is_generic_email_task(row: Dict[str, Any]) -> bool:
    title = str(get_any(row, "title") or "")
    project = infer_project(row)
    if project in {"email", "未分類"} and any(phrase in title for phrase in GENERIC_EMAIL_TITLES):
        return True
    if project == "email" and not any(alias in title for aliases in PROJECT_ALIASES.values() for alias in aliases):
        return True
    return False


def is_admin_or_low_confidence(row: Dict[str, Any], today: datetime) -> bool:
    project = infer_project(row)
    if project == "請求・経理" and due_score(row, today) < 7:
        return True
    if is_generic_email_task(row) and due_score(row, today) < 7:
        return True
    return False


def select_focus_tasks(open_tasks: List[Dict[str, Any]], today: datetime, limit: int) -> List[Dict[str, Any]]:
    ordered = sorted(open_tasks, key=lambda row: (-task_score(row, today), str(get_any(row, "updated_at", "updatedAt") or ""), str(get_any(row, "title") or "")))
    selected: List[Dict[str, Any]] = []
    project_counts: Counter[str] = Counter()
    for row in ordered:
        if is_admin_or_low_confidence(row, today):
            continue
        project = infer_project(row)
        cap = 1 if project in {"email", "請求・経理", "未分類"} else 2
        if project_counts[project] >= cap:
            continue
        selected.append(task_identity(row) | {"reason": focus_reason(row, today)})
        project_counts[project] += 1
        if len(selected) >= limit:
            break
    return selected


def select_admin_review(open_tasks: List[Dict[str, Any]], today: datetime, limit: int) -> List[Dict[str, Any]]:
    candidates = [row for row in open_tasks if is_admin_or_low_confidence(row, today)]
    ordered = sorted(candidates, key=lambda row: (-task_score(row, today), str(get_any(row, "updated_at", "updatedAt") or ""), str(get_any(row, "title") or "")))
    out: List[Dict[str, Any]] = []
    for row in ordered[:limit]:
        reason = "経理/請求系はFocus外で確認"
        if is_generic_email_task(row):
            reason = "generic email taskのため直接Focusに入れない"
        out.append(task_identity(row) | {"reason": reason})
    return out


def focus_reason(row: Dict[str, Any], today: datetime) -> str:
    reasons: List[str] = []
    score_due = due_score(row, today)
    if score_due >= 7:
        reasons.append("期限が今日/超過")
    elif score_due >= 3:
        reasons.append("期限が近い")
    text = f"{get_any(row, 'title') or ''}\n{get_any(row, 'note') or ''}"
    if any(hint in text for hint in URGENT_HINTS):
        reasons.append("重要キーワードあり")
    if any(hint in text for hint in DECISION_HINTS):
        reasons.append("判断/確認が必要")
    return "、".join(reasons) if reasons else "未完了タスク"


def select_decisions(open_tasks: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in open_tasks:
        text = f"{get_any(row, 'title') or ''}\n{get_any(row, 'note') or ''}"
        if any(hint in text for hint in DECISION_HINTS):
            out.append(task_identity(row) | {"reason": "あなたの判断/確認が必要そうです"})
    return out[:limit]


def select_waiting(open_tasks: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in open_tasks:
        text = f"{get_any(row, 'title') or ''}\n{get_any(row, 'note') or ''}"
        if any(hint in text for hint in WAITING_HINTS):
            out.append(task_identity(row) | {"reason": "相手待ち/確認待ちの可能性"})
    return out[:limit]


def build_project_health(tasks: List[Dict[str, Any]], today: datetime) -> List[Dict[str, Any]]:
    by_project: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in tasks:
        by_project[infer_project(row)].append(row)
    health: List[Dict[str, Any]] = []
    for project, rows in by_project.items():
        if project in {"email", "未分類"}:
            continue
        open_rows = [row for row in rows if not is_done(row)]
        overdue = [row for row in open_rows if due_score(row, today) >= 8]
        decisions = select_decisions(open_rows, 3)
        waiting = select_waiting(open_rows, 3)
        recent_updates = [
            row for row in rows
            if (parse_dt(get_any(row, "updated_at", "updatedAt")) and (today - parse_dt(get_any(row, "updated_at", "updatedAt"))).days <= 1)
        ]
        if overdue or len(decisions) >= 2:
            state = "red"
        elif waiting or decisions or open_rows:
            state = "yellow"
        else:
            state = "green"
        reason_parts = []
        if open_rows:
            reason_parts.append(f"未完了 {len(open_rows)}件")
        if overdue:
            reason_parts.append(f"期限超過 {len(overdue)}件")
        if decisions:
            reason_parts.append(f"判断候補 {len(decisions)}件")
        if waiting:
            reason_parts.append(f"待ち候補 {len(waiting)}件")
        if recent_updates:
            reason_parts.append(f"直近更新 {len(recent_updates)}件")
        health.append(
            {
                "project": project,
                "state": state,
                "reason": " / ".join(reason_parts) if reason_parts else "大きな未処理なし",
                "open": len(open_rows),
                "overdue": len(overdue),
                "decision_candidates": len(decisions),
                "waiting_candidates": len(waiting),
            }
        )
    order = {"red": 0, "yellow": 1, "green": 2}
    health.sort(key=lambda row: (order.get(str(row["state"]), 9), -int(row["open"]), str(row["project"])))
    return health[:10]


def latest_run_summaries(since: datetime) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, path in RUN_LOG_PATHS.items():
        entries = read_jsonl(path, max_lines=300)
        recent = []
        for entry in entries:
            ts = parse_dt(entry.get("ts") or entry.get("timestamp") or entry.get("generated_at"))
            if ts and ts >= since:
                recent.append(entry)
        if not recent:
            continue
        latest = recent[-1]
        rows.append({"name": name, "runs": len(recent), "latest": latest})
    return rows


def build_watch_items(run_summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    watch: List[Dict[str, Any]] = []
    for row in run_summaries:
        latest = row.get("latest") if isinstance(row.get("latest"), dict) else {}
        error_count = int(latest.get("error_count", 0) or latest.get("neuronic_errors", 0) or latest.get("failed_runs", 0) or 0)
        status = str(latest.get("status") or latest.get("gate") or "").lower()
        detail_blob = json.dumps(latest.get("checks") or latest.get("summary") or latest, ensure_ascii=False).lower()
        has_stale = "stale" in detail_blob and "stale_count': 0" not in detail_blob and '"stale_count": 0' not in detail_blob
        if error_count > 0 or status in {"failed", "fail", "blocked"} or has_stale:
            watch.append({"source": row["name"], "reason": "直近runにerror/stale系の兆候", "detail": summarize_run_detail(latest)})
        if row["name"] == "repair_candidates":
            dup_groups = int(latest.get("duplicate_groups", 0) or 0)
            if dup_groups:
                watch.append({"source": row["name"], "reason": f"重複repair候補 {dup_groups}グループ", "detail": summarize_run_detail(latest)})
    return watch[:8]


def summarize_run_detail(row: Dict[str, Any]) -> str:
    if not row:
        return ""
    keys = ("summary", "status", "event", "tasks", "errors", "error", "detail", "duplicate_groups", "semantic_parent_misnested")
    parts = []
    for key in keys:
        value = row.get(key)
        if value in (None, "", [], {}):
            continue
        parts.append(f"{key}={str(value)[:120]}")
    return " / ".join(parts)[:300]


def build_goals(project_health: List[Dict[str, Any]], focus: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    goals: List[str] = []
    primary_health = [
        row for row in project_health
        if row["state"] in {"red", "yellow"} and row["project"] not in {"email", "未分類", "請求・経理"}
    ]
    fallback_health = [row for row in project_health if row["state"] in {"red", "yellow"}]
    for row in primary_health + fallback_health:
        project = row["project"]
        if any(goal.startswith(f"{project}:") for goal in goals):
            continue
        if row.get("decision_candidates"):
            goals.append(f"{project}: 判断/確認が必要な論点を前に進める")
        elif row.get("waiting_candidates"):
            goals.append(f"{project}: 相手待ちの停滞を確認し、次の一手を決める")
        elif row.get("open"):
            goals.append(f"{project}: 未完了タスクを1つ以上進める")
        if len(goals) >= limit:
            break
    if len(goals) < limit:
        for task in focus:
            title = str(task.get("title") or "")
            project = str(task.get("project") or "未分類")
            goal = f"{project}: {title}"
            if goal not in goals:
                goals.append(goal)
            if len(goals) >= limit:
                break
    return goals[:limit]


def build_payload(tasks: List[Dict[str, Any]], *, base_url: str = "", generated_at: Optional[datetime] = None, focus_limit: int = 5) -> Dict[str, Any]:
    today = generated_at or now_jst()
    focus_limit = max(1, min(int(focus_limit), 15))
    candidate_index = read_feedback_candidate_index()
    duplicate_origins = duplicate_origin_ids_from_repair_candidates()
    merged_tasks = [
        merge_candidate_metadata(row, candidate_index)
        for row in tasks
        if str(get_any(row, "origin_id", "originId") or "") not in duplicate_origins
    ]
    open_tasks = [row for row in merged_tasks if not is_done(row)]
    focus = select_focus_tasks(open_tasks, today, focus_limit)
    admin_review = select_admin_review(open_tasks, today, 8)
    decisions = select_decisions(open_tasks, 8)
    waiting = select_waiting(open_tasks, 8)
    project_health = build_project_health(merged_tasks, today)
    since = today - timedelta(hours=24)
    run_summaries = latest_run_summaries(since)
    watch = build_watch_items(run_summaries)
    goals = build_goals(project_health, focus)
    status_counts = Counter(str(get_any(row, "status") or "unknown") for row in merged_tasks)
    return {
        "schema_version": 1,
        "kind": "morning_command_15",
        "generated_at": today.isoformat(),
        "mode": "read_only",
        "source": {"neuronic_base_url": base_url},
        "summary": {
            "tasks_total": len(merged_tasks),
            "open_tasks": len(open_tasks),
            "status_counts": dict(status_counts),
            "project_health_count": len(project_health),
            "focus_count": len(focus),
            "admin_review_count": len(admin_review),
            "decision_count": len(decisions),
            "waiting_count": len(waiting),
            "watch_count": len(watch),
            "suppressed_duplicate_candidates": len(tasks) - len(merged_tasks),
        },
        "today_goals": goals,
        "focus": focus,
        "admin_review": admin_review,
        "decisions": decisions,
        "waiting": waiting,
        "project_health": project_health,
        "watch": watch,
        "recent_runs": run_summaries,
        "next_prompt": f"今日のゴールを確認し、Focus {len(focus)}件から着手順を決めてください。",
    }


def render_markdown(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Morning Command 15")
    lines.append("")
    lines.append(f"- generated_at: {payload.get('generated_at')}")
    lines.append(f"- mode: {payload.get('mode')}")
    summary = payload.get("summary") or {}
    lines.append(f"- open_tasks: {summary.get('open_tasks', 0)} / tasks_total: {summary.get('tasks_total', 0)}")
    lines.append("")
    lines.append("## 今日のゴール")
    goals = payload.get("today_goals") or []
    if goals:
        for goal in goals:
            lines.append(f"- {goal}")
    else:
        lines.append("- 大きな優先ゴールは未検出です。")
    lines.append("")
    lines.append("## Focus")
    for row in payload.get("focus") or []:
        due = f" due:{row.get('due_date')}" if row.get("due_date") else ""
        lines.append(f"- [{row.get('project')}] {row.get('title')}{due} ({row.get('reason')})")
    if not payload.get("focus"):
        lines.append("- 未完了Focus候補はありません。")
    lines.append("")
    admin_review = payload.get("admin_review") or []
    if admin_review:
        lines.append("## 経理・低信頼候補")
        for row in admin_review:
            due = f" due:{row.get('due_date')}" if row.get("due_date") else ""
            lines.append(f"- [{row.get('project')}] {row.get('title')}{due} ({row.get('reason')})")
        lines.append("")
    lines.append("## 判断が必要")
    for row in payload.get("decisions") or []:
        lines.append(f"- [{row.get('project')}] {row.get('title')}")
    if not payload.get("decisions"):
        lines.append("- 明確な判断候補はありません。")
    lines.append("")
    lines.append("## Waiting / Watch")
    waiting = payload.get("waiting") or []
    if waiting:
        lines.append("### Waiting")
        for row in waiting:
            lines.append(f"- [{row.get('project')}] {row.get('title')} ({row.get('reason')})")
    watch = payload.get("watch") or []
    if watch:
        lines.append("### Watch")
        for row in watch:
            lines.append(f"- {row.get('source')}: {row.get('reason')} {row.get('detail')}")
    if not waiting and not watch:
        lines.append("- 大きな待ち/監視候補はありません。")
    lines.append("")
    lines.append("## Project Health")
    for row in payload.get("project_health") or []:
        lines.append(f"- {row.get('state')} [{row.get('project')}] {row.get('reason')}")
    if not payload.get("project_health"):
        lines.append("- project health は未検出です。")
    lines.append("")
    lines.append(f"Next: {payload.get('next_prompt')}")
    lines.append("")
    return "\n".join(lines)


def write_outputs(payload: Dict[str, Any], markdown: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LATEST_MD.write_text(markdown, encoding="utf-8")
    with HISTORY_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": iso_now(), "summary": payload.get("summary"), "path": str(LATEST_JSON)}, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the PBS Morning Command 15 brief.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--focus-limit", type=int, default=5)
    args = parser.parse_args()

    env = load_env()
    tasks, base_url = fetch_roby_tasks(env, limit=max(1, args.limit), max_pages=max(1, args.max_pages))
    payload = build_payload(tasks, base_url=base_url, focus_limit=args.focus_limit)
    markdown = render_markdown(payload)
    payload["paths"] = {"json": str(LATEST_JSON), "markdown": str(LATEST_MD), "history": str(HISTORY_JSONL)}
    if not args.no_write:
        write_outputs(payload, markdown)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

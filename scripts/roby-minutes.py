#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import time
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import urllib.error

STATE_PATH = Path.home() / ".openclaw" / "roby" / "minutes_state.json"
RUN_LOG_PATH = Path.home() / ".openclaw" / "roby" / "minutes_runs.jsonl"
DEBUG_LOG_PATH = Path.home() / ".openclaw" / "roby" / "minutes_debug.jsonl"
NEURONIC_LOG_PATH = Path.home() / ".openclaw" / "roby" / "neuronic_import_runs.jsonl"
ENV_PATH = Path.home() / ".openclaw" / ".env"
NOTION_KEY_PATH = Path.home() / ".config" / "notion" / "api_key"

DEFAULT_DAYS = 14
DEFAULT_MAX = 200

JST = timezone(timedelta(hours=9))


def load_env() -> Dict[str, str]:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            env[key] = val
    return env


def load_notion_key(env: Dict[str, str]) -> Optional[str]:
    key = env.get("NOTION_API_KEY") or env.get("NOTION_TOKEN") or env.get("NOTION_KEY")
    if key:
        return key
    if NOTION_KEY_PATH.exists():
        return NOTION_KEY_PATH.read_text(encoding="utf-8").strip()
    return None


def ensure_state() -> Dict[str, Any]:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"notion": {}, "gdocs": {}, "updated_at": None}
    return {"notion": {}, "gdocs": {}, "updated_at": None}


def save_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = int(time.time())
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def log_run(entry: Dict[str, Any]) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def normalize_notion_id(raw: str) -> str:
    if not raw:
        return ""
    m = re.search(r"([0-9a-fA-F]{32})", raw)
    if m:
        return m.group(1)
    m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", raw)
    if m:
        return m.group(1).replace("-", "")
    return raw


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def notion_request(method: str, url: str, token: str, version: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": version,
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "ignore")
        return {"ok": True, "data": json.loads(body) if body else {}}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        return {"ok": False, "status": e.code, "error": detail}
    except Exception as e:
        return {"ok": False, "status": None, "error": str(e)}


def notion_query_database_any(db_id: str, token: str, version: str, filter_payload: Optional[Dict[str, Any]] = None, max_pages: int = 200) -> Tuple[bool, List[Dict[str, Any]], str]:
    db_id = normalize_notion_id(db_id)
    results: List[Dict[str, Any]] = []
    for endpoint, use_version in [("data_sources", version), ("databases", "2022-06-28")]:
        start_cursor = None
        while True:
            payload: Dict[str, Any] = {"page_size": 100}
            if filter_payload:
                payload["filter"] = filter_payload
            if start_cursor:
                payload["start_cursor"] = start_cursor
            url = f"https://api.notion.com/v1/{endpoint}/{db_id}/query"
            resp = notion_request("POST", url, token, use_version, payload)
            if not resp.get("ok"):
                break
            data = resp.get("data", {})
            batch = data.get("results", [])
            results.extend(batch)
            if max_pages and len(results) >= max_pages:
                return True, results[:max_pages], endpoint
            if not data.get("has_more"):
                return True, results, endpoint
            start_cursor = data.get("next_cursor")
        results = []
    return False, [], ""


def notion_list_child_databases(page_id: str, token: str, version: str) -> List[Dict[str, str]]:
    page_id = normalize_notion_id(page_id)
    dbs: List[Dict[str, str]] = []
    start_cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
        if start_cursor:
            url += f"&start_cursor={start_cursor}"
        resp = notion_request("GET", url, token, version)
        if not resp.get("ok"):
            return dbs
        data = resp.get("data", {})
        for block in data.get("results", []):
            btype = block.get("type")
            if btype == "child_database":
                child = block.get("child_database", {})
                dbs.append({"id": block.get("id", ""), "title": child.get("title", "")})
            elif btype == "child_data_source":
                child = block.get("child_data_source", {})
                dbs.append({"id": block.get("id", ""), "title": child.get("title", "")})
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    return dbs


def extract_page_title(page: Dict[str, Any]) -> str:
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    return page.get("title") or "(untitled)"


def list_database_pages(db_id: str, token: str, version: str, since_iso: Optional[str], max_pages: int) -> List[Dict[str, Any]]:
    filter_payload = None
    if since_iso:
        filter_payload = {
            "timestamp": "last_edited_time",
            "last_edited_time": {"on_or_after": since_iso},
        }
    ok, pages, _ = notion_query_database_any(db_id, token, version, filter_payload, max_pages=max_pages)
    if not ok:
        return []
    return pages


def build_notion_structure(root_id: str, token: str, version: str, max_projects: int) -> Dict[str, Any]:
    root_id = normalize_notion_id(root_id)
    structure = {
        "root_id": root_id,
        "generated_at": datetime.now(JST).isoformat(),
        "projects": [],
        "databases": [],
    }

    ok, root_pages, _ = notion_query_database_any(root_id, token, version, None, max_pages=max_projects)
    if not ok:
        # treat root as a page
        child_dbs = notion_list_child_databases(root_id, token, version)
        for db in child_dbs:
            title = db.get("title") or "(untitled)"
            structure["databases"].append({
                "id": db.get("id"),
                "title": title,
                "project": title,
                "project_page_id": root_id,
            })
        return structure

    for page in root_pages:
        project_title = extract_page_title(page)
        project_page_id = page.get("id", "")
        child_dbs = notion_list_child_databases(project_page_id, token, version)
        if not child_dbs:
            continue
        project_entry = {
            "project": project_title,
            "project_page_id": project_page_id,
            "databases": [],
        }
        for db in child_dbs:
            db_id = db.get("id", "")
            db_title = db.get("title") or project_title
            project_entry["databases"].append({"id": db_id, "title": db_title})
            structure["databases"].append({
                "id": db_id,
                "title": db_title,
                "project": project_title,
                "project_page_id": project_page_id,
            })
        structure["projects"].append(project_entry)
    return structure


def load_cached_structure(root_id: str) -> Optional[Dict[str, Any]]:
    cache_path = STATE_PATH.parent / "notion_structure.json"
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("root_id") != normalize_notion_id(root_id):
        return None
    return data


def save_cached_structure(data: Dict[str, Any]) -> None:
    cache_path = STATE_PATH.parent / "notion_structure.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_page_text(page_id: str, token: str, version: str, max_blocks: int = 400) -> str:
    page_id = normalize_notion_id(page_id)
    lines: List[str] = []
    start_cursor = None
    count = 0
    while True:
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
        if start_cursor:
            url += f"&start_cursor={start_cursor}"
        resp = notion_request("GET", url, token, version)
        if not resp.get("ok"):
            break
        data = resp.get("data", {})
        for block in data.get("results", []):
            count += 1
            if count > max_blocks:
                break
            text = block_to_text(block)
            if text:
                lines.append(text)
        if count > max_blocks:
            break
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    return "\n".join(lines).strip()


def block_to_text(block: Dict[str, Any]) -> str:
    btype = block.get("type")
    if not btype:
        return ""
    if btype in ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item", "to_do", "toggle", "quote", "callout"):
        rich = block.get(btype, {}).get("rich_text", [])
        txt = "".join(t.get("plain_text", "") for t in rich).strip()
        prefix = ""
        if btype.startswith("heading"):
            prefix = "# "
        elif btype == "bulleted_list_item":
            prefix = "- "
        elif btype == "numbered_list_item":
            prefix = "1. "
        elif btype == "to_do":
            prefix = "[ ] "
        return f"{prefix}{txt}".strip()
    if btype == "code":
        return block.get("code", {}).get("text", "")
    return ""


ACTION_HINTS = [
    "確認",
    "対応",
    "実施",
    "作成",
    "共有",
    "調整",
    "依頼",
    "連携",
    "実装",
    "準備",
    "提出",
    "送付",
    "ヒアリング",
    "追跡",
    "検討",
    "設定",
    "修正",
]

STATUS_ONLY_HINTS = [
    "進捗",
    "現状",
    "報告",
    "完了",
    "問題なし",
    "備考",
    "要確認",
    "背景",
    "所感",
    "振り返り",
]

GENERIC_PROJECT_NAMES = {
    "",
    "TOKIWAGI",
    "TOKIWAGI_MASTER",
    "TOKIWAGIインナー議事録",
    "基礎情報",
    "GDocs",
}


def _clean_line(line: str) -> str:
    s = re.sub(r"^\s*[-*・●◯□■]+\s*", "", line.strip())
    s = re.sub(r"^\s*\d+[.)]\s*", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _line_looks_like_project_heading(line: str, known_projects: List[str]) -> Optional[str]:
    s = _clean_line(line)
    if not s:
        return None
    for p in sorted(set([x for x in known_projects if x]), key=len, reverse=True):
        if p and p in s:
            return p
    m = re.match(r"^([A-Za-z0-9一-龥ぁ-んァ-ヶー！!・／/ 　]+)[：:]\s*$", s)
    if m:
        return m.group(1).strip()
    return None


def _line_looks_actionable(line: str) -> bool:
    s = _clean_line(line)
    if not s or len(s) < 4:
        return False
    if any(k in s for k in STATUS_ONLY_HINTS) and not any(a in s for a in ACTION_HINTS):
        return False
    if any(a in s for a in ACTION_HINTS):
        return True
    if re.search(r"(今週|来週|今月|来月|まで|予定|必要|すべき|したい)", s):
        return True
    return False


def _has_action_signal(text: str) -> bool:
    s = _clean_line(text)
    if not s:
        return False
    if any(a in s for a in ACTION_HINTS):
        return True
    if re.search(r"(まで|期限|予定|必要|依頼|確認|対応|実施|作成|調整|共有|連携|設定|修正|実装|準備|追跡|ヒアリング|検討)", s):
        return True
    if re.search(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", s):
        return True
    return False


def _looks_noise_task_title(title: str) -> bool:
    s = _clean_line(title)
    if not s:
        return True
    if len(s) < 4:
        return True
    if re.fullmatch(r"[0-9０-９.,:：\- ]+", s):
        return True
    if any(k in s for k in STATUS_ONLY_HINTS) and not _has_action_signal(s):
        return True
    if re.match(r"^(現状|進捗|報告|備考|要確認|背景|所感|振り返り)([:：].*)?$", s) and not _has_action_signal(s):
        return True
    if s.endswith("について") and not _has_action_signal(s):
        return True
    return False


def _infer_project_from_text(text: str, known_projects: List[str]) -> Optional[str]:
    if not text:
        return None
    for p in sorted(set([x for x in known_projects if x and x not in GENERIC_PROJECT_NAMES]), key=len, reverse=True):
        if p in text:
            return p
    return None


def _resolve_project_name(
    project: str,
    title: str,
    note: str,
    source_title: str,
    default_project: str,
    known_projects: List[str],
) -> str:
    p = (project or "").strip()
    if p and p not in GENERIC_PROJECT_NAMES:
        return p
    inferred = _infer_project_from_text(" ".join([title or "", note or "", source_title or ""]), known_projects)
    if inferred:
        return inferred
    if default_project and default_project not in GENERIC_PROJECT_NAMES:
        return default_project
    return p or default_project or "TOKIWAGI"


def sanitize_extracted_tasks(
    extracted: List[Dict[str, Any]],
    default_project: str,
    known_projects: List[str],
    source_title: str,
    max_tasks_per_doc: int = 30,
    max_subtasks_per_parent: int = 8,
) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _fingerprint(title: str, project: str, due_date: str, note: str) -> str:
        return "|".join([
            _clean_line(title).lower(),
            (project or "").strip().lower(),
            (due_date or "").strip(),
            _clean_line((note or "")[:120]).lower(),
        ])

    for item in extracted:
        if not isinstance(item, dict):
            continue
        title = _clean_line(str(item.get("title") or ""))
        note = (item.get("note") or "").strip()
        due_date = (item.get("due_date") or "").strip()
        assignee = (item.get("assignee") or "").strip() or "私"
        project = _resolve_project_name(
            str(item.get("project") or ""),
            title,
            note,
            source_title,
            default_project,
            known_projects,
        )

        raw_subtasks = item.get("subtasks") or item.get("children") or []
        subtasks: List[Dict[str, Any]] = []
        if isinstance(raw_subtasks, list):
            for sub in raw_subtasks:
                if not isinstance(sub, dict):
                    continue
                st = _clean_line(str(sub.get("title") or ""))
                sn = (sub.get("note") or "").strip()
                sd = (sub.get("due_date") or "").strip()
                sa = (sub.get("assignee") or "").strip() or assignee
                sp = _resolve_project_name(
                    str(sub.get("project") or project),
                    st,
                    sn,
                    source_title,
                    project,
                    known_projects,
                )
                if _looks_noise_task_title(st) and not _has_action_signal(st) and not sd:
                    continue
                if not st:
                    continue
                sub_fp = _fingerprint(st, sp, sd, sn)
                if sub_fp in seen:
                    continue
                seen.add(sub_fp)
                if max_subtasks_per_parent > 0 and len(subtasks) >= max_subtasks_per_parent:
                    continue
                subtasks.append({
                    "title": st,
                    "project": sp,
                    "due_date": sd,
                    "assignee": sa,
                    "note": sn,
                })

        if subtasks:
            parent_title = title
            if (not parent_title) or (_looks_noise_task_title(parent_title) and not _has_action_signal(parent_title)):
                parent_title = f"{project} 対応タスク"
            cleaned.append({
                "title": parent_title[:120],
                "project": project,
                "due_date": due_date,
                "assignee": assignee,
                "note": note,
                "subtasks": subtasks[:max_subtasks_per_parent] if max_subtasks_per_parent > 0 else subtasks,
            })
            continue

        # Leaf task
        if not title:
            continue
        if _looks_noise_task_title(title) and not _has_action_signal(title) and not due_date:
            continue
        fp = _fingerprint(title, project, due_date, note)
        if fp in seen:
            continue
        seen.add(fp)
        cleaned.append({
            "title": title[:120],
            "project": project,
            "due_date": due_date,
            "assignee": assignee,
            "note": note,
        })

    if max_tasks_per_doc > 0:
        return cleaned[:max_tasks_per_doc]
    return cleaned


def heuristic_tasks_from_text(
    text: str,
    default_project: str,
    known_projects: List[str],
    max_projects: int = 8,
    max_items_per_project: int = 8,
) -> List[Dict[str, Any]]:
    groups: Dict[str, List[str]] = {}
    current_project = default_project
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = _line_looks_like_project_heading(line, known_projects)
        if heading:
            current_project = heading
            groups.setdefault(current_project, [])
            continue
        if _line_looks_actionable(line):
            groups.setdefault(current_project, [])
            cleaned = _clean_line(line)
            if cleaned not in groups[current_project]:
                groups[current_project].append(cleaned)

    tasks: List[Dict[str, Any]] = []
    for project, items in list(groups.items())[:max_projects]:
        if not items:
            continue
        if len(items) == 1:
            tasks.append({
                "title": items[0][:120],
                "due_date": "",
                "project": project or default_project,
                "assignee": "私",
                "note": "Heuristic extraction",
            })
            continue
        subtasks = []
        for item in items[:max_items_per_project]:
            subtasks.append({
                "title": item[:120],
                "due_date": "",
                "project": project or default_project,
                "assignee": "私",
                "note": "Heuristic extraction",
            })
        tasks.append({
            "title": f"{project or default_project} 対応タスク",
            "due_date": "",
            "project": project or default_project,
            "assignee": "私",
            "note": "Heuristic grouped extraction",
            "subtasks": subtasks,
        })
    return tasks


def drive_search_docs(folder_id: str, env: Dict[str, str], account: str, since_iso: str, max_docs: int) -> List[Dict[str, Any]]:
    folder_id = folder_id.strip()
    query = (
        f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document' "
        f"and modifiedTime >= '{since_iso}' and trashed=false"
    )
    cmd = [
        "gog",
        "drive",
        "search",
        query,
        "--raw-query",
        "--json",
        "--results-only",
        "--max",
        str(max_docs),
        "--no-input",
    ]
    if account:
        cmd += ["--account", account]
    out = subprocess.check_output(cmd, env=env, timeout=60)
    return json.loads(out)


def export_doc_text(doc_id: str, env: Dict[str, str], account: str) -> str:
    out_path = Path("/tmp") / f"roby_doc_{doc_id}.txt"
    cmd = ["gog", "docs", "export", doc_id, "--format", "txt", "--out", str(out_path), "--no-input"]
    if account:
        cmd += ["--account", account]
    subprocess.check_call(cmd, env=env, timeout=60)
    text = out_path.read_text(encoding="utf-8", errors="ignore")
    try:
        out_path.unlink()
    except Exception:
        pass
    return text.strip()


def _extract_json_value(data: Dict[str, Any]) -> str:
    for key in ("summary", "output", "text", "result"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _parse_jsonish_text(raw: str) -> Any:
    if not raw:
        return None
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            return None


def _run_gemini_json_prompt(
    text: str,
    prompt: str,
    env: Dict[str, str],
    *,
    max_output_tokens: str,
    length: str,
    timeout_sec: int,
) -> Tuple[Any, str]:
    cmd = [
        "summarize",
        "-",
        "--json",
        "--plain",
        "--metrics",
        "off",
        "--model",
        env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview"),
        "--length",
        length,
        "--force-summary",
        "--prompt",
        prompt,
        "--max-output-tokens",
        max_output_tokens,
    ]
    out = subprocess.check_output(cmd, input=text.encode("utf-8"), env=env, timeout=timeout_sec)
    data = json.loads(out)
    raw = _extract_json_value(data)
    return _parse_jsonish_text(raw), raw


def review_minutes_with_gemini(
    text: str,
    env: Dict[str, str],
    default_project: str,
    known_projects: List[str],
    today: str,
) -> Tuple[Optional[Dict[str, Any]], str]:
    known = ", ".join(sorted(set([p for p in known_projects if p]))[:30])
    prompt = (
        "You are reviewing Japanese meeting minutes for task extraction. "
        "Return ONLY JSON object with keys: summary, project_sections, cross_project_actions, noise_notes. "
        "project_sections is an array of {project, key_points, action_candidates}. "
        "action_candidates is an array of short actionable statements only (no status-only notes, no opinions, no background-only memos). "
        "Treat progress updates, reflections, criticism, and contextual explanations as noise unless they contain a concrete next action / request / deadline. "
        "Preserve project names explicitly and prefer known project names when text indicates them. "
        "cross_project_actions is an array of short actionable statements. "
        "noise_notes is an array of non-action memo lines that should not become tasks. "
        f"Today(JST): {today}. Default project: {default_project}. Known projects: {known}."
    )
    parsed, raw = _run_gemini_json_prompt(
        text,
        prompt,
        env,
        max_output_tokens=env.get("MINUTES_REVIEW_MAX_TOKENS", "2200"),
        length=env.get("MINUTES_REVIEW_LENGTH", "xl"),
        timeout_sec=int(env.get("MINUTES_REVIEW_TIMEOUT_SEC", "150")),
    )
    return (parsed if isinstance(parsed, dict) else None), raw


def extract_tasks_with_gemini_from_review(
    review: Dict[str, Any],
    env: Dict[str, str],
    default_project: str,
    known_projects: List[str],
    today: str,
) -> Tuple[List[Dict[str, Any]], str]:
    known = ", ".join(sorted(set([p for p in known_projects if p]))[:30])
    review_text = json.dumps(review, ensure_ascii=False)
    prompt = (
        "Convert the reviewed meeting summary into actionable tasks. "
        "Return ONLY a JSON array. Each item has keys: title, due_date, project, assignee, note, subtasks(optional). "
        "Use parent+subtasks when multiple actions belong to one project or theme. "
        "Ignore items listed in noise_notes. "
        "Do NOT emit status summaries as tasks (e.g., '進捗', '現状', '備考', '要確認' by themselves). "
        "Prefer concrete verb-led tasks. If a line is ambiguous, keep it in note under a parent task rather than creating many vague subtasks. "
        "Project must be one of Known projects when applicable; for internal MTG items, infer the specific project from project_sections instead of using a generic label. "
        "due_date must be YYYY-MM-DD or empty string. "
        "If due date is relative, infer date using Today(JST). "
        f"Today(JST): {today}. Default project: {default_project}. Known projects: {known}."
    )
    parsed, raw = _run_gemini_json_prompt(
        review_text,
        prompt,
        env,
        max_output_tokens=env.get("MINUTES_TASKS_MAX_TOKENS", "2600"),
        length=env.get("MINUTES_TASKS_LENGTH", "xxl"),
        timeout_sec=int(env.get("MINUTES_TASKS_TIMEOUT_SEC", "180")),
    )
    if isinstance(parsed, list):
        return parsed, raw
    return [], raw


def summarize_tasks(text: str, env: Dict[str, str], default_project: str, known_projects: List[str], today: str) -> Tuple[List[Dict[str, Any]], str]:
    known = ", ".join(sorted(set([p for p in known_projects if p]))[:25])
    review, review_raw = review_minutes_with_gemini(text, env, default_project, known_projects, today)
    if review:
        tasks, task_raw = extract_tasks_with_gemini_from_review(review, env, default_project, known_projects, today)
        if tasks:
            return tasks, json.dumps(
                {
                    "pipeline": "gemini_two_stage",
                    "review_raw": review_raw[:1200],
                    "task_raw": task_raw[:1200],
                },
                ensure_ascii=False,
            )

    # Fallback: one-stage extraction (existing behavior) if two-stage fails/truncates.
    prompt = (
        "Extract actionable tasks from the meeting minutes. "
        "Ignore pure status notes, commentary, criticism, retrospective feedback, and context-only memo lines. "
        "If tasks are related, group them under a parent task with a `subtasks` array. "
        "Return ONLY a JSON array. Each item has keys: title, due_date, project, assignee, note, subtasks (optional). "
        "Each subtask uses the same schema (title, due_date, project, assignee, note). "
        "due_date must be YYYY-MM-DD or empty string. "
        f"Today is {today} (JST). "
        f"Default project: {default_project}. "
        f"Known projects: {known}. "
        "Use the most appropriate project name if indicated. If not sure, use the default project. "
        "Prefer fewer high-quality actionable tasks over many vague bullets."
    )
    parsed, raw = _run_gemini_json_prompt(
        text,
        prompt,
        env,
        max_output_tokens=env.get("MINUTES_SUMMARIZE_MAX_TOKENS", "1600"),
        length=env.get("MINUTES_SUMMARIZE_LENGTH", "xxl"),
        timeout_sec=int(env.get("MINUTES_SUMMARIZE_TIMEOUT_SEC", "120")),
    )
    if isinstance(parsed, list):
        return parsed, raw
    return [], raw


def _stable_origin_id(task: Dict[str, Any], source_id: str) -> str:
    raw = "|".join([
        (task.get("title") or "").strip(),
        (task.get("project") or "").strip(),
        (task.get("due_date") or "").strip(),
        (task.get("assignee") or "").strip(),
        source_id,
    ])
    sha1_12 = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"roby:auto:{sha1_12}"


def _dedupe_tags(tags: List[str]) -> List[str]:
    seen = set()
    out = []
    for t in tags:
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _normalize_task_item(item: Dict[str, Any], default_project: str) -> Dict[str, Any]:
    title = (item.get("title") or "").strip()
    project = (item.get("project") or "").strip() or default_project
    due_date = (item.get("due_date") or "").strip()
    if due_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", due_date):
        due_date = ""
    assignee = (item.get("assignee") or "").strip() or "私"
    note = (item.get("note") or "").strip()
    return {
        "title": title,
        "project": project,
        "due_date": due_date,
        "assignee": assignee,
        "note": note,
    }


def build_neuronic_tasks(
    extracted: List[Dict[str, Any]],
    source: str,
    source_title: str,
    source_url: str,
    default_project: str,
    source_id: str,
) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    group_index = 0
    for item in extracted:
        normalized = _normalize_task_item(item, default_project)
        title = normalized.get("title")
        if not title:
            continue
        subtasks = item.get("subtasks") or item.get("children") or []

        note = (
            (normalized.get("note") + "\n\n" if normalized.get("note") else "")
            + f"Source: {source}\n"
            + f"Title: {source_title}\n"
            + f"URL: {source_url}"
        )
        tags = _dedupe_tags([
            f"source:{source}",
            f"project:{normalized.get('project')}",
            f"assignee:{normalized.get('assignee')}",
        ])

        parent_task = {
            "title": title,
            "project": normalized.get("project"),
            "due_date": normalized.get("due_date"),
            "assignee": normalized.get("assignee"),
            "note": note,
            "source": "roby",
            "status": "inbox",
            "priority": 1,
            "tags": tags,
            "parent_origin_id": None,
            "sibling_order": group_index,
            "outline_path": str(group_index),
        }
        parent_origin = _stable_origin_id(parent_task, f"{source_id}|parent|{group_index}")
        parent_task["origin_id"] = parent_origin

        if subtasks:
            group_tag = f"group:{parent_origin}"
            parent_task["tags"] = _dedupe_tags(parent_task["tags"] + [group_tag])
            tasks.append(parent_task)
            for sub_idx, sub in enumerate(subtasks):
                sub_norm = _normalize_task_item(sub, normalized.get("project") or default_project)
                if not sub_norm.get("title"):
                    continue
                sub_note = (
                    (sub_norm.get("note") + "\n\n" if sub_norm.get("note") else "")
                    + f"Parent: {title}\n"
                    + f"Source: {source}\n"
                    + f"Title: {source_title}\n"
                    + f"URL: {source_url}"
                )
                sub_tags = _dedupe_tags([
                    f"source:{source}",
                    f"project:{sub_norm.get('project')}",
                    f"assignee:{sub_norm.get('assignee')}",
                    group_tag,
                ])
                child_task = {
                    "title": sub_norm.get("title"),
                    "project": sub_norm.get("project"),
                    "due_date": sub_norm.get("due_date"),
                    "assignee": sub_norm.get("assignee"),
                    "note": sub_note,
                    "source": "roby",
                    "status": "inbox",
                    "priority": 1,
                    "tags": sub_tags,
                    "parent_origin_id": parent_origin,
                    "sibling_order": sub_idx,
                    "outline_path": f"{group_index}/{sub_idx}",
                }
                child_task["origin_id"] = _stable_origin_id(
                    child_task, f"{source_id}|child|{group_index}|{sub_idx}"
                )
                tasks.append(child_task)
        else:
            tasks.append(parent_task)
        group_index += 1
    return tasks


def _send_neuronic_once(tasks: List[Dict[str, Any]], env: Dict[str, str]) -> Dict[str, Any]:
    url = env.get("NEURONIC_URL", "http://127.0.0.1:5174/api/v1/tasks/import")
    fallback_url = env.get("NEURONIC_FALLBACK_URL", "http://127.0.0.1:5174/api/v1/tasks/bulk")
    token = env.get("NEURONIC_TOKEN") or env.get("TASKD_AUTH_TOKEN")
    payload_items = []
    for item in tasks:
        row = dict(item)
        # Compatibility: send both snake_case and camelCase for taskd variants.
        if "parent_origin_id" in row:
            row["parentOriginId"] = row.get("parent_origin_id")
        if "sibling_order" in row:
            row["siblingOrder"] = row.get("sibling_order")
        if "outline_path" in row:
            row["outlinePath"] = row.get("outline_path")
        payload_items.append(row)
    payload = {"items": payload_items}
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        header_name = env.get("NEURONIC_AUTH_HEADER", "Authorization")
        headers[header_name] = f"Bearer {token}"

    def _post(target_url: str) -> Dict[str, Any]:
        req = urllib.request.Request(target_url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            status_code = getattr(resp, "status", 200)
            body = resp.read().decode("utf-8", "ignore")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"response": body}
        return {"ok": True, "status_code": status_code, "body": parsed}

    try:
        res = _post(url)
        res["endpoint_used"] = "/api/v1/tasks/import"
        res["fallback_used"] = False
        return res
    except urllib.error.HTTPError as e:
        if e.code == 404 and url.endswith("/tasks/import"):
            try:
                res = _post(fallback_url)
                res["endpoint_used"] = "/api/v1/tasks/bulk"
                res["fallback_used"] = True
                return res
            except urllib.error.HTTPError as e2:
                return {
                    "ok": False,
                    "status_code": e2.code,
                    "error": f"HTTP {e2.code}",
                    "detail": e2.read().decode("utf-8", "ignore"),
                    "endpoint_used": "/api/v1/tasks/bulk",
                    "fallback_used": True,
                }
        return {
            "ok": False,
            "status_code": e.code,
            "error": f"HTTP {e.code}",
            "detail": e.read().decode("utf-8", "ignore"),
            "endpoint_used": "/api/v1/tasks/import",
            "fallback_used": False,
        }
    except Exception as e:
        return {
            "ok": False,
            "status_code": None,
            "error": str(e),
            "endpoint_used": "/api/v1/tasks/import",
            "fallback_used": False,
        }


def _is_payload_too_large(resp: Dict[str, Any]) -> bool:
    if not isinstance(resp, dict):
        return False
    body = resp.get("body", {}) if isinstance(resp.get("body"), dict) else {}
    err = (
        f"{resp.get('error', '')} {resp.get('detail', '')} {resp.get('reason', '')} "
        f"{body.get('error', '')} {body.get('detail', '')} {body.get('reason', '')}"
    ).lower()
    return "413" in err or "payload too large" in err or "request entity too large" in err


def _count_payload_meta(items: List[Dict[str, Any]]) -> Tuple[int, int]:
    parent_items = 0
    order_items = 0
    for it in items:
        parent_val = it.get("parent_origin_id", it.get("parentOriginId"))
        if parent_val is not None and str(parent_val).strip() != "":
            parent_items += 1
        if ("sibling_order" in it and it.get("sibling_order") is not None) or (
            "siblingOrder" in it and it.get("siblingOrder") is not None
        ):
            order_items += 1
    return parent_items, order_items


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _format_neuronic_cli_logs(result: Dict[str, Any], verbose: bool = False) -> List[str]:
    lines: List[str] = []
    endpoint = result.get("endpoint_used") or "(unknown)"
    fallback = bool(result.get("fallback_used"))
    items_sent = int(result.get("items_sent", 0) or 0)
    parent_items = int(result.get("items_with_parent", 0) or 0)
    order_items = int(result.get("items_with_order", 0) or 0)
    created = int(result.get("created", 0) or 0)
    updated = int(result.get("updated", 0) or 0)
    skipped = int(result.get("skipped", 0) or 0)
    error_count = int(result.get("error_count", 0) or 0)
    status_code = result.get("status_code")

    if not result.get("ok", True):
        lines.append(
            f"[neuronic] import failed: status={status_code or '?'} endpoint={endpoint}"
            + (" fallback=true" if fallback else "")
        )
        return lines

    if verbose:
        lines.append(
            "[neuronic] send "
            f"items={items_sent} parent_items={parent_items} ordered_items={order_items} "
            f"endpoint={endpoint}" + (" fallback=true" if fallback else "")
        )

    hierarchy_applied = result.get("hierarchy_applied", None)
    order_applied = result.get("order_applied", None)
    flags_available = (hierarchy_applied is not None) or (order_applied is not None)

    if flags_available:
        lines.append(
            "[neuronic] import ok: "
            f"created={created} updated={updated} skipped={skipped} errors={error_count} "
            f"hierarchy={str(hierarchy_applied).lower()} "
            f"order={str(order_applied).lower()} "
            f"endpoint={endpoint}" + (" fallback=true" if fallback else "")
        )
    else:
        lines.append(
            "[neuronic] import ok (legacy response): "
            f"created={created} updated={updated} skipped={skipped} errors={error_count} "
            f"endpoint={endpoint}" + (" fallback=true" if fallback else "")
        )
        if fallback:
            lines.append(
                "[neuronic] import fallback to /api/v1/tasks/bulk "
                "(hierarchy/order response flags unavailable)"
            )

    for msg in result.get("warning_messages", []) or []:
        lines.append(f"[neuronic] warning: {msg}")

    return lines


def _task_group_key(item: Dict[str, Any]) -> str:
    parent = item.get("parent_origin_id")
    if parent:
        return f"group:{parent}"
    return f"group:{item.get('origin_id', '')}"


def _split_grouped_batches(tasks: List[Dict[str, Any]], default_batch: int, max_batch_bytes: int) -> List[List[Dict[str, Any]]]:
    groups: List[List[Dict[str, Any]]] = []
    current_group: List[Dict[str, Any]] = []
    current_key = None
    for item in tasks:
        key = _task_group_key(item)
        if current_key is None or key == current_key:
            current_group.append(item)
            current_key = key
            continue
        groups.append(current_group)
        current_group = [item]
        current_key = key
    if current_group:
        groups.append(current_group)

    batches: List[List[Dict[str, Any]]] = []
    batch: List[Dict[str, Any]] = []
    batch_bytes = 0
    for group in groups:
        group_bytes = sum(len(json.dumps(it, ensure_ascii=False).encode("utf-8")) + 2 for it in group)
        if batch and (len(batch) + len(group) > default_batch or batch_bytes + group_bytes > max_batch_bytes):
            batches.append(batch)
            batch = []
            batch_bytes = 0
        # If a single group itself is too large, still enqueue it; recursive split handles it.
        batch.extend(group)
        batch_bytes += group_bytes
    if batch:
        batches.append(batch)
    return batches


def send_neuronic(tasks: List[Dict[str, Any]], env: Dict[str, str]) -> Dict[str, Any]:
    if not tasks:
        return {"created": 0, "updated": 0, "skipped": 0}

    default_batch = int(env.get("NEURONIC_BATCH_SIZE", "20"))
    max_batch_bytes = int(env.get("NEURONIC_MAX_BATCH_BYTES", "90000"))
    queue: List[List[Dict[str, Any]]] = _split_grouped_batches(tasks, default_batch, max_batch_bytes)
    verbose = env.get("ROBY_NEURONIC_VERBOSE", "0") == "1"
    items_with_parent, items_with_order = _count_payload_meta(tasks)

    aggregate: Dict[str, Any] = {
        "ok": True,
        "status_code": 200,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "error_count": 0,
        "batches": 0,
        "items_sent": len(tasks),
        "items_with_parent": items_with_parent,
        "items_with_order": items_with_order,
        "endpoint_used": None,
        "fallback_used": False,
        "hierarchy_applied": None,
        "order_applied": None,
        "warning_messages": [],
    }
    endpoints_seen = set()
    hierarchy_flags_seen: List[bool] = []
    order_flags_seen: List[bool] = []
    legacy_flags_unavailable = False

    while queue:
        current = queue.pop(0)
        resp = _send_neuronic_once(current, env)
        if _is_payload_too_large(resp) and len(current) > 1:
            mid = max(1, len(current) // 2)
            queue.insert(0, current[mid:])
            queue.insert(0, current[:mid])
            continue

        aggregate["batches"] += 1
        endpoints_seen.add(resp.get("endpoint_used") or "(unknown)")
        aggregate["fallback_used"] = bool(aggregate["fallback_used"]) or bool(resp.get("fallback_used"))

        if not resp.get("ok", False):
            aggregate.setdefault("errors", []).append(
                {
                    "reason": resp.get("detail") or resp.get("error"),
                    "batch_size": len(current),
                    "endpoint": resp.get("endpoint_used"),
                }
            )
            aggregate["error_count"] = int(aggregate.get("error_count", 0)) + 1
            aggregate["ok"] = False
            aggregate["error"] = resp.get("error")
            aggregate["detail"] = resp.get("detail")
            aggregate["status_code"] = resp.get("status_code")
            continue

        body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        for key in ("created", "updated", "skipped", "hierarchy_updated", "order_updated"):
            if key in body and isinstance(body.get(key), int):
                aggregate[key] = int(aggregate.get(key, 0)) + int(body.get(key, 0))
        if isinstance(body.get("errors"), list):
            aggregate.setdefault("errors", []).extend(body.get("errors", []))
            aggregate["error_count"] = len(aggregate.get("errors", []))
        if "hierarchy_applied" in body:
            if body.get("hierarchy_applied") is not None:
                hierarchy_flags_seen.append(bool(body.get("hierarchy_applied")))
        else:
            legacy_flags_unavailable = True
        if "order_applied" in body:
            if body.get("order_applied") is not None:
                order_flags_seen.append(bool(body.get("order_applied")))
        else:
            legacy_flags_unavailable = True

    if len(endpoints_seen) == 1:
        aggregate["endpoint_used"] = next(iter(endpoints_seen))
    elif len(endpoints_seen) > 1:
        aggregate["endpoint_used"] = "mixed"

    if hierarchy_flags_seen:
        aggregate["hierarchy_applied"] = all(hierarchy_flags_seen)
    elif not legacy_flags_unavailable:
        aggregate["hierarchy_applied"] = False
    else:
        aggregate["hierarchy_applied"] = None

    if order_flags_seen:
        aggregate["order_applied"] = all(order_flags_seen)
    elif not legacy_flags_unavailable:
        aggregate["order_applied"] = False
    else:
        aggregate["order_applied"] = None

    if aggregate["items_with_parent"] > 0 and aggregate.get("hierarchy_applied") is False:
        aggregate["warning_messages"].append(
            f"parent tasks were sent ({aggregate['items_with_parent']}) but hierarchy_applied=false"
        )
    if aggregate["items_with_order"] > 0 and aggregate.get("order_applied") is False:
        aggregate["warning_messages"].append(
            f"sibling order was sent ({aggregate['items_with_order']}) but order_applied=false"
        )

    if not aggregate.get("errors"):
        aggregate.pop("errors", None)

    for line in _format_neuronic_cli_logs(aggregate, verbose=verbose):
        print(line)

    _append_jsonl(
        NEURONIC_LOG_PATH,
        {
            "event": "neuronic_import_result",
            "timestamp": datetime.now(JST).isoformat(),
            "endpoint_used": aggregate.get("endpoint_used"),
            "fallback_used": aggregate.get("fallback_used", False),
            "status_code": aggregate.get("status_code"),
            "ok": aggregate.get("ok", True),
            "items_sent": aggregate.get("items_sent", 0),
            "items_with_parent": aggregate.get("items_with_parent", 0),
            "items_with_order": aggregate.get("items_with_order", 0),
            "created": aggregate.get("created", 0),
            "updated": aggregate.get("updated", 0),
            "skipped": aggregate.get("skipped", 0),
            "error_count": aggregate.get("error_count", 0),
            "hierarchy_applied": aggregate.get("hierarchy_applied"),
            "order_applied": aggregate.get("order_applied"),
            "warnings": aggregate.get("warning_messages", []),
        },
    )
    return aggregate


def send_slack(webhook_url: str, text: str) -> None:
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def apply_candidate_policy(candidates: List[Dict[str, Any]], policy: str) -> List[Dict[str, Any]]:
    p = (policy or "").strip().lower()
    if not p:
        return candidates
    if p in {"ops_default", "tokiwagi_master_plus_gdocs", "master+gdocs"}:
        filtered: List[Dict[str, Any]] = []
        for c in candidates:
            src = c.get("source")
            if src == "gdocs":
                filtered.append(c)
                continue
            if src != "notion":
                continue
            project = (c.get("project") or "").strip()
            db_title = (c.get("db_title") or "").strip()
            title = (c.get("title") or "").strip()
            if project == "TOKIWAGI_MASTER" or "TOKIWAGI_MASTER" in db_title or "社内定例" in title:
                filtered.append(c)
        return filtered
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--notion-root", default="")
    parser.add_argument("--drive-folder", default="")
    parser.add_argument("--account", default="")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--select", default="")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-notion", action="store_true")
    parser.add_argument("--skip-gdocs", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--policy", default="")
    args = parser.parse_args()

    env = load_env()
    if args.debug:
        env["ROBY_NEURONIC_VERBOSE"] = "1"
    notion_root = args.notion_root or env.get("TOKIWAGI_ROOT_ID") or env.get("NOTION_TOKIWAGI_ID", "")
    drive_folder = args.drive_folder or env.get("GDRIVE_MINUTES_FOLDER_ID", "")
    account = args.account or env.get("GOG_ACCOUNT", "")

    token = load_notion_key(env)
    if not token and not args.skip_notion:
        print("ERROR: Notion API key missing. Set NOTION_API_KEY or ~/.config/notion/api_key.")
        return 1

    since_dt = datetime.now(timezone.utc) - timedelta(days=args.days)
    since_iso = iso_z(since_dt)
    today_str = datetime.now(JST).strftime("%Y-%m-%d")

    state = ensure_state()
    processed_notion: Dict[str, str] = state.get("notion", {})
    processed_gdocs: Dict[str, str] = state.get("gdocs", {})

    summary = {
        "notion_pages": 0,
        "gdocs": 0,
        "tasks": 0,
        "neuronic_errors": 0,
    }

    candidates: List[Dict[str, Any]] = []
    known_projects: List[str] = []
    heuristic_used_docs = 0

    # Notion structure
    if not args.skip_notion and notion_root:
        structure = load_cached_structure(notion_root)
        if not structure or args.refresh:
            structure = build_notion_structure(notion_root, token, env.get("NOTION_VERSION", "2025-09-03"), args.max)
            save_cached_structure(structure)
        known_projects = [db.get("project") for db in structure.get("databases", [])]

        for db in structure.get("databases", []):
            db_id = db.get("id")
            project_name = db.get("project") or db.get("title") or "TOKIWAGI"
            db_title = db.get("title") or project_name
            pages = list_database_pages(db_id, token, env.get("NOTION_VERSION", "2025-09-03"), since_iso, args.max)
            for page in pages:
                page_id = page.get("id", "")
                last_edit = page.get("last_edited_time", "")
                if (not args.force) and page_id in processed_notion and processed_notion.get(page_id) == last_edit:
                    continue
                title = extract_page_title(page)
                candidates.append({
                    "source": "notion",
                    "project": project_name,
                    "db_title": db_title,
                    "page_id": page_id,
                    "title": title,
                    "updated": last_edit,
                    "url": page.get("url", ""),
                })

    # Google Docs
    if not args.skip_gdocs and drive_folder:
        docs = drive_search_docs(drive_folder, env, account, since_iso, args.max)
        for doc in docs:
            doc_id = doc.get("id", "")
            modified = doc.get("modifiedTime", "") or doc.get("modified_time", "")
            if (not args.force) and doc_id in processed_gdocs and processed_gdocs.get(doc_id) == modified:
                continue
            candidates.append({
                "source": "gdocs",
                "project": "",
                "doc_id": doc_id,
                "title": doc.get("name", "(untitled)"),
                "updated": modified,
                "url": f"https://docs.google.com/document/d/{doc_id}",
            })

    # sort by updated desc
    candidates.sort(key=lambda x: x.get("updated", ""), reverse=True)
    candidates = apply_candidate_policy(candidates, args.policy)

    if args.list or not args.run:
        for idx, item in enumerate(candidates, 1):
            label = f"{idx}. [{item.get('source')}] {item.get('title')}"
            if item.get("project"):
                label = f"{idx}. [{item.get('source')}] {item.get('project')} / {item.get('title')}"
            print(label)
            print(f"   - updated: {item.get('updated')}")
            print(f"   - {item.get('url')}")
        if not args.run:
            print("\nRun with --run to extract tasks. Use --select '1,3,5' to limit.")
            return 0

    selected = candidates
    if args.select:
        idxs = set()
        for part in re.split(r"[ ,]+", args.select.strip()):
            if not part:
                continue
            if part.isdigit():
                idxs.add(int(part))
        selected = [c for i, c in enumerate(candidates, 1) if i in idxs]

    all_tasks: List[Dict[str, Any]] = []
    debug_records: List[Dict[str, Any]] = []

    for item in selected:
        if item.get("source") == "notion":
            page_id = item.get("page_id")
            text = fetch_page_text(page_id, token, env.get("NOTION_VERSION", "2025-09-03"))
            if not text:
                processed_notion[page_id] = item.get("updated", "")
                continue
            extracted, raw_summary = summarize_tasks(
                text, env, item.get("project") or "TOKIWAGI", known_projects, today_str
            )
            fallback_used = False
            if not extracted:
                extracted = heuristic_tasks_from_text(
                    text,
                    item.get("project") or "TOKIWAGI",
                    known_projects,
                    max_projects=int(env.get("MINUTES_HEURISTIC_MAX_PROJECTS", "6")),
                    max_items_per_project=int(env.get("MINUTES_HEURISTIC_MAX_ITEMS_PER_PROJECT", "6")),
                )
                fallback_used = bool(extracted)
            if fallback_used:
                heuristic_used_docs += 1
            sanitized = sanitize_extracted_tasks(
                extracted,
                item.get("project") or "TOKIWAGI",
                known_projects,
                item.get("title", ""),
                max_tasks_per_doc=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
                max_subtasks_per_parent=int(env.get("MINUTES_MAX_SUBTASKS_PER_PARENT", "8")),
            )
            if args.debug:
                debug_records.append({
                    "source": "notion",
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "text_len": len(text),
                    "summary_len": len(raw_summary or ""),
                    "summary_snippet": (raw_summary or "")[:800],
                    "tasks": len(extracted),
                    "tasks_sanitized": len(sanitized),
                    "fallback": "heuristic" if fallback_used else "",
                })
            tasks = build_neuronic_tasks(
                sanitized,
                "notion",
                item.get("title", ""),
                item.get("url", ""),
                item.get("project") or "TOKIWAGI",
                page_id,
            )
            all_tasks.extend(tasks)
            processed_notion[page_id] = item.get("updated", "")
            summary["notion_pages"] += 1
        elif item.get("source") == "gdocs":
            doc_id = item.get("doc_id")
            text = export_doc_text(doc_id, env, account)
            if not text:
                processed_gdocs[doc_id] = item.get("updated", "")
                continue
            extracted, raw_summary = summarize_tasks(text, env, "GDocs", known_projects, today_str)
            fallback_used = False
            if not extracted:
                extracted = heuristic_tasks_from_text(
                    text,
                    "GDocs",
                    known_projects,
                    max_projects=int(env.get("MINUTES_HEURISTIC_MAX_PROJECTS", "6")),
                    max_items_per_project=int(env.get("MINUTES_HEURISTIC_MAX_ITEMS_PER_PROJECT", "6")),
                )
                fallback_used = bool(extracted)
            if fallback_used:
                heuristic_used_docs += 1
            sanitized = sanitize_extracted_tasks(
                extracted,
                "GDocs",
                known_projects,
                item.get("title", ""),
                max_tasks_per_doc=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
                max_subtasks_per_parent=int(env.get("MINUTES_MAX_SUBTASKS_PER_PARENT", "8")),
            )
            if args.debug:
                debug_records.append({
                    "source": "gdocs",
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "text_len": len(text),
                    "summary_len": len(raw_summary or ""),
                    "summary_snippet": (raw_summary or "")[:800],
                    "tasks": len(extracted),
                    "tasks_sanitized": len(sanitized),
                    "fallback": "heuristic" if fallback_used else "",
                })
            tasks = build_neuronic_tasks(
                sanitized,
                "gdocs",
                item.get("title", ""),
                item.get("url", ""),
                "GDocs",
                doc_id,
            )
            all_tasks.extend(tasks)
            processed_gdocs[doc_id] = item.get("updated", "")
            summary["gdocs"] += 1

    summary["tasks"] = len(all_tasks)
    summary["heuristic_used_docs"] = heuristic_used_docs

    if not args.dry_run:
        if all_tasks:
            resp = send_neuronic(all_tasks, env)
            if isinstance(resp, dict):
                summary["neuronic_created"] = int(resp.get("created", 0) or 0)
                summary["neuronic_updated"] = int(resp.get("updated", 0) or 0)
                summary["neuronic_skipped"] = int(resp.get("skipped", 0) or 0)
                summary["neuronic_error_count"] = int(resp.get("error_count", 0) or 0)
                if "hierarchy_applied" in resp:
                    summary["hierarchy_applied"] = resp.get("hierarchy_applied")
                if "order_applied" in resp:
                    summary["order_applied"] = resp.get("order_applied")
                if resp.get("endpoint_used") is not None:
                    summary["neuronic_endpoint"] = resp.get("endpoint_used")
                summary["neuronic_fallback"] = bool(resp.get("fallback_used", False))
                if resp.get("warning_messages"):
                    summary["neuronic_warnings"] = resp.get("warning_messages")
                if resp.get("error") or int(resp.get("error_count", 0) or 0) > 0:
                    summary["neuronic_errors"] += 1
                    summary["last_neuronic_error"] = resp.get("detail") or resp.get("error")
    else:
        summary["dry_run"] = True

    state["notion"] = processed_notion
    state["gdocs"] = processed_gdocs
    save_state(state)
    log_run({"ts": int(time.time()), "summary": summary})
    if args.debug and debug_records:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            for rec in debug_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    slack_url = env.get("SLACK_WEBHOOK_URL", "").strip()
    if slack_url:
        try:
            send_slack(slack_url, f"Roby Minutes Sync\n{json.dumps(summary, ensure_ascii=False)}")
        except Exception:
            pass

    if args.policy:
        summary["policy"] = args.policy

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

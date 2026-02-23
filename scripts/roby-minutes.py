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


def summarize_tasks(text: str, env: Dict[str, str], default_project: str, known_projects: List[str], today: str) -> List[Dict[str, Any]]:
    known = ", ".join(sorted(set([p for p in known_projects if p]))[:25])
    prompt = (
        "Extract actionable tasks from the meeting minutes. "
        "Ignore pure status notes or commentary. "
        "If tasks are related, group them under a parent task with a `subtasks` array. "
        "Return ONLY a JSON array. Each item has keys: title, due_date, project, assignee, note, subtasks (optional). "
        "Each subtask uses the same schema (title, due_date, project, assignee, note). "
        "due_date must be YYYY-MM-DD or empty string. "
        f"Today is {today} (JST). "
        f"Default project: {default_project}. "
        f"Known projects: {known}. "
        "Use the most appropriate project name if indicated. If not sure, use the default project."
    )
    cmd = [
        "summarize",
        "-",
        "--json",
        "--plain",
        "--metrics",
        "off",
        "--prompt",
        prompt,
        "--max-output-tokens",
        env.get("MINUTES_SUMMARIZE_MAX_TOKENS", "1600"),
    ]
    out = subprocess.check_output(cmd, input=text.encode("utf-8"), env=env, timeout=120)
    data = json.loads(out)
    summary = data.get("summary", "")
    if not summary:
        return []
    try:
        return json.loads(summary)
    except Exception:
        m = re.search(r"\[.*\]", summary, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return []
        return []


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
                }
                child_task["origin_id"] = _stable_origin_id(
                    child_task, f"{source_id}|child|{group_index}|{sub_idx}"
                )
                tasks.append(child_task)
        else:
            tasks.append(parent_task)
        group_index += 1
    return tasks


def send_neuronic(tasks: List[Dict[str, Any]], env: Dict[str, str]) -> Dict[str, Any]:
    if not tasks:
        return {"created": 0, "updated": 0, "skipped": 0}
    url = env.get("NEURONIC_URL", "http://127.0.0.1:5174/api/v1/tasks/import")
    fallback_url = env.get("NEURONIC_FALLBACK_URL", "http://127.0.0.1:5174/api/v1/tasks/bulk")
    token = env.get("NEURONIC_TOKEN") or env.get("TASKD_AUTH_TOKEN")
    payload = {"items": tasks}
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        header_name = env.get("NEURONIC_AUTH_HEADER", "Authorization")
        headers[header_name] = f"Bearer {token}"

    def _post(target_url: str) -> Dict[str, Any]:
        req = urllib.request.Request(target_url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", "ignore")
        try:
            return json.loads(body)
        except Exception:
            return {"response": body}

    try:
        return _post(url)
    except urllib.error.HTTPError as e:
        if e.code == 404 and url.endswith("/tasks/import"):
            try:
                return _post(fallback_url)
            except urllib.error.HTTPError as e2:
                return {"error": f"HTTP {e2.code}", "detail": e2.read().decode("utf-8", "ignore")}
        return {"error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "ignore")}
    except Exception as e:
        return {"error": str(e)}


def send_slack(webhook_url: str, text: str) -> None:
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


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
    args = parser.parse_args()

    env = load_env()
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

    for item in selected:
        if item.get("source") == "notion":
            page_id = item.get("page_id")
            text = fetch_page_text(page_id, token, env.get("NOTION_VERSION", "2025-09-03"))
            if not text:
                processed_notion[page_id] = item.get("updated", "")
                continue
            extracted = summarize_tasks(text, env, item.get("project") or "TOKIWAGI", known_projects, today_str)
            tasks = build_neuronic_tasks(
                extracted,
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
            extracted = summarize_tasks(text, env, "GDocs", known_projects, today_str)
            tasks = build_neuronic_tasks(
                extracted,
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

    if not args.dry_run:
        if all_tasks:
            resp = send_neuronic(all_tasks, env)
            if isinstance(resp, dict) and resp.get("error"):
                summary["neuronic_errors"] += 1
                summary["last_neuronic_error"] = resp.get("detail") or resp.get("error")
    else:
        summary["dry_run"] = True

    state["notion"] = processed_notion
    state["gdocs"] = processed_gdocs
    save_state(state)
    log_run({"ts": int(time.time()), "summary": summary})

    slack_url = env.get("SLACK_WEBHOOK_URL", "").strip()
    if slack_url:
        try:
            send_slack(slack_url, f"Roby Minutes Sync\n{json.dumps(summary, ensure_ascii=False)}")
        except Exception:
            pass

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

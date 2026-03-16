#!/usr/bin/env python3
import argparse
import json
import os
import re
import signal
import subprocess
import time
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import urllib.request
import urllib.error
from roby_audit import append_audit_event
from roby_context_seed import load_context_seed
from roby_local_first import env_flag, int_from_env, run_ollama_json

STATE_PATH = Path.home() / ".openclaw" / "roby" / "minutes_state.json"
RUN_LOG_PATH = Path.home() / ".openclaw" / "roby" / "minutes_runs.jsonl"
DEBUG_LOG_PATH = Path.home() / ".openclaw" / "roby" / "minutes_debug.jsonl"
NEURONIC_LOG_PATH = Path.home() / ".openclaw" / "roby" / "neuronic_import_runs.jsonl"
FEEDBACK_MANIFEST_PATH = Path.home() / ".openclaw" / "roby" / "feedback_candidates.jsonl"
HIERARCHY_STATE_PATH = Path.home() / ".openclaw" / "roby" / "neuronic_hierarchy_state.json"
TOKIWAGI_MASTER_REGISTRY_PATH = Path.home() / ".openclaw" / "roby" / "tokiwagi_master_registry_latest.json"
ENV_PATH = Path.home() / ".openclaw" / ".env"
NOTION_KEY_PATH = Path.home() / ".config" / "notion" / "api_key"

DEFAULT_DAYS = 14
DEFAULT_MAX = 200

JST = timezone(timedelta(hours=9))
PROJECT_ALIAS_REGISTRY: Dict[str, str] = {}
PROJECT_EXTRA_ALIASES: Dict[str, List[str]] = {}
PROJECT_OWNER_HINTS_REGISTRY: Dict[str, List[str]] = {}
PROJECT_ACTION_HINTS_REGISTRY: Dict[str, List[str]] = {}
PROJECT_TASK_POSITIVE_HINTS_REGISTRY: Dict[str, List[str]] = {}
PROJECT_TASK_NEGATIVE_HINTS_REGISTRY: Dict[str, List[str]] = {}
PROJECT_LOW_SELF_INVOLVEMENT: Dict[str, bool] = {}
CONTEXT_PROJECTS: List[str] = []
CONTEXT_SELF_OWNER_ALIASES: List[str] = []


class MinutesDocTimeout(RuntimeError):
    pass
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


def build_run_id(prefix: str = "minutes") -> str:
    seed = f"{time.time_ns()}|{os.getpid()}|{prefix}"
    return f"roby:{prefix}:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"


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


def _read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _normalize_owner_hint_candidate(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text).strip()
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"(さん|氏|様|店長|本部長)$", "", text)
    text = re.sub(r"(の件は|から|より).*$", "", text)
    if "@" in text:
        text = text.split("@", 1)[0]
    text = text.strip(" -_/：:")
    lowered = text.lower()
    if not text or lowered in OWNER_NOISE_HINTS:
        return ""
    if len(text) <= 1 and not text.isascii():
        return ""
    if len(text) > 14:
        return ""
    if re.search(r"(議事録|タスク|資料|対応|会議|確認|共有|連携|レポート|社|会社)", text):
        return ""
    return text


def load_tokiwagi_master_registry(path: Optional[Path] = None) -> Dict[str, Any]:
    payload = _read_json_file(path or TOKIWAGI_MASTER_REGISTRY_PATH)
    if payload:
        apply_tokiwagi_master_registry(payload)
    apply_context_seed_data(load_context_seed())
    return payload


def apply_tokiwagi_master_registry(registry: Dict[str, Any]) -> None:
    PROJECT_ALIAS_REGISTRY.clear()
    PROJECT_EXTRA_ALIASES.clear()
    PROJECT_OWNER_HINTS_REGISTRY.clear()
    PROJECT_ACTION_HINTS_REGISTRY.clear()
    PROJECT_TASK_POSITIVE_HINTS_REGISTRY.clear()
    PROJECT_TASK_NEGATIVE_HINTS_REGISTRY.clear()
    PROJECT_LOW_SELF_INVOLVEMENT.clear()
    CONTEXT_PROJECTS.clear()
    for project_entry in registry.get("project_registry", []) or []:
        if not isinstance(project_entry, dict):
            continue
        canonical = _canonical_project_display_name(str(project_entry.get("project") or ""))
        if not canonical:
            continue
        aliases = []
        for raw in (project_entry.get("aliases") or []):
            alias = str(raw or "").strip()
            if alias:
                aliases.append(alias)
        local_llm = project_entry.get("local_llm") or {}
        for raw in (local_llm.get("aliases") or []):
            alias = str(raw or "").strip()
            if alias:
                aliases.append(alias)
        for alias in aliases:
            PROJECT_EXTRA_ALIASES.setdefault(canonical, [])
            if alias and alias not in PROJECT_EXTRA_ALIASES[canonical]:
                PROJECT_EXTRA_ALIASES[canonical].append(alias)
            alias_norm = _normalize_project_token(alias)
            if alias_norm:
                PROJECT_ALIAS_REGISTRY[alias_norm] = canonical
        owners: List[str] = []
        for row in (project_entry.get("top_owners") or []):
            if isinstance(row, dict):
                owner = _normalize_owner_hint_candidate(str(row.get("value") or ""))
                if owner:
                    owners.append(owner)
        for raw in (local_llm.get("owner_hints") or []):
            owner = _normalize_owner_hint_candidate(str(raw or ""))
            if owner:
                owners.append(owner)
        PROJECT_OWNER_HINTS_REGISTRY[canonical] = list(dict.fromkeys(owners))[:8]
        actions: List[str] = []
        for row in (project_entry.get("top_action_patterns") or []):
            if isinstance(row, dict):
                label = str(row.get("value") or "").strip()
                if label:
                    actions.append(label)
        for raw in (local_llm.get("action_patterns") or []):
            label = str(raw or "").strip()
            if label:
                actions.append(label)
        PROJECT_ACTION_HINTS_REGISTRY[canonical] = list(dict.fromkeys(actions))[:8]


def apply_context_seed_data(seed: Dict[str, Any]) -> None:
    global CONTEXT_SELF_OWNER_ALIASES
    if not isinstance(seed, dict):
        return

    self_aliases: List[str] = []
    role = seed.get("role") or {}
    owner_rules = seed.get("owner_rules") or {}
    for raw in (role.get("self_aliases") or []):
        alias = _normalize_owner_hint_candidate(str(raw or ""))
        if alias:
            self_aliases.append(alias)
    for raw in (owner_rules.get("self_aliases") or []):
        alias = _normalize_owner_hint_candidate(str(raw or ""))
        if alias:
            self_aliases.append(alias)
    CONTEXT_SELF_OWNER_ALIASES = list(dict.fromkeys(self_aliases))

    for project_entry in (seed.get("projects") or []):
        if not isinstance(project_entry, dict):
            continue
        canonical = _canonical_project_display_name(str(project_entry.get("project") or ""))
        if not canonical:
            continue
        if canonical not in CONTEXT_PROJECTS:
            CONTEXT_PROJECTS.append(canonical)

        for raw in (project_entry.get("aliases") or []):
            alias = str(raw or "").strip()
            if not alias:
                continue
            PROJECT_EXTRA_ALIASES.setdefault(canonical, [])
            if alias not in PROJECT_EXTRA_ALIASES[canonical]:
                PROJECT_EXTRA_ALIASES[canonical].append(alias)
            alias_norm = _normalize_project_token(alias)
            if alias_norm:
                PROJECT_ALIAS_REGISTRY[alias_norm] = canonical

        owners = PROJECT_OWNER_HINTS_REGISTRY.get(canonical, [])
        for raw in (project_entry.get("owner_hints") or []):
            owner = _normalize_owner_hint_candidate(str(raw or ""))
            if owner:
                owners.append(owner)
        if owners:
            PROJECT_OWNER_HINTS_REGISTRY[canonical] = list(dict.fromkeys(owners))[:8]

        actions = PROJECT_ACTION_HINTS_REGISTRY.get(canonical, [])
        for raw in (project_entry.get("action_hints") or []):
            label = str(raw or "").strip()
            if label:
                actions.append(label)
        if actions:
            PROJECT_ACTION_HINTS_REGISTRY[canonical] = list(dict.fromkeys(actions))[:8]

        positive_hints = PROJECT_TASK_POSITIVE_HINTS_REGISTRY.get(canonical, [])
        for raw in (project_entry.get("positive_task_hints") or []):
            label = str(raw or "").strip()
            if label:
                positive_hints.append(label)
        if positive_hints:
            PROJECT_TASK_POSITIVE_HINTS_REGISTRY[canonical] = list(dict.fromkeys(positive_hints))[:12]

        negative_hints = PROJECT_TASK_NEGATIVE_HINTS_REGISTRY.get(canonical, [])
        for raw in (project_entry.get("negative_task_hints") or []):
            label = str(raw or "").strip()
            if label:
                negative_hints.append(label)
        if negative_hints:
            PROJECT_TASK_NEGATIVE_HINTS_REGISTRY[canonical] = list(dict.fromkeys(negative_hints))[:12]

        scope_blob = "\n".join(
            [
                str(project_entry.get("self_scope") or ""),
                str(project_entry.get("non_self_scope") or ""),
            ]
        )
        if re.search(r"(ほとんど携わっておらず|ほとんど関わっておらず|主担当ではない|担当しない範囲|.+が主導)", scope_blob):
            PROJECT_LOW_SELF_INVOLVEMENT[canonical] = True


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


def normalize_google_doc_id(raw: str) -> str:
    if not raw:
        return ""
    text = str(raw).strip()
    patterns = [
        r"docs\.google\.com/document/d/([A-Za-z0-9_-]{20,})",
        r"drive\.google\.com/file/d/([A-Za-z0-9_-]{20,})",
        r"/d/([A-Za-z0-9_-]{20,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", text):
        return text
    return ""


def detect_minutes_target_source(target: str, target_source: str = "auto") -> str:
    source = (target_source or "auto").strip().lower()
    if source in {"notion", "gdocs"}:
        return source
    if not target:
        return ""
    if normalize_notion_id(target) and re.fullmatch(r"[0-9a-fA-F]{32}", normalize_notion_id(target)):
        return "notion"
    if normalize_google_doc_id(target):
        return "gdocs"
    return ""


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


def fetch_notion_page_metadata(page_id: str, token: str, version: str) -> Dict[str, Any]:
    page_id = normalize_notion_id(page_id)
    if not page_id:
        return {}
    resp = notion_request("GET", f"https://api.notion.com/v1/pages/{page_id}", token, version)
    if not resp.get("ok"):
        return {}
    return resp.get("data", {})


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


def lookup_notion_database_context(structure: Dict[str, Any], db_id: str) -> Dict[str, Any]:
    db_norm = normalize_notion_id(db_id)
    if not db_norm:
        return {}
    for entry in structure.get("databases", []):
        if normalize_notion_id(str(entry.get("id") or "")) == db_norm:
            return entry
    return {}


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
    "推奨",
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

MEMO_NOISE_HINTS = [
    "参考",
    "背景",
    "所感",
    "振り返り",
    "報告のみ",
    "メモ",
    "議論",
]

OWNER_NOISE_HINTS = {
    "",
    "ai",
    "midj",
    "担当者",
    "システム開発部",
    "プロジェクト管理室",
    "広告部",
    "マーケティング部",
    "デジタルマーケティングチーム",
    "一広",
}

DEFAULT_SELF_OWNER_ALIASES = (
    "私",
    "自分",
    "新後",
    "新後周平",
    "周平",
    "にーご",
    "shu",
    "nigo",
    "s.nigo",
    "snigo",
)

GENERIC_PROJECT_NAMES = {
    "",
    "TOKIWAGI",
    "TOKIWAGI_MASTER",
    "TOKIWAGIインナー議事録",
    "基礎情報",
    "GDocs",
}

PROJECT_DISPLAY_NORMALIZATION = {
    "MID": "ミッド・ガーデン・ジャパン",
    "MIDジャパン-パチンコレポート": "ミッド・ガーデン・ジャパン",
    "ミッドガーデンジャパン": "ミッド・ガーデン・ジャパン",
}

TASK_REWRITE_PREFIXES = [
    r"^(?:現状|進捗|報告|背景|備考|所感|課題|論点|検討事項|要確認|対応|予定|ネクストアクション|次(?:の)?アクション|本日の重点タスク|重点タスク|TODO|ToDo|タスク)[:：]\s*",
    r"^(?:-|\*|・|●|◯|□|■|•|→|⇒|↳)\s*",
    r"^\d+[.)]\s*",
]

MINUTES_POSITIVE_TITLE_PATTERNS = [
    r"議事録",
    r"社内定例",
    r"定例",
    r"\bMTG\b",
    r"ミーティング",
    r"会議",
    r"打ち合わせ",
    r"Gemini によるメモ",
]

MINUTES_NEGATIVE_TITLE_PATTERNS = [
    r"^(?:承諾|辞退|招待)(?:[:：]|$)",
    r"(?:^|[\s/])更新[:：]",
    r"事務所情報",
    r"ログイン情報",
    r"請求確認項目",
    r"アカウント発行のお知らせ",
]

MINUTES_POSITIVE_TEXT_PATTERNS = [
    r"ネクストアクション",
    r"次(?:の)?アクション",
    r"本日の重点タスク",
    r"決定事項",
    r"社内定例報告",
    r"議事録",
    r"TODO",
    r"タスク",
    r"打ち合わせ",
    r"会議",
    r"ミーティング",
]

MINUTES_NEGATIVE_TEXT_PATTERNS = [
    r"営業時間",
    r"住所",
    r"アクセス",
    r"ログイン",
    r"パスワード",
]


def _clean_line(line: str) -> str:
    s = re.sub(r"^\s*[-*・●◯□■]+\s*", "", line.strip())
    s = re.sub(r"^\s*\d+[.)]\s*", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def extract_owner_mentions(text: str) -> List[str]:
    owners: List[str] = []
    patterns = [
        r"([一-龥ぁ-んァ-ヶA-Za-z0-9]+(?:さん|氏|様))",
        r"([一-龥ぁ-んァ-ヶA-Za-z0-9]+(?:店長|本部長))",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text or ""):
            owner = _normalize_owner_hint_candidate(str(match or ""))
            if owner:
                owners.append(owner)
    return owners


def _get_self_owner_aliases(env: Optional[Dict[str, str]] = None) -> set[str]:
    aliases = list(DEFAULT_SELF_OWNER_ALIASES)
    aliases.extend(CONTEXT_SELF_OWNER_ALIASES)
    raw = str((env or os.environ).get("ROBY_MINUTES_SELF_ALIASES", "") or "").strip()
    if raw:
        aliases.extend(re.split(r"[\n,]+", raw))
    normalized: set[str] = set()
    for alias in aliases:
        compact = re.sub(r"[\s\u3000]+", "", str(alias or "").strip())
        if compact in {"私", "自分"}:
            normalized.add("私")
            continue
        owner = _normalize_owner_hint_candidate(str(alias or ""))
        if owner:
            normalized.add(owner)
    return normalized


def _canonicalize_assignee(raw: str, env: Optional[Dict[str, str]] = None) -> str:
    compact = re.sub(r"[\s\u3000]+", "", str(raw or "").strip())
    if compact in {"私", "自分"}:
        return "私"
    owner = _normalize_owner_hint_candidate(raw or "")
    if not owner:
        return ""
    if owner in _get_self_owner_aliases(env):
        return "私"
    return owner


def _resolve_assignee_hint(
    raw_assignee: str,
    title: str,
    note: str,
    parent_assignee: str = "",
    env: Optional[Dict[str, str]] = None,
) -> str:
    direct = _canonicalize_assignee(raw_assignee, env)
    mentioned_owners = [
        _canonicalize_assignee(owner, env)
        for owner in (extract_owner_mentions(title or "") + extract_owner_mentions((note or "")[:400]))
    ]
    mentioned_owners = [owner for owner in mentioned_owners if owner]
    if direct and direct != "私":
        return direct
    for normalized in mentioned_owners:
        if normalized and normalized != "私":
            return normalized
    if direct:
        return direct
    for normalized in mentioned_owners:
        if normalized:
            return normalized
    parent = _canonicalize_assignee(parent_assignee or "", env)
    if parent:
        return parent
    return ""


def _is_self_assignee(assignee: str, env: Optional[Dict[str, str]] = None) -> bool:
    return _canonicalize_assignee(assignee or "", env) == "私"


def _should_emit_minutes_task(assignee: str, env: Optional[Dict[str, str]] = None) -> bool:
    normalized = _canonicalize_assignee(assignee or "", env)
    if not normalized:
        return True
    return normalized == "私"


def classify_action_patterns(line: str) -> List[str]:
    text = line or ""
    mapping = [
        ("会議調整", [r"日程", r"打ち合わせ", r"会議", r"ミーティング", r"定例"]),
        ("資料作成", [r"資料", r"提案", r"見積", r"案内", r"レポート"]),
        ("確認・調査", [r"確認", r"調査", r"把握", r"見直し", r"チェック"]),
        ("実装・設定", [r"実装", r"設定", r"開発", r"修正", r"連携"]),
        ("連携・共有", [r"共有", r"連絡", r"依頼", r"送付", r"引き継ぎ"]),
        ("分析・評価", [r"分析", r"評価", r"検証", r"比較", r"集計"]),
    ]
    labels: List[str] = []
    for label, patterns in mapping:
        if any(re.search(pattern, text) for pattern in patterns):
            labels.append(label)
    return labels


def _increment_counter_map(target: Dict[str, int], values: Iterable[str]) -> None:
    for value in values:
        key = (value or "").strip()
        if not key:
            continue
        target[key] = int(target.get(key, 0)) + 1


def _canonical_project_display_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    return PROJECT_DISPLAY_NORMALIZATION.get(raw, raw)


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


def _matches_any_pattern(text: str, patterns: Iterable[str]) -> bool:
    blob = str(text or "")
    return any(re.search(pattern, blob, re.IGNORECASE) for pattern in patterns)


def assess_minutes_candidate_quality(
    title: str,
    text: str,
    source: str,
    project: str = "",
) -> Dict[str, Any]:
    title_blob = str(title or "").strip()
    text_blob = str(text or "")
    project_blob = str(project or "").strip()
    positive_title = _matches_any_pattern(title_blob, MINUTES_POSITIVE_TITLE_PATTERNS)
    negative_title = _matches_any_pattern(title_blob, MINUTES_NEGATIVE_TITLE_PATTERNS)
    positive_text = _matches_any_pattern(text_blob[:4000], MINUTES_POSITIVE_TEXT_PATTERNS)
    negative_text = _matches_any_pattern(text_blob[:2000], MINUTES_NEGATIVE_TEXT_PATTERNS)

    action_lines = 0
    for raw_line in text_blob.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        if _line_looks_actionable(line) or _has_action_signal(line):
            action_lines += 1
            if action_lines >= 6:
                break

    project_hint = bool(project_blob and project_blob not in {"", "TOKIWAGI", "TOKIWAGI_MASTER", "基礎情報"})

    signals = {
        "positive_title": positive_title,
        "negative_title": negative_title,
        "positive_text": positive_text,
        "negative_text": negative_text,
        "action_lines": action_lines,
        "project_hint": project_hint,
        "source": source,
    }

    reasons: List[str] = []
    if negative_title and not positive_title and action_lines <= 1 and not positive_text:
        reasons.append("title_negative_without_minutes_signals")
    if "事務所情報" in title_blob and action_lines <= 1:
        reasons.append("office_info_like_document")
    if negative_text and not positive_text and action_lines <= 1 and source == "notion":
        reasons.append("static_info_like_text")
    if not (positive_title or positive_text or project_hint) and action_lines == 0:
        reasons.append("no_meeting_or_action_signals")

    ok = not reasons
    return {"ok": ok, "reasons": reasons, "signals": signals}


def _has_action_signal(text: str) -> bool:
    s = _clean_line(text)
    if not s:
        return False
    if any(a in s for a in ACTION_HINTS):
        return True
    if re.search(r"(まで|期限|予定|必要|依頼|確認|対応|実施|作成|調整|共有|連携|設定|修正|実装|準備|追跡|ヒアリング|検討|推奨)", s):
        return True
    if re.search(r"(する|したい|進める|行う|送る|まとめる|整理する|確認する|共有する|提出する|依頼する|推奨する)$", s):
        return True
    if re.search(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", s):
        return True
    return False


def _looks_report_only_title(title: str) -> bool:
    s = _clean_line(title)
    if not s:
        return False
    if re.search(r"(完了|実装済|停止している|段階にある|済み)$", s):
        return True
    if re.search(r"(仮構築完了|実装済)", s):
        return True
    return False


def _looks_timing_only_clause(title: str) -> bool:
    s = _clean_line(title)
    if not s:
        return False
    timing_signal = bool(re.search(r"(後|次第|まで|受けて)", s) or re.search(r"\d{1,2}[/-]\d{1,2}", s))
    if not timing_signal:
        return False
    if any(k in s for k in ("申請", "確認", "調査", "整理", "送信", "共有", "連携", "作成", "判断", "提出", "認証")):
        return False
    return bool(re.search(r"(実施|対応|着手|開始)(する)?$", s))


def _looks_non_japanese_translation_artifact(text: str) -> bool:
    s = _clean_line(text)
    if not s:
        return False
    # Local models occasionally drift into simplified-Chinese phrasing; drop those titles before task化.
    simplified_phrases = [
        "确认",
        "实际问题",
        "解决方案",
        "进行账户",
        "账户认证",
        "构建",
    ]
    return any(phrase in s for phrase in simplified_phrases)


def _looks_noise_task_title(title: str) -> bool:
    s = _clean_line(title)
    if not s:
        return True
    if len(s) < 4:
        return True
    if _looks_non_japanese_translation_artifact(s):
        return True
    if _looks_report_only_title(s):
        return True
    if re.fullmatch(r"[0-9０-９.,:：\- ]+", s):
        return True
    if any(k in s for k in STATUS_ONLY_HINTS) and not _has_action_signal(s):
        return True
    if re.match(r"^(現状|進捗|報告|備考|要確認|背景|所感|振り返り)([:：].*)?$", s) and not _has_action_signal(s):
        return True
    if any(k in s for k in MEMO_NOISE_HINTS) and not _has_action_signal(s):
        return True
    if re.match(r"^(共有|確認|対応|調整|検討)(事項|内容)?$", s):
        return True
    if re.search(r"\bPROJECT\b", s, re.IGNORECASE):
        return True
    if re.match(r"^[^。]{0,20}ミーティングの実施$", s):
        return True
    if re.match(r"^[^。.!?]{1,12}(について|に関して)$", s):
        return True
    if s.endswith("について") and not _has_action_signal(s):
        return True
    return False


def _strip_project_heading_prefix(text: str, known_projects: List[str]) -> str:
    raw = (text or "").strip()
    if not raw or ("：" not in raw and ":" not in raw):
        return raw
    leading, rest = re.split(r"[：:]", raw, maxsplit=1)
    matched = _match_known_project_name(leading.strip(), known_projects)
    if matched:
        return rest.strip()
    return raw


def _strip_task_prefixes(text: str) -> str:
    current = (text or "").strip()
    while current:
        updated = current
        for pattern in TASK_REWRITE_PREFIXES:
            updated = re.sub(pattern, "", updated).strip()
        if updated == current:
            break
        current = updated
    return current.strip("。.!? \t")


def _normalize_action_clause(text: str, known_projects: List[str]) -> str:
    clause = _clean_line(text or "")
    if not clause:
        return ""
    clause = _strip_project_heading_prefix(clause, known_projects)
    clause = _strip_task_prefixes(clause)
    clause = re.sub(r"^\(([^)]*)\)\s*", "", clause).strip()
    clause = clause.strip("。.!? \t")
    if not clause:
        return ""
    if _looks_non_japanese_translation_artifact(clause):
        return ""
    if _looks_noise_task_title(clause) and not _has_action_signal(clause):
        return ""
    if not (_line_looks_actionable(clause) or _has_action_signal(clause)):
        return ""
    return clause[:120]


def _extract_action_clauses(text: str, known_projects: List[str], max_items: int = 6) -> List[str]:
    if not text:
        return []
    candidates: List[str] = []
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"[。!?！？]", "\n", line)
        line = line.replace("↳", "\n").replace("→", "\n")
        for segment in line.splitlines():
            parts = [segment]
            if "・" in segment:
                split_parts = [p.strip() for p in segment.split("・") if p.strip()]
                actionable_split_parts = [
                    p for p in split_parts
                    if _has_action_signal(p) or _line_looks_actionable(p)
                ]
                if len(actionable_split_parts) >= 2:
                    parts = actionable_split_parts
            for part in parts:
                clause = _normalize_action_clause(part, known_projects)
                if clause:
                    candidates.append(clause)
    ordered: List[str] = []
    seen: set[str] = set()
    for clause in candidates:
        key = clause.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(clause)
        if max_items > 0 and len(ordered) >= max_items:
            break
    return ordered


def _compact_task_note(note: str, selected_titles: List[str], known_projects: List[str]) -> str:
    lines: List[str] = []
    seen: set[str] = set()
    selected_keys = {(x or "").strip().lower() for x in selected_titles if x}
    for raw_line in str(note or "").splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        normalized = _normalize_action_clause(line, known_projects)
        key = normalized.lower() if normalized else line.lower()
        if key in selected_keys or key in seen:
            continue
        seen.add(key)
        lines.append(line[:180])
        if len(lines) >= 3:
            break
    return "\n".join(lines).strip()


def _rewrite_leaf_task_candidates(
    title: str,
    note: str,
    known_projects: List[str],
    max_items: int = 4,
) -> List[str]:
    title_clause = _normalize_action_clause(title, known_projects)
    note_clauses = [
        clause
        for clause in _extract_action_clauses(note, known_projects, max_items=max_items)
        if not _looks_timing_only_clause(clause)
    ]

    if title_clause and not note_clauses:
        return [title_clause]
    if not title_clause:
        return note_clauses[:max_items]

    candidates: List[str] = [title_clause]
    title_key = title_clause.lower()
    for clause in note_clauses:
        if clause.lower() == title_key:
            continue
        candidates.append(clause)
        if max_items > 0 and len(candidates) >= max_items:
            break

    if _looks_noise_task_title(title) and note_clauses:
        return note_clauses[:max_items]
    return candidates[:max_items]


def _infer_project_from_text(text: str, known_projects: List[str]) -> Optional[str]:
    if not text:
        return None
    blob = text or ""
    best_project = None
    best_score = 0
    for project in sorted(set([x for x in known_projects if x and x not in GENERIC_PROJECT_NAMES]), key=len, reverse=True):
        score = 0
        for alias in _project_aliases(project):
            if not alias:
                continue
            count = blob.count(alias)
            if count <= 0:
                continue
            score += min(count, 8) * max(2, min(len(alias), 12))
        if score > best_score:
            best_score = score
            best_project = project
    return best_project


def _normalize_project_token(text: str) -> str:
    s = (text or "").strip().lower()
    if not s:
        return ""
    s = s.replace("　", " ")
    s = s.replace("様", "")
    s = re.sub(r"[\s・/:：()（）\-\u2010-\u2015]+", "", s)
    return s


def _match_known_project_name(label: str, known_projects: List[str]) -> Optional[str]:
    raw = (label or "").strip()
    if not raw:
        return None
    norm = _normalize_project_token(raw)
    if not norm:
        return None
    registry_match = PROJECT_ALIAS_REGISTRY.get(norm)
    if registry_match:
        return registry_match
    for project in sorted(set([x for x in known_projects if x and x not in GENERIC_PROJECT_NAMES]), key=len, reverse=True):
        project_norm = _normalize_project_token(project)
        if not project_norm:
            continue
        if norm == project_norm or norm in project_norm or project_norm in norm:
            return project
        for alias in _project_aliases(project):
            alias_norm = _normalize_project_token(alias)
            if not alias_norm:
                continue
            if norm == alias_norm or norm in alias_norm or alias_norm in norm:
                return project
    return None


def _looks_plausible_project_label(label: str) -> bool:
    s = _clean_line(label)
    if not s or s in GENERIC_PROJECT_NAMES:
        return False
    if len(s) > 60 and _has_action_signal(s):
        return False
    if re.search(r"(対応タスク|タスク一覧|アクション候補|アクション)", s):
        return False
    if re.search(r"(する|した|して|すべき|必要|予定|依頼|共有|確認|対応|実施|作成|調整|検討|準備|修正|実装)$", s):
        return False
    return True


def _resolve_project_name(
    project: str,
    title: str,
    note: str,
    source_title: str,
    default_project: str,
    known_projects: List[str],
) -> str:
    section_project = ""
    note_text = str(note or "")
    section_match = re.search(r"section_project:([^\n]+)", note_text)
    if section_match:
        section_project = _clean_line(section_match.group(1))
    if section_project:
        matched_section = _match_known_project_name(section_project, known_projects)
        if matched_section:
            return _canonical_project_display_name(matched_section)

    p = (project or "").strip()
    matched = _match_known_project_name(p, known_projects)
    content_blob = " ".join([title or "", note_text]).strip()
    inferred_from_content = _infer_project_from_text(content_blob, known_projects) if content_blob else None
    if matched:
        matched_canonical = _canonical_project_display_name(matched)
        inferred_canonical = _canonical_project_display_name(inferred_from_content or "")
        if (
            inferred_canonical
            and inferred_canonical != matched_canonical
            and _project_alias_hit_count(inferred_canonical, content_blob) > 0
            and _project_alias_hit_count(matched_canonical, content_blob) == 0
        ):
            return inferred_canonical
        return matched_canonical
    inferred = _infer_project_from_text(" ".join([p or "", title or "", note or "", source_title or ""]), known_projects)
    if inferred:
        return _canonical_project_display_name(inferred)
    if p and _looks_plausible_project_label(p):
        return _canonical_project_display_name(p)
    if default_project and default_project not in GENERIC_PROJECT_NAMES:
        return _canonical_project_display_name(_match_known_project_name(default_project, known_projects) or default_project)
    return _canonical_project_display_name(p or default_project or "TOKIWAGI")


def _project_aliases(name: str) -> List[str]:
    base = (name or "").strip()
    if not base:
        return []
    head_hyphen = re.split(r"[-ー／/]", base, maxsplit=1)[0].strip()
    head_space = base.split(" ", 1)[0].strip()
    latin_head_match = re.match(r"^([A-Za-z0-9]{2,})", base)
    latin_head = latin_head_match.group(1).strip() if latin_head_match else ""
    aliases = {
        base,
        base.replace("　", " "),
        base.replace(" ", ""),
        base.replace("　", ""),
        base.replace("・", ""),
        base.replace("様", ""),
        base.split(":", 1)[0].strip(),
        head_hyphen,
        head_space,
        latin_head,
    }
    for alias in PROJECT_EXTRA_ALIASES.get(_canonical_project_display_name(base), []):
        aliases.add(alias)
    return [a for a in aliases if a and len(a) >= 2]


def infer_primary_project(
    text: str,
    known_projects: List[str],
    source_title: str,
    fallback_project: str,
) -> str:
    candidates = [p for p in known_projects if p and p not in GENERIC_PROJECT_NAMES]
    if not candidates:
        return fallback_project or "TOKIWAGI"

    blob = f"{source_title}\n{text}"[:60000]
    best_project = ""
    best_score = 0
    for project in candidates:
        score = 0
        for alias in _project_aliases(project):
            count = blob.count(alias)
            if count <= 0:
                continue
            score += min(count, 8) * 10
            if alias in source_title:
                score += 15
        if score > best_score:
            best_score = score
            best_project = project

    if best_project and best_score >= 10:
        return best_project
    return fallback_project or "TOKIWAGI"


def extract_project_sections(
    text: str,
    *,
    default_project: str,
    known_projects: List[str],
    source_title: str,
    mod: Any,
) -> Dict[str, Dict[str, Any]]:
    inferred_primary = mod.infer_primary_project(
        text=text,
        known_projects=known_projects,
        source_title=source_title,
        fallback_project=default_project,
    )
    current_project = mod._canonical_project_display_name(inferred_primary or default_project)
    sections: Dict[str, Dict[str, Any]] = {}

    def ensure_section(project_name: str) -> Dict[str, Any]:
        canonical = mod._canonical_project_display_name(project_name or default_project or "TOKIWAGI_MASTER")
        if canonical not in sections:
            sections[canonical] = {
                "project": canonical,
                "line_count": 0,
                "action_count": 0,
                "sample_lines": [],
                "owners": {},
                "action_patterns": {},
            }
        return sections[canonical]

    ensure_section(current_project)

    for raw in text.splitlines():
        line = mod._clean_line(raw)
        if not line:
            continue
        heading = mod._line_looks_like_project_heading(raw, known_projects)
        if heading:
            matched = mod._match_known_project_name(heading, known_projects)
            if matched:
                current_project = mod._canonical_project_display_name(matched)
                ensure_section(current_project)
                continue
        elif "：" in raw or ":" in raw:
            leading = raw.split("：", 1)[0].split(":", 1)[0]
            matched = mod._match_known_project_name(leading, known_projects)
            if matched:
                current_project = mod._canonical_project_display_name(matched)
                ensure_section(current_project)

        matched_inline = mod._infer_project_from_text(line, known_projects)
        project_name = mod._canonical_project_display_name(matched_inline or current_project or default_project)
        section = ensure_section(project_name)
        section["line_count"] += 1
        action_patterns = classify_action_patterns(line)
        if mod._line_looks_actionable(line) or action_patterns or re.search(r"(する|した|して|ます|予定|必要|依頼|確認)$", line):
            section["action_count"] += 1
        if len(section["sample_lines"]) < 8 and line not in section["sample_lines"]:
            section["sample_lines"].append(line[:180])
        _increment_counter_map(section["owners"], extract_owner_mentions(line))
        _increment_counter_map(section["action_patterns"], action_patterns)

    return sections


def extend_known_projects_with_registry(known_projects: List[str], registry: Dict[str, Any]) -> List[str]:
    merged: List[str] = []
    for project in known_projects:
        canonical = _canonical_project_display_name(project)
        if canonical and canonical not in merged:
            merged.append(canonical)
    for project in CONTEXT_PROJECTS:
        canonical = _canonical_project_display_name(project)
        if canonical and canonical not in merged:
            merged.append(canonical)
    for entry in registry.get("project_registry", []) or []:
        if not isinstance(entry, dict):
            continue
        canonical = _canonical_project_display_name(str(entry.get("project") or ""))
        if canonical and canonical not in merged:
            merged.append(canonical)
    return merged


def infer_registry_project_hints(
    text: str,
    source_title: str,
    registry: Dict[str, Any],
    max_hints: int = 6,
) -> List[str]:
    if not registry:
        return []
    blob = f"{source_title}\n{text}"[:80000]
    scores: Dict[str, int] = {}
    for entry in registry.get("project_registry", []) or []:
        if not isinstance(entry, dict):
            continue
        canonical = _canonical_project_display_name(str(entry.get("project") or ""))
        if not canonical or canonical in GENERIC_PROJECT_NAMES:
            continue
        score = 0
        aliases = []
        aliases.extend(entry.get("aliases") or [])
        aliases.extend(PROJECT_EXTRA_ALIASES.get(canonical) or [])
        local_llm = entry.get("local_llm") or {}
        aliases.extend(local_llm.get("aliases") or [])
        aliases.extend(entry.get("sample_doc_titles") or [])
        for alias in aliases:
            alias_text = str(alias or "").strip()
            if not alias_text:
                continue
            count = blob.count(alias_text)
            if count > 0:
                score += min(count, 5) * max(4, min(len(alias_text), 18))
        for row in (entry.get("top_action_patterns") or []):
            if isinstance(row, dict):
                label = str(row.get("value") or "").strip()
                if label and label in blob:
                    score += 2
        if score > 0:
            scores[canonical] = score
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [name for name, _ in ordered[:max_hints]]


def build_registry_context(project_hints: List[str], registry: Dict[str, Any], limit: int = 4) -> str:
    if not registry or not project_hints:
        return ""
    registry_map = {
        _canonical_project_display_name(str(entry.get("project") or "")): entry
        for entry in (registry.get("project_registry") or [])
        if isinstance(entry, dict)
    }
    lines: List[str] = []
    for project in project_hints[:limit]:
        entry = registry_map.get(_canonical_project_display_name(project))
        if not entry:
            continue
        owner_hints = PROJECT_OWNER_HINTS_REGISTRY.get(project) or []
        action_hints = PROJECT_ACTION_HINTS_REGISTRY.get(project) or []
        local_llm = entry.get("local_llm") or {}
        summary = str(local_llm.get("summary") or "").strip()
        lines.append(f"- project: {project}")
        if owner_hints:
            lines.append(f"  owners: {', '.join(owner_hints[:4])}")
        if action_hints:
            lines.append(f"  patterns: {', '.join(action_hints[:4])}")
        if summary:
            lines.append(f"  context: {summary}")
    return "\n".join(lines)


def segment_minutes_text(
    text: str,
    default_project: str,
    known_projects: List[str],
    source_title: str,
) -> Tuple[str, Dict[str, Any]]:
    class _Adapter:
        infer_primary_project = staticmethod(infer_primary_project)
        _canonical_project_display_name = staticmethod(_canonical_project_display_name)
        _line_looks_like_project_heading = staticmethod(_line_looks_like_project_heading)
        _match_known_project_name = staticmethod(_match_known_project_name)
        _infer_project_from_text = staticmethod(_infer_project_from_text)
        _clean_line = staticmethod(_clean_line)
        _line_looks_actionable = staticmethod(_line_looks_actionable)

    sections = extract_project_sections(
        text,
        default_project=default_project,
        known_projects=known_projects,
        source_title=source_title,
        mod=_Adapter,
    )
    ordered_projects = [
        name
        for name, data in sorted(
            sections.items(),
            key=lambda item: (-int(item[1].get("action_count") or 0), -int(item[1].get("line_count") or 0), item[0]),
        )
        if name and name not in GENERIC_PROJECT_NAMES
    ]
    if len(ordered_projects) <= 1:
        return text, {
            "segmented": False,
            "project_hints": ordered_projects[:1],
            "section_count": len(ordered_projects),
        }
    body_lines: List[str] = []
    for project in ordered_projects:
        section = sections.get(project) or {}
        sample_lines = section.get("sample_lines") or []
        if not sample_lines:
            continue
        body_lines.append(f"[Project: {project}]")
        for line in sample_lines[:8]:
            body_lines.append(f"- {line}")
        owner_hints = PROJECT_OWNER_HINTS_REGISTRY.get(project) or []
        if owner_hints:
            body_lines.append(f"- owner_hints: {', '.join(owner_hints[:4])}")
        body_lines.append("")
    if not body_lines:
        return text, {
            "segmented": False,
            "project_hints": ordered_projects[:1],
            "section_count": len(ordered_projects),
        }
    segmented_text = "\n".join(body_lines).strip()
    return segmented_text, {
        "segmented": True,
        "project_hints": ordered_projects[:6],
        "section_count": len(ordered_projects),
    }


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

    def _append_leaf(title_value: str, project_value: str, due_value: str, assignee_value: str, note_value: str) -> None:
        title_clean = _clean_line(title_value)
        if not title_clean:
            return
        if _looks_noise_task_title(title_clean) and not _has_action_signal(title_clean) and not due_value:
            return
        fp = _fingerprint(title_clean, project_value, due_value, note_value)
        if fp in seen:
            return
        seen.add(fp)
        cleaned.append({
            "title": title_clean[:120],
            "project": project_value,
            "due_date": due_value,
            "assignee": assignee_value,
            "note": note_value,
        })

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
        assignee = _resolve_assignee_hint(
            str(item.get("assignee") or ""),
            title,
            note,
        )
        raw_item_project = str(item.get("project") or "")
        project = _resolve_project_name(
            raw_item_project,
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
                sa = _resolve_assignee_hint(
                    str(sub.get("assignee") or ""),
                    st,
                    sn,
                    parent_assignee=assignee,
                )
                sp = _resolve_project_name(
                    str(sub.get("project") or project),
                    st,
                    sn,
                    source_title,
                    project,
                    known_projects,
                )
                rewritten_titles = _rewrite_leaf_task_candidates(st, sn, known_projects)
                compact_note = _compact_task_note(sn, rewritten_titles, known_projects)
                for rewritten_title in rewritten_titles:
                    if max_subtasks_per_parent > 0 and len(subtasks) >= max_subtasks_per_parent:
                        break
                    if _looks_timing_only_clause(rewritten_title):
                        continue
                    sub_fp = _fingerprint(rewritten_title, sp, sd, compact_note)
                    if sub_fp in seen:
                        continue
                    seen.add(sub_fp)
                    subtasks.append({
                        "title": rewritten_title,
                        "project": sp,
                        "due_date": sd,
                        "assignee": sa,
                        "note": compact_note,
                    })

        if subtasks and len(subtasks) == 1 and ((not title) or _looks_noise_task_title(title)):
            single = subtasks[0]
            cleaned.append({
                "title": single.get("title", "")[:120],
                "project": single.get("project", project),
                "due_date": single.get("due_date", ""),
                "assignee": single.get("assignee", assignee),
                "note": single.get("note", note),
            })
            continue

        if subtasks:
            subtask_blob = "\n".join(
                [f"{sub.get('title', '')}\n{sub.get('note', '')}" for sub in subtasks[:8]]
            )
            contextual_project = _infer_project_from_text(
                "\n".join([note, subtask_blob, source_title]),
                known_projects,
            )
            project = (
                _canonical_project_display_name(contextual_project) if contextual_project else None
                or _resolve_project_name(
                    raw_item_project,
                    title,
                    "\n".join([note, subtask_blob]),
                    source_title,
                    project,
                    known_projects,
                )
            )
            normalized_subtasks: List[Dict[str, Any]] = []
            for sub in subtasks:
                sub_project = str(sub.get("project") or "")
                if raw_item_project.strip() and sub_project.strip() == raw_item_project.strip() and project != raw_item_project.strip():
                    sub_project = ""
                normalized_subtasks.append({
                    **sub,
                    "project": _resolve_project_name(
                        sub_project,
                        str(sub.get("title") or ""),
                        str(sub.get("note") or ""),
                        source_title,
                        project,
                        known_projects,
                    ),
                })
            parent_title = _normalize_minutes_parent_title(title, project, source_title)
            cleaned.append({
                "title": parent_title[:120],
                "project": project,
                "due_date": due_date,
                "assignee": assignee,
                "note": note,
                "subtasks": (
                    normalized_subtasks[:max_subtasks_per_parent]
                    if max_subtasks_per_parent > 0
                    else normalized_subtasks
                ),
            })
            continue

        # Leaf task
        rewritten_titles = _rewrite_leaf_task_candidates(title, note, known_projects)
        compact_note = _compact_task_note(note, rewritten_titles, known_projects)
        for rewritten_title in rewritten_titles:
            if _looks_timing_only_clause(rewritten_title):
                continue
            _append_leaf(rewritten_title, project, due_date, assignee, compact_note)
            if max_tasks_per_doc > 0 and len(cleaned) >= max_tasks_per_doc:
                return cleaned[:max_tasks_per_doc]

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


def fetch_drive_file_metadata(doc_id: str, env: Dict[str, str], account: str) -> Dict[str, Any]:
    doc_id = normalize_google_doc_id(doc_id)
    if not doc_id:
        return {}
    cmd = ["gog", "drive", "get", doc_id, "--json", "--results-only", "--no-input"]
    if account:
        cmd += ["--account", account]
    out = subprocess.check_output(cmd, env=env, timeout=60)
    data = json.loads(out)
    return data if isinstance(data, dict) else {}


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


def build_target_candidate(
    target: str,
    source: str,
    env: Dict[str, str],
    account: str,
    token: str,
    version: str,
    structure: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    kind = detect_minutes_target_source(target, source)
    if kind == "notion":
        page_id = normalize_notion_id(target)
        meta = fetch_notion_page_metadata(page_id, token, version)
        if not meta:
            raise ValueError(f"Notion page not found: {target}")
        parent = meta.get("parent") or {}
        db_id = parent.get("database_id") or parent.get("data_source_id") or ""
        db_context = lookup_notion_database_context(structure or {}, str(db_id))
        project = str(db_context.get("project") or "").strip()
        db_title = str(db_context.get("title") or "").strip()
        return {
            "source": "notion",
            "project": project,
            "db_title": db_title,
            "page_id": page_id,
            "title": extract_page_title(meta),
            "updated": meta.get("last_edited_time", ""),
            "url": meta.get("url", f"https://www.notion.so/{page_id}"),
        }
    if kind == "gdocs":
        doc_id = normalize_google_doc_id(target)
        meta = fetch_drive_file_metadata(doc_id, env, account)
        title = str(meta.get("name") or "").strip() or f"Google Doc {doc_id}"
        updated = str(meta.get("modifiedTime") or meta.get("modified_time") or "").strip()
        return {
            "source": "gdocs",
            "project": "",
            "doc_id": doc_id,
            "title": title,
            "updated": updated,
            "url": f"https://docs.google.com/document/d/{doc_id}",
        }
    raise ValueError(f"Unsupported target source: {target}")


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


def _strip_code_fence(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _is_explicit_empty_tasks(raw: str) -> bool:
    s = _strip_code_fence(raw)
    if not s:
        return False
    if s == "[]":
        return True
    parsed = _parse_jsonish_text(s)
    if isinstance(parsed, list) and len(parsed) == 0:
        return True
    if isinstance(parsed, dict):
        tasks = parsed.get("tasks")
        if isinstance(tasks, list) and len(tasks) == 0:
            return True
    return False


def _coerce_task_array(parsed: Any) -> List[Dict[str, Any]]:
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    if isinstance(parsed, dict):
        for key in ("tasks", "items", "result"):
            val = parsed.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _extract_tasks_from_partial_json_text(raw: str, default_project: str) -> List[Dict[str, Any]]:
    s = _strip_code_fence(raw)
    if not s:
        return []
    title_iter = list(re.finditer(r'"title"\s*:\s*"([^"]+)"', s))
    tasks: List[Dict[str, Any]] = []
    for i, m in enumerate(title_iter):
        title = (m.group(1) or "").strip()
        if not title:
            continue
        start = m.start()
        end = title_iter[i + 1].start() if i + 1 < len(title_iter) else min(len(s), start + 400)
        chunk = s[start:end]

        due = ""
        due_m = re.search(r'"due_date"\s*:\s*(?:"([^"]*)"|null)', chunk)
        if due_m:
            due = (due_m.group(1) or "").strip()
            if due and not re.match(r"^\d{4}-\d{2}-\d{2}$", due):
                due = ""

        project = default_project
        proj_m = re.search(r'"project"\s*:\s*(?:"([^"]*)"|null)', chunk)
        if proj_m and (proj_m.group(1) or "").strip():
            project = (proj_m.group(1) or "").strip()

        assignee = "私"
        assignee_m = re.search(r'"assignee"\s*:\s*(?:"([^"]*)"|null)', chunk)
        if assignee_m and (assignee_m.group(1) or "").strip():
            assignee = (assignee_m.group(1) or "").strip()

        note = ""
        note_m = re.search(r'"note"\s*:\s*(?:"([^"]*)"|null)', chunk)
        if note_m and (note_m.group(1) or "").strip():
            note = (note_m.group(1) or "").strip()

        tasks.append({
            "title": title,
            "due_date": due,
            "project": project,
            "assignee": assignee,
            "note": note,
        })
    return tasks


def _task_key_for_merge(item: Dict[str, Any]) -> str:
    return "|".join([
        _clean_line(str(item.get("title") or "")).lower(),
        _clean_line(str(item.get("project") or "")).lower(),
        _clean_line(str(item.get("due_date") or "")).lower(),
    ])


def _merge_tasks(*task_lists: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for src in task_lists:
        for item in src:
            if not isinstance(item, dict):
                continue
            key = _task_key_for_merge(item)
            if not key.strip("|") or key in seen:
                continue
            seen.add(key)
            out.append(item)
            if limit > 0 and len(out) >= limit:
                return out
    return out


def _run_gemini_json_prompt(
    text: str,
    prompt: str,
    env: Dict[str, str],
    *,
    model: str,
    max_output_tokens: str,
    length: str,
    timeout_sec: int,
) -> Tuple[Any, str]:
    if "/" in model and model.split("/", 1)[0].strip().lower() == "ollama":
        parsed, meta = run_ollama_json(
            prompt=prompt,
            source_text=text,
            env=env,
            model=model,
            timeout_sec=timeout_sec,
            num_predict=int(max_output_tokens or "1200"),
            temperature=float(env.get("MINUTES_OLLAMA_TEMPERATURE", "0.15") or "0.15"),
            top_p=float(env.get("MINUTES_OLLAMA_TOP_P", "0.9") or "0.9"),
            repeat_penalty=float(env.get("MINUTES_OLLAMA_REPEAT_PENALTY", "1.05") or "1.05"),
        )
        raw = json.dumps(parsed, ensure_ascii=False) if parsed is not None else json.dumps(meta, ensure_ascii=False)
        return parsed, raw

    cmd = [
        "summarize",
        "-",
        "--json",
        "--plain",
        "--metrics",
        "off",
        "--model",
        model,
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


def _candidate_models(env: Dict[str, str], list_key: str, fallback: List[str]) -> List[str]:
    supported_providers = {"xai", "openai", "google", "anthropic", "zai", "ollama"}

    def _is_supported(model: str) -> bool:
        m = (model or "").strip()
        if not m:
            return False
        if "/" not in m:
            # Backward compatibility: bare model names are passed through.
            return True
        provider = m.split("/", 1)[0].strip().lower()
        return provider in supported_providers

    raw = (env.get(list_key) or "").strip()
    models = [x.strip() for x in raw.split(",") if x.strip() and _is_supported(x.strip())]
    if not models:
        models = [x for x in fallback if x and _is_supported(x)]
    # dedupe while preserving order
    uniq: List[str] = []
    seen = set()
    for m in models:
        if m in seen:
            continue
        seen.add(m)
        uniq.append(m)
    return uniq


def minutes_local_preprocess_would_run(text: str, env: Dict[str, str]) -> bool:
    if not env_flag(env, "MINUTES_LOCAL_PREPROCESS_ENABLE", True):
        return False
    min_chars = int_from_env(env, "MINUTES_LOCAL_PREPROCESS_MIN_CHARS", 1200)
    return len(text) >= min_chars


def local_preprocess_minutes(
    text: str,
    env: Dict[str, str],
    default_project: str,
    known_projects: List[str],
    today: str,
    registry_context: str = "",
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if not env_flag(env, "MINUTES_LOCAL_PREPROCESS_ENABLE", True):
        return None, {"enabled": False, "reason": "disabled"}
    min_chars = int_from_env(env, "MINUTES_LOCAL_PREPROCESS_MIN_CHARS", 1200)
    if len(text) < min_chars:
        return None, {"enabled": False, "reason": "short_input", "min_chars": min_chars}

    known = ", ".join(sorted(set([p for p in known_projects if p]))[:25])
    prompt = (
        "You are doing a local-first preprocessing pass for Japanese meeting minutes. "
        "Return ONLY JSON object with keys: cleaned_text, primary_project, project_hints, action_candidates, noise_notes. "
        "cleaned_text should preserve concrete requests, deadlines, owners, and decisions, while removing filler, repeated context, and retrospective commentary. "
        "primary_project should be a specific project if strongly indicated, otherwise empty string. "
        "project_hints must be an array of likely project names. "
        "action_candidates must be an array of short concrete action lines only. "
        "noise_notes must be an array of memo/status lines that should not become tasks. "
        "Output cleaned_text, action_candidates, and noise_notes in Japanese only; never translate into Chinese or English. "
        f"Today(JST): {today}. Default project: {default_project}. Known projects: {known}. "
        + (f"Project registry hints:\n{registry_context}" if registry_context else "")
    )
    model = (env.get("MINUTES_LOCAL_PREPROCESS_MODEL") or env.get("ROBY_ORCH_MINUTES_LOCAL_QUALITY_MODEL") or "qwen2.5:7b").strip()
    parsed, meta = run_ollama_json(
        prompt=prompt,
        source_text=text[: int_from_env(env, "MINUTES_LOCAL_PREPROCESS_MAX_INPUT_CHARS", 18000)],
        env=env,
        model=model,
        timeout_sec=int_from_env(env, "MINUTES_LOCAL_PREPROCESS_TIMEOUT_SEC", 70),
        num_predict=int_from_env(env, "MINUTES_LOCAL_PREPROCESS_NUM_PREDICT", 2200),
        temperature=float(env.get("MINUTES_LOCAL_PREPROCESS_TEMPERATURE", "0.15") or "0.15"),
        top_p=float(env.get("MINUTES_LOCAL_PREPROCESS_TOP_P", "0.9") or "0.9"),
        repeat_penalty=float(env.get("MINUTES_LOCAL_PREPROCESS_REPEAT_PENALTY", "1.05") or "1.05"),
    )
    if not isinstance(parsed, dict):
        return None, meta

    cleaned_text = str(parsed.get("cleaned_text") or "").strip()
    if not cleaned_text:
        return None, {**meta, "error": "minutes_local_preprocess_empty"}

    project_hints = [str(x).strip() for x in (parsed.get("project_hints") or []) if str(x).strip()]
    action_candidates = [str(x).strip() for x in (parsed.get("action_candidates") or []) if str(x).strip()]
    noise_notes = [str(x).strip() for x in (parsed.get("noise_notes") or []) if str(x).strip()]
    return {
        "cleaned_text": cleaned_text,
        "primary_project": str(parsed.get("primary_project") or "").strip(),
        "project_hints": project_hints[:8],
        "action_candidates": action_candidates[:20],
        "noise_notes": noise_notes[:20],
    }, meta


def run_with_doc_timeout(timeout_sec: int, fn, *args, **kwargs):
    if timeout_sec <= 0 or os.name == "nt":
        return fn(*args, **kwargs)

    def _handle_timeout(signum, frame):
        raise MinutesDocTimeout(f"minutes_doc_timeout:{timeout_sec}")

    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_sec))
    try:
        return fn(*args, **kwargs)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def tasks_from_local_preprocess(
    local_preprocess: Dict[str, Any],
    default_project: str,
    known_projects: List[str],
    max_items: int = 8,
) -> List[Dict[str, Any]]:
    candidates = local_preprocess.get("action_candidates") or []
    if not isinstance(candidates, list):
        return []
    fallback_project = str(local_preprocess.get("primary_project") or "").strip() or default_project
    tasks: List[Dict[str, Any]] = []
    seen = set()
    for raw in candidates:
        title = _clean_line(str(raw or ""))
        if not title or _looks_noise_task_title(title):
            continue
        project = _resolve_project_name(
            "",
            title,
            "",
            "",
            fallback_project,
            known_projects,
        )
        key = f"{title.lower()}|{project.lower()}"
        if key in seen:
            continue
        seen.add(key)
        tasks.append(
            {
                "title": title[:120],
                "due_date": "",
                "project": project,
                "assignee": "私",
                "note": "local_preprocess.action_candidates",
            }
        )
        if max_items > 0 and len(tasks) >= max_items:
            break
    return tasks


def _run_gemini_json_prompt_with_retry(
    text: str,
    prompt: str,
    env: Dict[str, str],
    *,
    model_list_key: str,
    fallback_models: List[str],
    max_output_tokens: str,
    retry_max_output_tokens: str,
    length: str,
    timeout_sec: int,
    retry_timeout_sec: int,
) -> Tuple[Any, str]:
    models = _candidate_models(env, model_list_key, fallback_models)
    last_raw = ""
    for idx, model in enumerate(models):
        tokens = max_output_tokens if idx == 0 else retry_max_output_tokens
        timeout = timeout_sec if idx == 0 else retry_timeout_sec
        try:
            parsed, raw = _run_gemini_json_prompt(
                text,
                prompt,
                env,
                model=model,
                max_output_tokens=tokens,
                length=length,
                timeout_sec=timeout,
            )
            last_raw = raw or ""
            if parsed is not None:
                return parsed, raw
        except Exception:
            continue
    return None, last_raw


def review_minutes_with_gemini(
    text: str,
    env: Dict[str, str],
    default_project: str,
    known_projects: List[str],
    today: str,
    registry_context: str = "",
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
        "Output summary, key_points, action_candidates, and noise_notes in Japanese only. "
        f"Today(JST): {today}. Default project: {default_project}. Known projects: {known}. "
        + (f"Project registry hints:\n{registry_context}" if registry_context else "")
    )
    parsed, raw = _run_gemini_json_prompt_with_retry(
        text,
        prompt,
        env,
        model_list_key="MINUTES_REVIEW_MODELS",
        fallback_models=[
            env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview"),
            "google/gemini-2.5-pro",
        ],
        max_output_tokens=env.get("MINUTES_REVIEW_MAX_TOKENS", "2200"),
        retry_max_output_tokens=env.get("MINUTES_REVIEW_RETRY_MAX_TOKENS", "3200"),
        length=env.get("MINUTES_REVIEW_LENGTH", "xl"),
        timeout_sec=int(env.get("MINUTES_REVIEW_TIMEOUT_SEC", "150")),
        retry_timeout_sec=int(env.get("MINUTES_REVIEW_RETRY_TIMEOUT_SEC", "220")),
    )
    return (parsed if isinstance(parsed, dict) else None), raw


def extract_tasks_with_gemini_from_review(
    review: Dict[str, Any],
    env: Dict[str, str],
    default_project: str,
    known_projects: List[str],
    today: str,
    registry_context: str = "",
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
        "Output titles, notes, and subtasks in Japanese only. "
        f"Today(JST): {today}. Default project: {default_project}. Known projects: {known}. "
        + (f"Project registry hints:\n{registry_context}" if registry_context else "")
    )
    parsed, raw = _run_gemini_json_prompt_with_retry(
        review_text,
        prompt,
        env,
        model_list_key="MINUTES_TASKS_MODELS",
        fallback_models=[
            env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview"),
            "google/gemini-2.5-pro",
        ],
        max_output_tokens=env.get("MINUTES_TASKS_MAX_TOKENS", "2600"),
        retry_max_output_tokens=env.get("MINUTES_TASKS_RETRY_MAX_TOKENS", "3600"),
        length=env.get("MINUTES_TASKS_LENGTH", "xxl"),
        timeout_sec=int(env.get("MINUTES_TASKS_TIMEOUT_SEC", "180")),
        retry_timeout_sec=int(env.get("MINUTES_TASKS_RETRY_TIMEOUT_SEC", "240")),
    )
    coerced = _coerce_task_array(parsed)
    if coerced:
        return coerced, raw
    return [], raw


def extract_coverage_tasks_from_review(
    review: Dict[str, Any],
    current_tasks: List[Dict[str, Any]],
    env: Dict[str, str],
    default_project: str,
    known_projects: List[str],
    today: str,
    registry_context: str = "",
) -> Tuple[List[Dict[str, Any]], str]:
    known = ", ".join(sorted(set([p for p in known_projects if p]))[:30])
    review_text = json.dumps(review, ensure_ascii=False)
    existing_titles = ", ".join([str(t.get("title") or "") for t in current_tasks[:40]])
    prompt = (
        "You are doing a coverage boost pass for task extraction. "
        "Given reviewed meeting sections and already extracted tasks, add only missing high-confidence actionable tasks. "
        "Return ONLY a JSON array. Each item has keys: title, due_date, project, assignee, note, subtasks(optional). "
        "Do not duplicate existing titles. Do NOT emit status summaries or commentary. "
        "Prefer one concrete task per actionable section when missing. "
        "Project must be specific (avoid generic TOKIWAGI for internal MTG if section suggests project). "
        "due_date must be YYYY-MM-DD or empty string. "
        "Output titles and notes in Japanese only. "
        f"Today(JST): {today}. Default project: {default_project}. Known projects: {known}. "
        f"Existing titles: {existing_titles}. "
        + (f"Project registry hints:\n{registry_context}" if registry_context else "")
    )
    parsed, raw = _run_gemini_json_prompt_with_retry(
        review_text,
        prompt,
        env,
        model_list_key="MINUTES_COVERAGE_MODELS",
        fallback_models=[
            env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview"),
            "google/gemini-2.5-pro",
        ],
        max_output_tokens=env.get("MINUTES_COVERAGE_MAX_TOKENS", "2000"),
        retry_max_output_tokens=env.get("MINUTES_COVERAGE_RETRY_MAX_TOKENS", "3000"),
        length=env.get("MINUTES_COVERAGE_LENGTH", "m"),
        timeout_sec=int(env.get("MINUTES_COVERAGE_TIMEOUT_SEC", "120")),
        retry_timeout_sec=int(env.get("MINUTES_COVERAGE_RETRY_TIMEOUT_SEC", "180")),
    )
    coerced = _coerce_task_array(parsed)
    if coerced:
        return coerced, raw
    return [], raw


def tasks_from_review_object(
    review: Dict[str, Any],
    default_project: str,
    known_projects: List[str],
    max_per_project: int = 8,
    max_cross_project: int = 8,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    sections = review.get("project_sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            project = _resolve_project_name(
                str(sec.get("project") or ""),
                "",
                "",
                "",
                default_project,
                known_projects,
            )
            candidates = sec.get("action_candidates") or []
            if not isinstance(candidates, list):
                continue
            count = 0
            for cand in candidates:
                if count >= max_per_project:
                    break
                title = _normalize_action_clause(str(cand or ""), known_projects)
                if not title or _looks_noise_task_title(title):
                    continue
                out.append({
                    "title": title[:120],
                    "due_date": "",
                    "project": project,
                    "assignee": "私",
                    "note": f"review.project_sections.action_candidates\nsection_project:{project}",
                })
                count += 1

    cross = review.get("cross_project_actions") or []
    if isinstance(cross, list):
        count = 0
        for cand in cross:
            if count >= max_cross_project:
                break
            title = _clean_line(str(cand or ""))
            if not title or _looks_noise_task_title(title):
                continue
            out.append({
                "title": title[:120],
                "due_date": "",
                "project": default_project,
                "assignee": "私",
                "note": "review.cross_project_actions",
            })
            count += 1
    return out


def adjudicate_review_candidates_with_gemini(
    review: Dict[str, Any],
    current_candidates: List[Dict[str, Any]],
    env: Dict[str, str],
    default_project: str,
    known_projects: List[str],
    today: str,
    existing_tasks: Optional[List[Dict[str, Any]]] = None,
    registry_context: str = "",
) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    if not current_candidates:
        return [], ""
    known = ", ".join(sorted(set([p for p in known_projects if p]))[:30])
    existing_titles = ", ".join(
        [str(t.get("title") or "") for t in (existing_tasks or [])[:40] if str(t.get("title") or "").strip()]
    )
    payload = {
        "review": review,
        "candidates": current_candidates,
        "existing_titles": existing_titles,
    }
    prompt = (
        "You are adjudicating borderline task candidates from Japanese internal meeting minutes. "
        "Return ONLY a JSON array. Each item has keys: title, due_date, project, assignee, note. "
        "The user acts like a translator / coordinator / PM-style operator, so keep candidates when the user's next action is needed. "
        "User-owned next actions include confirming, coordinating, replying, requesting, organizing, investigating, deciding, and preparing. "
        "Even if another person or team is mentioned, keep the candidate when the user's next action is to coordinate, confirm, or communicate. "
        "Only include candidates that should become actionable tasks for the user. "
        "Drop only clearly report-only/status-only/progress-only/background-only/completed items with no next action for the user. "
        "Rewrite vague titles into concrete action titles when the surrounding review context makes them clear enough. "
        "Do NOT emit standalone timing-only fragments like '3月13日のHP公開後に実施'; fold timing into the actionable task title or note instead. "
        "Questions can remain tasks when they imply a needed investigation, decision, confirmation, or follow-up owned by the user; prefer rewrite over drop. "
        "When a candidate is ambiguous between drop and keep, prefer keeping it if the section context suggests a concrete next step. "
        "Generic schedule items can remain only if you can rewrite them into a specific schedule/coordination task using the section context. "
        "If your first instinct would drop every candidate, keep the top 1-2 high-confidence actionable candidates instead of returning an empty array. "
        "Prefer the section project context over generic keyword matches. "
        "Strong project hints include: ボーネルンド=スマレジ/OBIC/DIPRO/POS, ミッド・ガーデン・ジャパン=MID/堀之内店/Liny/Synergy/AI店長, 瑞鳳社ーデータ分析=Yellowfin/インサイト機能/Mapbox, SNW様-第三者広告配信=DSP/IDFA/くふうジオデータ/一広. "
        "BT振興会系は Mooovi と単発案件を分けて考え、チケットショップは Mooovi に自動で寄せないこと. "
        "Avoid duplicates against existing titles. "
        "Project must be one of Known projects when applicable. "
        "due_date must be YYYY-MM-DD or empty string. "
        f"Today(JST): {today}. Default project: {default_project}. Known projects: {known}. "
        f"Existing titles: {existing_titles}. "
        + (f"Project registry hints:\n{registry_context}" if registry_context else "")
    )
    parsed, raw = _run_gemini_json_prompt_with_retry(
        json.dumps(payload, ensure_ascii=False),
        prompt,
        env,
        model_list_key="MINUTES_REVIEW_ADJUDICATE_MODELS",
        fallback_models=[
            env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview"),
            "google/gemini-2.5-pro",
        ],
        max_output_tokens=env.get("MINUTES_REVIEW_ADJUDICATE_MAX_TOKENS", "1800"),
        retry_max_output_tokens=env.get("MINUTES_REVIEW_ADJUDICATE_RETRY_MAX_TOKENS", "2600"),
        length=env.get("MINUTES_REVIEW_ADJUDICATE_LENGTH", "l"),
        timeout_sec=int(env.get("MINUTES_REVIEW_ADJUDICATE_TIMEOUT_SEC", "120")),
        retry_timeout_sec=int(env.get("MINUTES_REVIEW_ADJUDICATE_RETRY_TIMEOUT_SEC", "180")),
    )
    tasks = _coerce_task_array(parsed)
    if tasks or isinstance(parsed, (list, dict)):
        cleaned: List[Dict[str, Any]] = []
        for item in tasks:
            title = _clean_line(str(item.get("title") or ""))
            if not title or _looks_report_only_title(title):
                continue
            project = _resolve_project_name(
                str(item.get("project") or ""),
                title,
                str(item.get("note") or ""),
                "",
                default_project,
                known_projects,
            )
            note = _clean_line(str(item.get("note") or ""))
            if "review.adjudicated_candidates" not in note:
                note = (note + "\n" if note else "") + "review.adjudicated_candidates"
            cleaned.append(
                {
                    "title": title[:120],
                    "due_date": str(item.get("due_date") or "").strip(),
                    "project": project,
                    "assignee": str(item.get("assignee") or "私").strip() or "私",
                    "note": note,
                }
            )
        return cleaned, raw
    return None, raw


def _review_candidate_fallback_priority(item: Dict[str, Any]) -> int:
    title = _clean_line(str(item.get("title") or ""))
    note = _clean_line(str(item.get("note") or ""))
    score = 0
    if re.search(r"\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2}", title):
        score += 3
    if _has_action_signal(title):
        score += 2
    if any(k in title for k in ("確認", "申請", "調査", "整理", "送信", "実施", "作成", "判断")):
        score += 2
    if "section_project:" in note:
        score += 1
    if "review.project_sections.action_candidates" in note:
        score += 1
    if _looks_report_only_title(title):
        score -= 5
    return score


def fallback_review_candidates_for_empty_adjudication(
    review_tasks: List[Dict[str, Any]],
    limit: int = 2,
) -> List[Dict[str, Any]]:
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for item in review_tasks:
        if not isinstance(item, dict):
            continue
        title = _clean_line(str(item.get("title") or ""))
        if not title or _looks_report_only_title(title):
            continue
        score = _review_candidate_fallback_priority(item)
        if score <= 0:
            continue
        scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("title") or "")))
    out: List[Dict[str, Any]] = []
    seen = set()
    for _, item in scored:
        key = _task_key_for_merge(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if limit > 0 and len(out) >= limit:
            break
    return out


def fallback_review_section_rescue_tasks(
    review: Dict[str, Any],
    default_project: str,
    known_projects: List[str],
    max_items: int = 3,
) -> List[Dict[str, Any]]:
    sections = review.get("project_sections") or []
    if not isinstance(sections, list):
        return []
    candidates: List[Dict[str, Any]] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        project = _resolve_project_name(
            str(sec.get("project") or ""),
            "",
            "",
            "",
            default_project,
            known_projects,
        )
        raw_lines: List[str] = []
        for key in ("action_candidates", "key_points"):
            values = sec.get(key) or []
            if isinstance(values, list):
                raw_lines.extend([str(v or "") for v in values if str(v or "").strip()])
        for clause in _extract_action_clauses("\n".join(raw_lines), known_projects, max_items=8):
            if _looks_report_only_title(clause):
                continue
            candidates.append(
                {
                    "title": clause[:120],
                    "due_date": "",
                    "project": project,
                    "assignee": "私",
                    "note": f"review.section_rescue\nsection_project:{project}",
                }
            )
    scored = sorted(
        candidates,
        key=lambda item: (-_review_candidate_fallback_priority(item), str(item.get("title") or "")),
    )
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in scored:
        key = _task_key_for_merge(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if max_items > 0 and len(out) >= max_items:
            break
    return out


def local_recall_boost_tasks(
    text: str,
    default_project: str,
    known_projects: List[str],
    max_projects: int = 4,
    max_items_per_project: int = 5,
) -> List[Dict[str, Any]]:
    boosted = heuristic_tasks_from_text(
        text,
        default_project,
        known_projects,
        max_projects=max_projects,
        max_items_per_project=max_items_per_project,
    )
    for item in boosted:
        if isinstance(item, dict):
            note = (item.get("note") or "").strip()
            item["note"] = (note + "\n" if note else "") + "local_recall_boost"
    return boosted


def summarize_tasks(
    text: str,
    env: Dict[str, str],
    default_project: str,
    known_projects: List[str],
    today: str,
    source_title: str = "",
) -> Tuple[List[Dict[str, Any]], str]:
    registry = load_tokiwagi_master_registry()
    working_known_projects = extend_known_projects_with_registry(list(known_projects), registry)
    segmented_text, segment_meta = segment_minutes_text(
        text,
        default_project=default_project,
        known_projects=working_known_projects,
        source_title=source_title or default_project,
    )
    registry_project_hints = infer_registry_project_hints(segmented_text, source_title or default_project, registry)
    if registry_project_hints:
        working_known_projects = list(dict.fromkeys(working_known_projects + registry_project_hints))
    working_default_project = infer_primary_project(
        text=segmented_text,
        known_projects=working_known_projects,
        source_title=source_title or default_project,
        fallback_project=default_project,
    )
    registry_context = build_registry_context(
        segment_meta.get("project_hints", []) or registry_project_hints,
        registry,
    )
    working_text = segmented_text
    local_hint_tasks: List[Dict[str, Any]] = []
    local_meta: Dict[str, Any] = {"enabled": False}

    local_preprocess, local_preprocess_meta = local_preprocess_minutes(
        working_text,
        env,
        working_default_project,
        working_known_projects,
        today,
        registry_context=registry_context,
    )
    local_meta = {
        "enabled": True,
        "result": local_preprocess_meta,
        "segmentation": segment_meta,
        "registry_hints": registry_project_hints[:6],
    }
    if isinstance(local_preprocess, dict):
        cleaned_text = str(local_preprocess.get("cleaned_text") or "").strip()
        if cleaned_text:
            working_text = cleaned_text
        primary_project = str(local_preprocess.get("primary_project") or "").strip()
        if primary_project:
            working_default_project = primary_project
        project_hints = [
            str(x).strip()
            for x in (local_preprocess.get("project_hints") or [])
            if str(x).strip()
        ]
        if project_hints:
            working_known_projects = list(dict.fromkeys(working_known_projects + project_hints))
        local_hint_tasks = tasks_from_local_preprocess(
            local_preprocess,
            working_default_project,
            working_known_projects,
            max_items=int(env.get("MINUTES_LOCAL_PREPROCESS_MAX_TASKS", "8")),
        )
        local_meta["preprocess"] = {
            "primary_project": working_default_project,
            "project_hints": project_hints[:8],
            "action_candidates": (
                local_preprocess.get("action_candidates")[:20]
                if isinstance(local_preprocess.get("action_candidates"), list)
                else []
            ),
            "noise_notes": (
                local_preprocess.get("noise_notes")[:20]
                if isinstance(local_preprocess.get("noise_notes"), list)
                else []
            ),
            "hint_task_count": len(local_hint_tasks),
        }

    known = ", ".join(sorted(set([p for p in working_known_projects if p]))[:25])
    min_tasks_if_long = int(env.get("MINUTES_MIN_TASKS_IF_LONG", "4"))
    long_doc_chars = int(env.get("MINUTES_LONG_DOC_CHARS", "1200"))
    review, review_raw = review_minutes_with_gemini(
        working_text,
        env,
        working_default_project,
        working_known_projects,
        today,
        registry_context=registry_context,
    )
    if review:
        llm_tasks, task_raw = extract_tasks_with_gemini_from_review(
            review,
            env,
            working_default_project,
            working_known_projects,
            today,
            registry_context=registry_context,
        )
        review_tasks = tasks_from_review_object(
            review,
            working_default_project,
            working_known_projects,
            max_per_project=int(env.get("MINUTES_REVIEW_MAX_PER_PROJECT", "8")),
            max_cross_project=int(env.get("MINUTES_REVIEW_MAX_CROSS_PROJECT", "8")),
        )
        adjudication_raw = ""
        adjudicated_review_tasks = review_tasks
        if env_flag(env, "MINUTES_REVIEW_ADJUDICATE_ENABLE", True) and review_tasks:
            adjudicated, adjudication_raw = adjudicate_review_candidates_with_gemini(
                review,
                review_tasks,
                env,
                working_default_project,
                working_known_projects,
                today,
                existing_tasks=_merge_tasks(
                    local_hint_tasks,
                    llm_tasks,
                    limit=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
                ),
                registry_context=registry_context,
            )
            if adjudicated is not None:
                adjudicated_review_tasks = adjudicated
                if not adjudicated_review_tasks:
                    adjudicated_review_tasks = fallback_review_candidates_for_empty_adjudication(
                        review_tasks,
                        limit=int(env.get("MINUTES_REVIEW_ADJUDICATE_EMPTY_FALLBACK", "2")),
                    )
        merged_review_tasks = _merge_tasks(
            local_hint_tasks,
            llm_tasks,
            adjudicated_review_tasks,
            limit=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
        )
        coverage_raw = ""
        if len(working_text) >= long_doc_chars and len(merged_review_tasks) < min_tasks_if_long:
            coverage_tasks, coverage_raw = extract_coverage_tasks_from_review(
                review,
                merged_review_tasks,
                env,
                working_default_project,
                working_known_projects,
                today,
                registry_context=registry_context,
            )
            if coverage_tasks:
                merged_review_tasks = _merge_tasks(
                    merged_review_tasks,
                    coverage_tasks,
                    limit=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
                )
        if not merged_review_tasks:
            rescue_tasks = fallback_review_section_rescue_tasks(
                review,
                working_default_project,
                working_known_projects,
                max_items=int(env.get("MINUTES_REVIEW_EMPTY_RESCUE_MAX_TASKS", "3")),
            )
            if rescue_tasks:
                merged_review_tasks = _merge_tasks(
                    merged_review_tasks,
                    rescue_tasks,
                    limit=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
                )
        if merged_review_tasks:
            return merged_review_tasks, json.dumps(
                {
                    "pipeline": "gemini_two_stage",
                    "review_raw": review_raw[:1200],
                    "task_raw": task_raw[:1200],
                    "review_adjudication_raw": adjudication_raw[:1200],
                    "coverage_raw": coverage_raw[:1200],
                    "local_preprocess": local_meta,
                },
                ensure_ascii=False,
            )

    prompt = (
        "Extract actionable tasks from the meeting minutes. "
        "Ignore pure status notes, commentary, criticism, retrospective feedback, and context-only memo lines. "
        "If tasks are related, group them under a parent task with a `subtasks` array. "
        "Return ONLY a JSON array. Each item has keys: title, due_date, project, assignee, note, subtasks (optional). "
        "Each subtask uses the same schema (title, due_date, project, assignee, note). "
        "due_date must be YYYY-MM-DD or empty string. "
        "Output titles, notes, and subtasks in Japanese only. "
        f"Today is {today} (JST). "
        f"Default project: {working_default_project}. "
        f"Known projects: {known}. "
        "Use the most appropriate project name if indicated. If not sure, use the default project. "
        "Prefer fewer high-quality actionable tasks over many vague bullets."
    )
    parsed, raw = _run_gemini_json_prompt_with_retry(
        working_text,
        prompt,
        env,
        model_list_key="MINUTES_SUMMARY_MODELS",
        fallback_models=[
            env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview"),
            "google/gemini-2.5-pro",
        ],
        max_output_tokens=env.get("MINUTES_SUMMARIZE_MAX_TOKENS", "1600"),
        retry_max_output_tokens=env.get("MINUTES_SUMMARIZE_RETRY_MAX_TOKENS", "2600"),
        length=env.get("MINUTES_SUMMARIZE_LENGTH", "xxl"),
        timeout_sec=int(env.get("MINUTES_SUMMARIZE_TIMEOUT_SEC", "120")),
        retry_timeout_sec=int(env.get("MINUTES_SUMMARIZE_RETRY_TIMEOUT_SEC", "200")),
    )
    coerced = _coerce_task_array(parsed)
    if coerced:
        tasks = _merge_tasks(
            local_hint_tasks,
            coerced,
            limit=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
        )
    else:
        tasks = list(local_hint_tasks)

    compact_prompt = (
        "Extract only high-confidence actionable tasks from the meeting minutes. "
        "Return ONLY a JSON array with at most 8 items. "
        "Each item must have keys: title, due_date, project, assignee, note. "
        "Do not include subtasks in this compact mode. "
        "Ignore pure status notes and background explanations. "
        "Prefer concise verb-led tasks. "
        "Output titles and notes in Japanese only. "
        f"Today is {today} (JST). Default project: {working_default_project}. Known projects: {known}."
    )
    parsed_compact, raw_compact = _run_gemini_json_prompt_with_retry(
        working_text,
        compact_prompt,
        env,
        model_list_key="MINUTES_COMPACT_MODELS",
        fallback_models=[
            env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview"),
            "google/gemini-2.5-pro",
        ],
        max_output_tokens=env.get("MINUTES_COMPACT_MAX_TOKENS", "1400"),
        retry_max_output_tokens=env.get("MINUTES_COMPACT_RETRY_MAX_TOKENS", "2200"),
        length=env.get("MINUTES_COMPACT_LENGTH", "m"),
        timeout_sec=int(env.get("MINUTES_COMPACT_TIMEOUT_SEC", "120")),
        retry_timeout_sec=int(env.get("MINUTES_COMPACT_RETRY_TIMEOUT_SEC", "180")),
    )
    coerced_compact = _coerce_task_array(parsed_compact)
    if coerced_compact:
        tasks = _merge_tasks(
            tasks,
            coerced_compact,
            limit=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
        )

    malformed_source = (raw_compact or raw or "").strip()
    repair_raw = ""
    if malformed_source and (not _is_explicit_empty_tasks(malformed_source)):
        repair_prompt = (
            "Convert the following extraction output into valid JSON array only. "
            "Schema for each item: title, due_date, project, assignee, note. "
            "If subtasks are found, keep them as `subtasks` array with same schema. "
            "Do not add explanations, markdown, or code fences."
        )
        parsed_repair, raw_repair = _run_gemini_json_prompt_with_retry(
            malformed_source,
            repair_prompt,
            env,
            model_list_key="MINUTES_REPAIR_MODELS",
            fallback_models=[
                env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview"),
                "google/gemini-2.5-pro",
            ],
            max_output_tokens=env.get("MINUTES_REPAIR_MAX_TOKENS", "2200"),
            retry_max_output_tokens=env.get("MINUTES_REPAIR_RETRY_MAX_TOKENS", "3200"),
            length=env.get("MINUTES_REPAIR_LENGTH", "l"),
            timeout_sec=int(env.get("MINUTES_REPAIR_TIMEOUT_SEC", "120")),
            retry_timeout_sec=int(env.get("MINUTES_REPAIR_RETRY_TIMEOUT_SEC", "180")),
        )
        coerced_repair = _coerce_task_array(parsed_repair)
        if coerced_repair:
            tasks = _merge_tasks(
                tasks,
                coerced_repair,
                limit=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
            )
        else:
            partial_tasks = _extract_tasks_from_partial_json_text(raw_repair or malformed_source, working_default_project)
            if partial_tasks:
                tasks = _merge_tasks(
                    tasks,
                    partial_tasks,
                    limit=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
                )
        repair_raw = raw_repair or malformed_source

    final_raw = repair_raw or raw_compact or raw
    if len(working_text) >= long_doc_chars and len(tasks) < min_tasks_if_long:
        existing_titles = ", ".join([str(x.get("title") or "") for x in tasks][:20])
        enrich_prompt = (
            "Extract additional high-confidence actionable tasks that are missing from current extraction. "
            "Return ONLY JSON array. Each item: title, due_date, project, assignee, note. "
            "Avoid duplicates against existing titles. "
            f"Existing titles: {existing_titles}. "
            f"Today is {today} (JST). Default project: {working_default_project}. Known projects: {known}."
        )
        parsed_enrich, raw_enrich = _run_gemini_json_prompt_with_retry(
            working_text,
            enrich_prompt,
            env,
            model_list_key="MINUTES_ENRICH_MODELS",
            fallback_models=[
                env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview"),
                "google/gemini-2.5-pro",
            ],
            max_output_tokens=env.get("MINUTES_ENRICH_MAX_TOKENS", "1800"),
            retry_max_output_tokens=env.get("MINUTES_ENRICH_RETRY_MAX_TOKENS", "2600"),
            length=env.get("MINUTES_ENRICH_LENGTH", "m"),
            timeout_sec=int(env.get("MINUTES_ENRICH_TIMEOUT_SEC", "120")),
            retry_timeout_sec=int(env.get("MINUTES_ENRICH_RETRY_TIMEOUT_SEC", "180")),
        )
        enrich_tasks = _coerce_task_array(parsed_enrich)
        if enrich_tasks:
            tasks = _merge_tasks(
                tasks,
                enrich_tasks,
                limit=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
            )
            final_raw = raw_enrich or final_raw

    if len(working_text) >= long_doc_chars and len(tasks) < min_tasks_if_long:
        boost = local_recall_boost_tasks(
            working_text,
            working_default_project,
            working_known_projects,
            max_projects=int(env.get("MINUTES_RECALL_BOOST_MAX_PROJECTS", "4")),
            max_items_per_project=int(env.get("MINUTES_RECALL_BOOST_MAX_ITEMS_PER_PROJECT", "5")),
        )
        if boost:
            tasks = _merge_tasks(
                tasks,
                boost,
                limit=int(env.get("MINUTES_MAX_TASKS_PER_DOC", "20")),
            )
            return tasks, json.dumps(
                {
                    "pipeline": "fallback_with_local_boost",
                    "raw": (final_raw or "")[:1200],
                    "local_preprocess": local_meta,
                },
                ensure_ascii=False,
            )

    if tasks:
        return tasks, json.dumps(
            {
                "pipeline": "fallback_single_stage",
                "raw": (final_raw or "")[:1200],
                "local_preprocess": local_meta,
            },
            ensure_ascii=False,
        )
    return [], json.dumps(
        {
            "pipeline": "fallback_empty",
            "raw": (final_raw or "")[:1200],
            "local_preprocess": local_meta,
        },
        ensure_ascii=False,
    )

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
    assignee = _canonicalize_assignee(str(item.get("assignee") or ""))
    note = (item.get("note") or "").strip()
    return {
        "title": title,
        "project": project,
        "due_date": due_date,
        "assignee": assignee,
        "note": note,
    }


def _build_minutes_group_parent_title(project: str, source_title: str) -> str:
    cleaned_source = _clean_line(source_title or "")[:80]
    if cleaned_source and project and project in cleaned_source:
        return cleaned_source[:120]
    if cleaned_source:
        return f"{project} / {cleaned_source}"[:120]
    return f"{project} 対応タスク"[:120]


def _normalize_minutes_parent_title(parent_title: str, project: str, source_title: str) -> str:
    project = (project or "").strip() or "TOKIWAGI"
    raw = _clean_line(parent_title or "")
    source = _clean_line(source_title or "")
    if raw and re.search(r"対応タスク$", raw):
        return _build_minutes_group_parent_title(project, source)
    if raw and not (_looks_noise_task_title(raw) and not _has_action_signal(raw)):
        if project and project not in raw:
            if source and (raw == source or raw in source):
                return _build_minutes_group_parent_title(project, source)
            return f"{project} / {raw}"[:120]
        return raw[:120]
    return _build_minutes_group_parent_title(project, source)


def _group_leaf_minutes_tasks_by_project(
    extracted: List[Dict[str, Any]],
    default_project: str,
    source_title: str,
) -> List[Dict[str, Any]]:
    grouped: List[Dict[str, Any]] = []
    flat_by_project: Dict[str, List[Dict[str, Any]]] = {}

    for item in extracted:
        raw_subtasks = item.get("subtasks") or item.get("children") or []
        if isinstance(raw_subtasks, list) and raw_subtasks:
            grouped.append(item)
            continue

        normalized = _normalize_task_item(item, default_project)
        if not normalized.get("title"):
            continue
        project = normalized.get("project") or default_project
        flat_by_project.setdefault(project, []).append(normalized)

    for project, subtasks in flat_by_project.items():
        parent_note = "自動グループ化: 同一議事録・同一プロジェクトの抽出タスクを親子化"
        visible_assignees = [
            _canonicalize_assignee(str(sub.get("assignee") or ""))
            for sub in subtasks
            if _canonicalize_assignee(str(sub.get("assignee") or ""))
        ]
        parent_assignee = ""
        if visible_assignees:
            unique_assignees = list(dict.fromkeys(visible_assignees))
            if len(unique_assignees) == 1:
                parent_assignee = unique_assignees[0]
        grouped.append(
            {
                "title": _build_minutes_group_parent_title(project, source_title),
                "project": project,
                "due_date": "",
                "assignee": parent_assignee,
                "note": parent_note,
                "subtasks": subtasks,
            }
        )

    return grouped


def _project_alias_hit_count(project: str, text: str) -> int:
    blob = text or ""
    if not blob or not project:
        return 0
    hits = 0
    for alias in _project_aliases(project):
        alias_text = str(alias or "").strip()
        if not alias_text:
            continue
        if alias_text in blob:
            hits += 1
    return hits


def _assess_context_seed_task_fit(project: str, title: str, note: str) -> Dict[str, Any]:
    target = _canonical_project_display_name(project or "")
    positive_hints = PROJECT_TASK_POSITIVE_HINTS_REGISTRY.get(target, [])
    negative_hints = PROJECT_TASK_NEGATIVE_HINTS_REGISTRY.get(target, [])
    title_text = str(title or "")
    note_text = str(note or "")
    blob = "\n".join([title_text, note_text]).strip()
    if not target or not blob:
        return {"positive_hits": 0, "negative_hits": 0, "drop": False, "score_delta": 0}

    positive_hits = sum(1 for hint in positive_hints if hint and hint in blob)
    negative_hits = sum(1 for hint in negative_hints if hint and hint in blob)
    has_action = _has_action_signal(title_text) or _has_action_signal(note_text)
    looks_noise = _looks_noise_task_title(title_text)
    clean_title = _clean_line(title_text)
    exact_negative_hit = any(_clean_line(hint) == clean_title for hint in negative_hints if hint)

    drop = False
    if negative_hits and not positive_hits and exact_negative_hit:
        drop = True
    elif negative_hits and not positive_hits and not has_action:
        drop = True
    elif negative_hits and not positive_hits and looks_noise:
        drop = True

    score_delta = min(positive_hits, 2)
    if negative_hits and not positive_hits:
        score_delta -= 1
    return {
        "positive_hits": positive_hits,
        "negative_hits": negative_hits,
        "drop": drop,
        "score_delta": score_delta,
    }


def _has_self_scope_evidence(project: str, title: str, note: str, context_fit: Dict[str, Any]) -> bool:
    target = _canonical_project_display_name(project or "")
    if not PROJECT_LOW_SELF_INVOLVEMENT.get(target):
        return True
    blob = "\n".join([str(title or ""), str(note or "")])
    self_aliases = _get_self_owner_aliases()
    if any(alias and alias in blob for alias in self_aliases):
        return True
    if int(context_fit.get("positive_hits", 0) or 0) > 0:
        return True
    return False


def _has_confident_minutes_project(
    project: str,
    title: str,
    note: str,
    source_title: str,
    default_project: str,
    known_projects: List[str],
    doc_project_hints: Optional[List[str]] = None,
    registry: Optional[Dict[str, Any]] = None,
) -> bool:
    target = _canonical_project_display_name(project or "")
    if not target:
        return False

    blob_parts = [str(title or ""), str(note or ""), str(source_title or "")]
    blob = "\n".join([part for part in blob_parts if part]).strip()
    explicit_project = _match_known_project_name(project, known_projects)
    inferred_project = _infer_project_from_text(blob, known_projects)
    registry_hints = infer_registry_project_hints(blob, source_title, registry or {})
    doc_hints = [
        _canonical_project_display_name(str(name or ""))
        for name in (doc_project_hints or [])
        if _canonical_project_display_name(str(name or ""))
    ]
    alias_hits = _project_alias_hit_count(target, blob)
    source_alias_hits = _project_alias_hit_count(target, source_title or "")
    context_fit = _assess_context_seed_task_fit(target, title, note)

    score = 0
    if context_fit.get("drop"):
        return False
    if not _has_self_scope_evidence(target, title, note, context_fit):
        return False
    if explicit_project and _canonical_project_display_name(explicit_project) == target:
        score += 2
    if alias_hits > 0:
        score += 2 + min(alias_hits, 2)
    if source_alias_hits > 0:
        score += 1
    if inferred_project and _canonical_project_display_name(inferred_project) == target:
        score += 3
    if target in registry_hints:
        score += 2
    if target in doc_hints:
        score += 2
    if note and "review.project_sections.action_candidates" in note:
        score += 1
    score += int(context_fit.get("score_delta", 0) or 0)

    has_conflict = bool(
        inferred_project
        and _canonical_project_display_name(inferred_project) != target
        and _canonical_project_display_name(inferred_project) not in GENERIC_PROJECT_NAMES
    )
    if has_conflict:
        score -= 3
    if note and "review.cross_project_actions" in note:
        score -= 2
    if doc_hints and target not in doc_hints and target not in GENERIC_PROJECT_NAMES:
        score -= 2

    inherited_only = not explicit_project or (
        default_project
        and _canonical_project_display_name(explicit_project or "") == _canonical_project_display_name(default_project)
    )
    target_is_generic = target in GENERIC_PROJECT_NAMES

    if target_is_generic:
        return score >= 4
    if doc_hints and inherited_only and target not in doc_hints:
        if alias_hits <= 0 and source_alias_hits <= 0 and target not in registry_hints:
            return False
    if explicit_project and _canonical_project_display_name(explicit_project) == target and not has_conflict:
        if not (note and "review.cross_project_actions" in note):
            return score >= 2
    if inherited_only and score < 3:
        return False
    if has_conflict and score < 5:
        return False
    return score >= 3


def build_neuronic_tasks(
    extracted: List[Dict[str, Any]],
    source: str,
    source_title: str,
    source_url: str,
    default_project: str,
    source_id: str,
    run_id: str,
    known_projects: Optional[List[str]] = None,
    doc_project_hints: Optional[List[str]] = None,
    registry: Optional[Dict[str, Any]] = None,
    include_legacy_group_tag: bool = False,
) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    effective_known_projects = list(known_projects or [])
    if default_project:
        effective_known_projects.append(default_project)
    for item in extracted:
        project_name = _canonical_project_display_name(str(item.get("project") or ""))
        if project_name:
            effective_known_projects.append(project_name)
        for child in item.get("subtasks") or item.get("children") or []:
            if not isinstance(child, dict):
                continue
            child_project = _canonical_project_display_name(str(child.get("project") or ""))
            if child_project:
                effective_known_projects.append(child_project)
    effective_known_projects = list(dict.fromkeys([p for p in effective_known_projects if p]))
    grouped_extracted = _group_leaf_minutes_tasks_by_project(extracted, default_project, source_title)
    group_index = 0
    for item in grouped_extracted:
        normalized = _normalize_task_item(item, default_project)
        title = normalized.get("title")
        if not title:
            continue
        subtasks = item.get("subtasks") or item.get("children") or []
        parent_assignee = normalized.get("assignee") or ""

        filtered_subtasks: List[Dict[str, Any]] = []
        if isinstance(subtasks, list) and subtasks:
            for sub in subtasks:
                sub_norm = _normalize_task_item(sub, normalized.get("project") or default_project)
                if not sub_norm.get("title"):
                    continue
                if not _should_emit_minutes_task(sub_norm.get("assignee") or ""):
                    continue
                if not _has_confident_minutes_project(
                    sub_norm.get("project") or normalized.get("project") or default_project,
                    sub_norm.get("title") or "",
                    str(sub_norm.get("note") or ""),
                    source_title,
                    normalized.get("project") or default_project,
                    effective_known_projects,
                    doc_project_hints=doc_project_hints,
                    registry=registry,
                ):
                    continue
                filtered_subtasks.append(sub_norm)
            subtasks = filtered_subtasks
            if not subtasks:
                continue
            child_assignees = [
                _canonicalize_assignee(str(sub.get("assignee") or ""))
                for sub in subtasks
                if _canonicalize_assignee(str(sub.get("assignee") or ""))
            ]
            if child_assignees:
                unique_child_assignees = list(dict.fromkeys(child_assignees))
                if len(unique_child_assignees) == 1:
                    parent_assignee = unique_child_assignees[0]
                elif "私" in unique_child_assignees and len(unique_child_assignees) == 1:
                    parent_assignee = "私"
                else:
                    parent_assignee = ""
            else:
                parent_assignee = ""
        else:
            if not _should_emit_minutes_task(parent_assignee):
                continue
            if not _has_confident_minutes_project(
                normalized.get("project") or default_project,
                normalized.get("title") or "",
                normalized.get("note") or "",
                source_title,
                default_project,
                effective_known_projects,
                doc_project_hints=doc_project_hints,
                registry=registry,
            ):
                continue
        if not subtasks and not title:
            continue

        note = (
            (normalized.get("note") + "\n\n" if normalized.get("note") else "")
            + f"Source: {source}\n"
            + f"Title: {source_title}\n"
            + f"URL: {source_url}"
        )
        tags = [f"source:{source}", f"project:{normalized.get('project')}"]
        if parent_assignee:
            tags.append(f"assignee:{parent_assignee}")
        tags = _dedupe_tags(tags)

        parent_task = {
            "title": title,
            "project": normalized.get("project"),
            "due_date": normalized.get("due_date"),
            "assignee": parent_assignee,
            "note": note,
            "source": "roby",
            "status": "inbox",
            "priority": 1,
            "tags": tags,
            "parent_origin_id": None,
            "sibling_order": group_index,
            "outline_path": str(group_index),
            "run_id": run_id,
            "feedback_state": "pending",
            "source_doc_id": source_id,
            "source_doc_title": source_title,
        }
        parent_origin = _stable_origin_id(parent_task, f"{source_id}|parent|{group_index}")
        parent_task["origin_id"] = parent_origin
        group_ref = f"group:{parent_origin}"
        parent_task["external_ref"] = group_ref

        if subtasks:
            if include_legacy_group_tag:
                parent_task["tags"] = _dedupe_tags(parent_task["tags"] + [group_ref])
            tasks.append(parent_task)
            for sub_idx, sub in enumerate(subtasks):
                sub_norm = _normalize_task_item(sub, normalized.get("project") or default_project)
                sub_note = (
                    (sub_norm.get("note") + "\n\n" if sub_norm.get("note") else "")
                    + f"Parent: {title}\n"
                    + f"Source: {source}\n"
                    + f"Title: {source_title}\n"
                    + f"URL: {source_url}"
                )
                sub_tags = [f"source:{source}", f"project:{sub_norm.get('project')}"]
                if sub_norm.get("assignee"):
                    sub_tags.append(f"assignee:{sub_norm.get('assignee')}")
                sub_tags = _dedupe_tags(sub_tags)
                if include_legacy_group_tag:
                    sub_tags = _dedupe_tags(sub_tags + [group_ref])
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
                    "run_id": run_id,
                    "feedback_state": "pending",
                    "source_doc_id": source_id,
                    "source_doc_title": source_title,
                    "external_ref": group_ref,
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
        if "external_ref" in row:
            row["externalRef"] = row.get("external_ref")
        if "run_id" in row:
            row["runId"] = row.get("run_id")
        if "feedback_state" in row:
            row["feedbackState"] = row.get("feedback_state")
        if "source_doc_id" in row:
            row["sourceDocId"] = row.get("source_doc_id")
        if "source_doc_title" in row:
            row["sourceDocTitle"] = row.get("source_doc_title")
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


def _hierarchy_state_path(env: Dict[str, str]) -> Path:
    custom = (env.get("ROBY_NEURONIC_HIERARCHY_STATE_PATH") or "").strip()
    if custom:
        return Path(custom).expanduser()
    return HIERARCHY_STATE_PATH


def _load_known_hierarchy_origin_ids(env: Dict[str, str]) -> set[str]:
    path = _hierarchy_state_path(env)
    if not path.exists():
        return set()
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    ids = obj.get("known_origin_ids") if isinstance(obj, dict) else None
    if not isinstance(ids, list):
        return set()
    return {str(x).strip() for x in ids if str(x).strip()}


def _save_known_hierarchy_origin_ids(env: Dict[str, str], origin_ids: set[str]) -> None:
    path = _hierarchy_state_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(JST).isoformat(),
        "known_origin_ids": sorted(origin_ids),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_hierarchy_send_policy(tasks: List[Dict[str, Any]], env: Dict[str, str]) -> Tuple[List[Dict[str, Any]], set[str], str, int]:
    mode = (env.get("ROBY_NEURONIC_HIERARCHY_MODE", "create_only") or "create_only").strip().lower()
    if mode not in {"always", "create_only"}:
        mode = "create_only"

    known = _load_known_hierarchy_origin_ids(env) if mode == "create_only" else set()
    out: List[Dict[str, Any]] = []
    suppressed = 0
    for item in tasks:
        row = dict(item)
        if mode == "create_only":
            origin_id = (row.get("origin_id") or "").strip()
            if origin_id and origin_id in known:
                had_hierarchy = False
                for key in ("parent_origin_id", "sibling_order", "outline_path", "parentOriginId", "siblingOrder", "outlinePath"):
                    if key in row:
                        had_hierarchy = had_hierarchy or (row.get(key) is not None)
                        row.pop(key, None)
                if had_hierarchy:
                    suppressed += 1
        out.append(row)
    return out, known, mode, suppressed


def _successful_origin_ids_from_response(batch: List[Dict[str, Any]], body: Dict[str, Any]) -> List[str]:
    if not batch:
        return []
    errors = body.get("errors")
    if not isinstance(errors, list) or not errors:
        return [str(it.get("origin_id") or "").strip() for it in batch if str(it.get("origin_id") or "").strip()]
    failed_indexes = set()
    for err in errors:
        if isinstance(err, dict) and "index" in err:
            try:
                failed_indexes.add(int(err.get("index")))
            except Exception:
                continue
    if not failed_indexes:
        return [str(it.get("origin_id") or "").strip() for it in batch if str(it.get("origin_id") or "").strip()]
    ok_ids: List[str] = []
    for idx, item in enumerate(batch):
        if idx in failed_indexes:
            continue
        oid = str(item.get("origin_id") or "").strip()
        if oid:
            ok_ids.append(oid)
    return ok_ids


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_feedback_manifest(tasks: List[Dict[str, Any]], run_id: str) -> None:
    items: List[Dict[str, Any]] = []
    for t in tasks:
        items.append(
            {
                "origin_id": t.get("origin_id", ""),
                "title": t.get("title", ""),
                "project": t.get("project", ""),
                "parent_origin_id": t.get("parent_origin_id", None),
                "source_doc_id": t.get("source_doc_id", ""),
                "source_doc_title": t.get("source_doc_title", ""),
                "feedback_state": t.get("feedback_state", "pending"),
            }
        )
    _append_jsonl(
        FEEDBACK_MANIFEST_PATH,
        {
            "event": "feedback_candidates",
            "timestamp": datetime.now(JST).isoformat(),
            "run_id": run_id,
            "count": len(items),
            "items": items,
        },
    )


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
        return f"parent:{parent}"
    return f"root:{item.get('origin_id', '')}"


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

    send_tasks, known_hierarchy_ids, hierarchy_mode, suppressed_hierarchy_count = _apply_hierarchy_send_policy(tasks, env)
    default_batch = int(env.get("NEURONIC_BATCH_SIZE", "20"))
    max_batch_bytes = int(env.get("NEURONIC_MAX_BATCH_BYTES", "90000"))
    queue: List[List[Dict[str, Any]]] = _split_grouped_batches(send_tasks, default_batch, max_batch_bytes)
    verbose = env.get("ROBY_NEURONIC_VERBOSE", "0") == "1"
    items_with_parent, items_with_order = _count_payload_meta(send_tasks)

    aggregate: Dict[str, Any] = {
        "ok": True,
        "status_code": 200,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "error_count": 0,
        "batches": 0,
        "items_sent": len(send_tasks),
        "items_with_parent": items_with_parent,
        "items_with_order": items_with_order,
        "hierarchy_mode": hierarchy_mode,
        "suppressed_hierarchy_count": suppressed_hierarchy_count,
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
    successful_origin_ids: set[str] = set()

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
        for oid in _successful_origin_ids_from_response(current, body):
            successful_origin_ids.add(oid)
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

    if hierarchy_mode == "create_only" and successful_origin_ids:
        merged_ids = set(known_hierarchy_ids)
        merged_ids.update(successful_origin_ids)
        _save_known_hierarchy_origin_ids(env, merged_ids)

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


def format_minutes_slack(summary: Dict[str, Any]) -> str:
    run_id = str(summary.get("run_id", "-"))
    tasks = int(summary.get("tasks", 0) or 0)
    errors = int(summary.get("neuronic_errors", 0) or 0)
    notion_pages = int(summary.get("notion_pages", 0) or 0)
    gdocs = int(summary.get("gdocs", 0) or 0)
    candidates_total = int(summary.get("candidates_total", 0) or 0)
    candidates_selected = int(summary.get("candidates_selected", 0) or 0)
    skipped_non_minutes_docs = int(summary.get("skipped_non_minutes_docs", 0) or 0)
    created = int(summary.get("neuronic_created", 0) or 0)
    updated = int(summary.get("neuronic_updated", 0) or 0)
    skipped = int(summary.get("neuronic_skipped", 0) or 0)
    endpoint = str(summary.get("neuronic_endpoint", "-"))
    fallback = "あり" if bool(summary.get("neuronic_fallback", False)) else "なし"
    hierarchy = summary.get("hierarchy_applied")
    order = summary.get("order_applied")
    hierarchy_text = "-" if hierarchy is None else ("適用" if bool(hierarchy) else "未適用")
    order_text = "-" if order is None else ("適用" if bool(order) else "未適用")
    status = "失敗あり" if errors > 0 else ("変更あり" if tasks > 0 else "変更なし")

    lines = [
        "【Roby 議事録同期レポート】",
        f"・実行結果: {status}",
        f"・run_id: {run_id}",
        "",
        "■入力",
        f"・Notion対象ページ: {notion_pages}",
        f"・Google Docs対象: {gdocs}",
        f"・候補数: {candidates_total}（採用: {candidates_selected}）",
        f"・非議事録スキップ: {skipped_non_minutes_docs}",
        "",
        "■Neuronic連携",
        f"・生成タスク数: {tasks}",
        f"・created/updated/skipped: {created}/{updated}/{skipped}",
        f"・エラー数: {errors}",
        f"・endpoint: {endpoint}",
        f"・fallback: {fallback}",
        f"・階層適用: {hierarchy_text}",
        f"・順序適用: {order_text}",
    ]
    if summary.get("last_neuronic_error"):
        lines.extend(["", "■エラー詳細", str(summary.get("last_neuronic_error"))[:800]])
    return "\n".join(lines)


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
    parser.add_argument("--target", default="")
    parser.add_argument("--target-source", choices=["auto", "notion", "gdocs"], default="auto")
    args = parser.parse_args()

    env = load_env()
    if args.debug:
        env["ROBY_NEURONIC_VERBOSE"] = "1"
    notion_root = args.notion_root or env.get("TOKIWAGI_ROOT_ID") or env.get("NOTION_TOKIWAGI_ID", "")
    drive_folder = args.drive_folder or env.get("GDRIVE_MINUTES_FOLDER_ID", "")
    account = args.account or env.get("GOG_ACCOUNT", "")
    target_kind = detect_minutes_target_source(args.target, args.target_source)

    token = load_notion_key(env)
    notion_required = (not args.skip_notion) and (bool(notion_root) or target_kind == "notion")
    if not token and notion_required:
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
        "candidates_total": 0,
        "candidates_selected": 0,
        "candidate_items_capped": 0,
        "task_run_capped": 0,
        "task_run_cap_reached": False,
        "timed_out_docs": 0,
        "skipped_non_minutes_docs": 0,
    }

    candidates: List[Dict[str, Any]] = []
    known_projects: List[str] = []
    heuristic_used_docs = 0
    registry = load_tokiwagi_master_registry()
    structure: Dict[str, Any] = {}

    if args.target and not target_kind:
        print(f"ERROR: Unsupported target. Specify a Notion page URL/ID or Google Docs URL/ID: {args.target}")
        return 1

    # Notion structure
    if not args.skip_notion and notion_root:
        structure = load_cached_structure(notion_root)
        if not structure or args.refresh:
            structure = build_notion_structure(notion_root, token, env.get("NOTION_VERSION", "2025-09-03"), args.max)
            save_cached_structure(structure)
        known_projects = extend_known_projects_with_registry(
            [db.get("project") for db in structure.get("databases", [])],
            registry,
        )

        if not args.target:
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

    if not known_projects:
        known_projects = extend_known_projects_with_registry([], registry)

    # Google Docs
    if not args.target and not args.skip_gdocs and drive_folder:
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

    if args.target:
        try:
            candidates = [
                build_target_candidate(
                    args.target,
                    args.target_source,
                    env,
                    account,
                    token or "",
                    env.get("NOTION_VERSION", "2025-09-03"),
                    structure=structure,
                )
            ]
        except Exception as exc:
            print(f"ERROR: Failed to resolve target '{args.target}': {exc}")
            return 1

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
    else:
        max_candidates_per_run = int(env.get("MINUTES_MAX_CANDIDATES_PER_RUN", "30") or "30")
        if max_candidates_per_run > 0 and len(selected) > max_candidates_per_run:
            summary["candidate_items_capped"] = len(selected) - max_candidates_per_run
            selected = selected[:max_candidates_per_run]

    summary["candidates_total"] = len(candidates)
    summary["candidates_selected"] = len(selected)

    all_tasks: List[Dict[str, Any]] = []
    debug_records: List[Dict[str, Any]] = []
    run_id = build_run_id("minutes")
    include_legacy_group_tag = env.get("NEURONIC_LEGACY_GROUP_TAG", "0") == "1"
    max_tasks_per_run = int(env.get("MINUTES_MAX_TASKS_PER_RUN", "120") or "120")
    local_preprocess_budget = int(env.get("MINUTES_LOCAL_PREPROCESS_MAX_DOCS_PER_RUN", "1") or "1")
    doc_timeout_sec = int(env.get("MINUTES_DOC_TIMEOUT_SEC", "90") or "90")
    local_preprocess_used = 0

    for item in selected:
        reached_task_cap = False
        if item.get("source") == "notion":
            page_id = item.get("page_id")
            text = fetch_page_text(page_id, token, env.get("NOTION_VERSION", "2025-09-03"))
            if not text:
                processed_notion[page_id] = item.get("updated", "")
                continue
            quality = assess_minutes_candidate_quality(
                title=item.get("title", ""),
                text=text,
                source="notion",
                project=item.get("project") or "",
            )
            if not quality.get("ok"):
                summary["skipped_non_minutes_docs"] += 1
                processed_notion[page_id] = item.get("updated", "")
                if args.debug:
                    debug_records.append({
                        "source": "notion",
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "skipped": "non_minutes",
                        "reasons": quality.get("reasons", []),
                        "signals": quality.get("signals", {}),
                    })
                continue
            default_project = infer_primary_project(
                text=text,
                known_projects=known_projects,
                source_title=item.get("title", ""),
                fallback_project=item.get("project") or "TOKIWAGI",
            )
            effective_env = env
            if minutes_local_preprocess_would_run(text, env):
                if local_preprocess_budget >= 0 and local_preprocess_used >= local_preprocess_budget:
                    effective_env = dict(env)
                    effective_env["MINUTES_LOCAL_PREPROCESS_ENABLE"] = "0"
                else:
                    local_preprocess_used += 1
            timed_out = False
            try:
                extracted, raw_summary = run_with_doc_timeout(
                    doc_timeout_sec,
                    summarize_tasks,
                    text,
                    effective_env,
                    default_project,
                    known_projects,
                    today_str,
                    item.get("title", ""),
                )
            except MinutesDocTimeout:
                timed_out = True
                extracted, raw_summary = [], json.dumps(
                    {"pipeline": "doc_timeout", "timeout_sec": doc_timeout_sec},
                    ensure_ascii=False,
                )
            fallback_used = False
            if timed_out:
                summary["timed_out_docs"] += 1
            if timed_out or ((not extracted) and (not _is_explicit_empty_tasks(raw_summary))):
                extracted = heuristic_tasks_from_text(
                    text,
                    default_project,
                    known_projects,
                    max_projects=int(env.get("MINUTES_HEURISTIC_MAX_PROJECTS", "6")),
                    max_items_per_project=int(env.get("MINUTES_HEURISTIC_MAX_ITEMS_PER_PROJECT", "6")),
                )
                fallback_used = bool(extracted)
            if fallback_used:
                heuristic_used_docs += 1
            sanitized = sanitize_extracted_tasks(
                extracted,
                default_project,
                known_projects,
                f"{item.get('title', '')} / {item.get('db_title', '')}",
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
                    "timed_out": timed_out,
                })
            tasks = build_neuronic_tasks(
                sanitized,
                "notion",
                item.get("title", ""),
                item.get("url", ""),
                default_project,
                page_id,
                run_id,
                known_projects=known_projects,
                doc_project_hints=segment_minutes_text(
                    text,
                    default_project=default_project,
                    known_projects=known_projects,
                    source_title=item.get("title", ""),
                )[1].get("project_hints", []),
                registry=registry,
                include_legacy_group_tag=include_legacy_group_tag,
            )
            if max_tasks_per_run > 0:
                remaining = max_tasks_per_run - len(all_tasks)
                if remaining <= 0:
                    tasks = []
                    reached_task_cap = True
                elif len(tasks) > remaining:
                    summary["task_run_capped"] += (len(tasks) - remaining)
                    tasks = tasks[:remaining]
                    reached_task_cap = True
            all_tasks.extend(tasks)
            processed_notion[page_id] = item.get("updated", "")
            summary["notion_pages"] += 1
        elif item.get("source") == "gdocs":
            doc_id = item.get("doc_id")
            text = export_doc_text(doc_id, env, account)
            if not text:
                processed_gdocs[doc_id] = item.get("updated", "")
                continue
            quality = assess_minutes_candidate_quality(
                title=item.get("title", ""),
                text=text,
                source="gdocs",
                project=item.get("project") or "",
            )
            if not quality.get("ok"):
                summary["skipped_non_minutes_docs"] += 1
                processed_gdocs[doc_id] = item.get("updated", "")
                if args.debug:
                    debug_records.append({
                        "source": "gdocs",
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "skipped": "non_minutes",
                        "reasons": quality.get("reasons", []),
                        "signals": quality.get("signals", {}),
                    })
                continue
            default_project = infer_primary_project(
                text=text,
                known_projects=known_projects,
                source_title=item.get("title", ""),
                fallback_project="TOKIWAGI",
            )
            effective_env = env
            if minutes_local_preprocess_would_run(text, env):
                if local_preprocess_budget >= 0 and local_preprocess_used >= local_preprocess_budget:
                    effective_env = dict(env)
                    effective_env["MINUTES_LOCAL_PREPROCESS_ENABLE"] = "0"
                else:
                    local_preprocess_used += 1
            timed_out = False
            try:
                extracted, raw_summary = run_with_doc_timeout(
                    doc_timeout_sec,
                    summarize_tasks,
                    text,
                    effective_env,
                    default_project,
                    known_projects,
                    today_str,
                    item.get("title", ""),
                )
            except MinutesDocTimeout:
                timed_out = True
                extracted, raw_summary = [], json.dumps(
                    {"pipeline": "doc_timeout", "timeout_sec": doc_timeout_sec},
                    ensure_ascii=False,
                )
            fallback_used = False
            if timed_out:
                summary["timed_out_docs"] += 1
            if timed_out or ((not extracted) and (not _is_explicit_empty_tasks(raw_summary))):
                extracted = heuristic_tasks_from_text(
                    text,
                    default_project,
                    known_projects,
                    max_projects=int(env.get("MINUTES_HEURISTIC_MAX_PROJECTS", "6")),
                    max_items_per_project=int(env.get("MINUTES_HEURISTIC_MAX_ITEMS_PER_PROJECT", "6")),
                )
                fallback_used = bool(extracted)
            if fallback_used:
                heuristic_used_docs += 1
            sanitized = sanitize_extracted_tasks(
                extracted,
                default_project,
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
                    "timed_out": timed_out,
                })
            tasks = build_neuronic_tasks(
                sanitized,
                "gdocs",
                item.get("title", ""),
                item.get("url", ""),
                default_project,
                doc_id,
                run_id,
                known_projects=known_projects,
                doc_project_hints=segment_minutes_text(
                    text,
                    default_project=default_project,
                    known_projects=known_projects,
                    source_title=item.get("title", ""),
                )[1].get("project_hints", []),
                registry=registry,
                include_legacy_group_tag=include_legacy_group_tag,
            )
            if max_tasks_per_run > 0:
                remaining = max_tasks_per_run - len(all_tasks)
                if remaining <= 0:
                    tasks = []
                    reached_task_cap = True
                elif len(tasks) > remaining:
                    summary["task_run_capped"] += (len(tasks) - remaining)
                    tasks = tasks[:remaining]
                    reached_task_cap = True
            all_tasks.extend(tasks)
            processed_gdocs[doc_id] = item.get("updated", "")
            summary["gdocs"] += 1

        if reached_task_cap:
            summary["task_run_cap_reached"] = True
            break

    summary["tasks"] = len(all_tasks)
    summary["heuristic_used_docs"] = heuristic_used_docs
    summary["local_preprocess_docs"] = local_preprocess_used
    summary["local_preprocess_budget"] = local_preprocess_budget
    summary["run_id"] = run_id

    if not args.dry_run:
        if all_tasks:
            write_feedback_manifest(all_tasks, run_id)
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
    notify_on_no_change = env.get("MINUTES_NOTIFY_ON_NO_CHANGE", "0") == "1"
    should_notify = int(summary.get("neuronic_errors", 0)) > 0 or int(summary.get("tasks", 0)) > 0 or notify_on_no_change
    summary["slack_notified"] = False
    if slack_url and should_notify:
        try:
            send_slack(slack_url, format_minutes_slack(summary)[:3800])
            summary["slack_notified"] = True
        except Exception:
            pass

    if args.policy:
        summary["policy"] = args.policy

    if env.get("ROBY_IMMUTABLE_AUDIT", "1") == "1":
        try:
            append_audit_event(
                "minutes_sync.run",
                {
                    "run_id": run_id,
                    "notion_pages": int(summary.get("notion_pages", 0)),
                    "gdocs": int(summary.get("gdocs", 0)),
                    "candidates_total": int(summary.get("candidates_total", 0)),
                    "candidates_selected": int(summary.get("candidates_selected", 0)),
                    "tasks": int(summary.get("tasks", 0)),
                    "task_run_cap_reached": bool(summary.get("task_run_cap_reached", False)),
                    "heuristic_used_docs": int(summary.get("heuristic_used_docs", 0)),
                    "skipped_non_minutes_docs": int(summary.get("skipped_non_minutes_docs", 0)),
                    "neuronic_errors": int(summary.get("neuronic_errors", 0)),
                    "dry_run": bool(summary.get("dry_run", False)),
                    "policy": summary.get("policy", ""),
                },
                source="roby-minutes",
                run_id=run_id,
                severity="error" if int(summary.get("neuronic_errors", 0)) > 0 else "info",
            )
        except Exception:
            pass

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

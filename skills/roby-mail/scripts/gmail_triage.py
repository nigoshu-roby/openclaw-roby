#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
import hashlib
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Tuple

STATE_PATH = Path.home() / ".openclaw" / "roby" / "gmail_triage_state.json"
RUN_LOG_PATH = Path.home() / ".openclaw" / "roby" / "gmail_triage_runs.jsonl"
RULES_PATH = Path.home() / ".openclaw" / "roby" / "gmail_triage_rules.json"
FEEDBACK_MANIFEST_PATH = Path.home() / ".openclaw" / "roby" / "feedback_candidates.jsonl"

DEFAULT_QUERY = "newer_than:2d in:inbox"
DEFAULT_MAX = 50


def build_run_id(prefix: str = "gmail") -> str:
    seed = f"{time.time_ns()}|{os.getpid()}|{prefix}"
    return f"roby:{prefix}:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"

RELATED_TOOLS = [
    "liny",
    "line",
    "line公式",
    "line広告",
    "yellowfin",
    "autoro",
    "synergy!",
    "google",
    "aws",
    "notion",
]

IMPORTANT_KEYWORDS = [
    "至急",
    "緊急",
    "期限",
    "有効期限",
    "更新",
    "renew",
    "expire",
    "失効",
    "請求",
    "支払い",
    "見積",
    "契約",
    "申請",
    "承認",
    "確認",
    "ご確認",
    "判断",
    "相談",
    "依頼",
    "お願い",
    "対応",
    "返答",
    "返信",
    "ご返信",
    "ご回答",
    "お手数",
]

AD_HINTS = [
    "noreply",
    "no-reply",
    "newsletter",
    "お知らせ",
    "最新情報",
    "キャンペーン",
    "プロモーション",
    "sale",
    "coupon",
    "セミナー",
    "イベント",
    "marketing",
    "広告",
    "unsubscribe",
    "セール",
]

PROMO_SUBJECT_HINTS = [
    "開催",
    "申込受付中",
    "主催",
    "セミナー",
    "ウェビナー",
    "webinar",
    "メルマガ",
    "新着情報",
    "ads update",
    "not sure where to start",
    "アップデート",
    "連携できる",
    "無料で試せる",
    "今すぐ直せる",
    "成果にまだ間に合う",
    "アンケート",
    "お知らせが",
    "実践を語る",
]

ACTIONABLE_NOTICE_HINTS = [
    "アカウント発行",
    "アカウント発行のお知らせ",
    "スケジュールエラー通知",
    "pipeline",
    "etl結果",
    "定例ミーティング",
    "ミーティングの件",
]

ALERT_HINTS = [
    "エラー",
    "障害",
    "失敗",
    "停止",
    "警告",
    "アラート",
    "critical",
    "incident",
]

RELATED_DOMAINS = {
    "line.me": "line",
    "linecorp.com": "line",
    "linebiz.com": "line",
    "autoro.io": "autoro",
    "notion.so": "notion",
}

PROMO_SENDER_DOMAINS = [
    "toridori.co.jp",
    "diggle.team",
    "innovation.co.jp",
    "sales-skygroup.jp",
    "billage.space",
    "one-stream.jp",
    "mapbox.com",
    "necfru.com",
    "facebookmail.com",
    "stream.co.jp",
    "shein.com",
]

RULE_BUCKET_KEYS = ("sender_domains", "sender_contains", "subject_contains", "subject_regex")

DEFAULT_RULES_TEMPLATE: Dict[str, Dict[str, List[str]]] = {
    "force_archive": {
        "sender_domains": sorted(set(PROMO_SENDER_DOMAINS)),
        "sender_contains": [
            "yads-no-reply@mail.yahoo.co.jp",
            "blends-info@toridori.co.jp",
            "hello@mapbox.com",
        ],
        "subject_contains": [
            "申込受付中",
            "主催",
            "セミナー",
            "ウェビナー",
            "メルマガ",
            "ads update",
            "新着情報",
            "キャンペーンの予算が消化されました",
            "広告が承認されました",
            "広告アカウントが承認されました",
            "お知らせが",
        ],
        "subject_regex": [],
    },
    "force_review": {
        "sender_domains": [
            "tokiwa-gi.com",
            "crmstyle.com",
            "autoro.io",
            "zuiho-group.co.jp",
        ],
        "sender_contains": [
            "support@crmstyle.com",
            "noreply@autoro.io",
            "<internal-user-email>",
        ],
        "subject_contains": [
            "定例ミーティング",
            "ミーティングの件",
            "打ち合わせ",
            "日程",
            "アカウント発行",
            "スケジュールエラー通知",
            "pipeline",
            "etl結果",
        ],
        "subject_regex": [],
    },
    "force_reply": {
        "sender_domains": [],
        "sender_contains": [],
        "subject_contains": [],
        "subject_regex": [],
    },
}


def load_env() -> Dict[str, str]:
    env = dict(os.environ)
    env_path = Path.home() / ".openclaw" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip()
            if (val.startswith("\"") and val.endswith("\"")) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            env[key] = val
    return env


def ensure_state() -> Dict[str, Any]:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"processed": {}, "updated_at": None}
    return {"processed": {}, "updated_at": None}


def save_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = int(time.time())
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def log_run(entry: Dict[str, Any]) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_feedback_manifest(tasks: List[Dict[str, Any]], run_id: str) -> None:
    FEEDBACK_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    with FEEDBACK_MANIFEST_PATH.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "event": "feedback_candidates",
                    "ts": int(time.time()),
                    "run_id": run_id,
                    "count": len(items),
                    "items": items,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _normalize_rule_bucket(bucket: Dict[str, Any] | None) -> Dict[str, List[str]]:
    src = bucket if isinstance(bucket, dict) else {}
    out: Dict[str, List[str]] = {}
    for key in RULE_BUCKET_KEYS:
        values = src.get(key, [])
        if not isinstance(values, list):
            values = []
        cleaned = []
        seen = set()
        for v in values:
            s = str(v).strip()
            if not s:
                continue
            low = s.lower()
            if low in seen:
                continue
            seen.add(low)
            cleaned.append(s)
        out[key] = cleaned
    return out


def ensure_rules_file(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    template = {
        key: _normalize_rule_bucket(val) for key, val in DEFAULT_RULES_TEMPLATE.items()
    }
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_rules_with_defaults(data: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    merged: Dict[str, Any] = {}
    changed = False
    for category in ("force_archive", "force_review", "force_reply"):
        base = _normalize_rule_bucket(DEFAULT_RULES_TEMPLATE.get(category, {}))
        cur = _normalize_rule_bucket(data.get(category, {}))
        out_bucket: Dict[str, List[str]] = {}
        for key in RULE_BUCKET_KEYS:
            items = []
            seen = set()
            for src in (cur.get(key, []), base.get(key, [])):
                for v in src:
                    low = v.lower()
                    if low in seen:
                        continue
                    seen.add(low)
                    items.append(v)
            out_bucket[key] = items
            if items != cur.get(key, []):
                changed = True
        merged[category] = out_bucket
    if set(data.keys()) != {"force_archive", "force_review", "force_reply"}:
        changed = True
    return merged, changed


def load_rules(path: Path) -> Dict[str, Any]:
    ensure_rules_file(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw if isinstance(raw, dict) else {}
    except Exception:
        data = {}
    merged, changed = _merge_rules_with_defaults(data)
    if changed:
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def _match_rule_bucket(bucket: Dict[str, Any], subject_lower: str, sender_lower: str) -> bool:
    if not isinstance(bucket, dict):
        return False
    for dom in bucket.get("sender_domains", []) or []:
        if dom and str(dom).lower() in sender_lower:
            return True
    for token in bucket.get("sender_contains", []) or []:
        if token and str(token).lower() in sender_lower:
            return True
    for token in bucket.get("subject_contains", []) or []:
        if token and str(token).lower() in subject_lower:
            return True
    for pat in bucket.get("subject_regex", []) or []:
        if not pat:
            continue
        try:
            if re.search(str(pat), subject_lower, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def match_user_override(subject: str, sender: str, rules: Dict[str, Any], cc: str = "") -> Tuple[str | None, str | None]:
    subject_lower = (subject or "").lower()
    sender_lower = f"{sender or ''} {cc or ''}".lower()
    category_map = {
        "force_archive": "archive",
        "force_review": "needs_review",
        "force_reply": "needs_reply",
    }
    for category in ("force_reply", "force_review", "force_archive"):
        if _match_rule_bucket(rules.get(category, {}), subject_lower, sender_lower):
            return category_map[category], category
    return None, None


def strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<script.*?>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def gog_search(account: str, query: str, max_results: int, env: Dict[str, str]) -> List[Dict[str, Any]]:
    cmd = [
        "gog",
        "gmail",
        "messages",
        "search",
        query,
        "--max",
        str(max_results),
        "--json",
        "--results-only",
        "--include-body",
        "--no-input",
    ]
    if account:
        cmd += ["--account", account]
    try:
        out = subprocess.check_output(cmd, env=env, timeout=60)
        return json.loads(out)
    except subprocess.TimeoutExpired:
        return []


def archive_thread(account: str, thread_id: str, env: Dict[str, str]) -> None:
    cmd = [
        "gog",
        "gmail",
        "thread",
        "modify",
        thread_id,
        "--remove",
        "INBOX",
        "--no-input",
        "--force",
    ]
    if account:
        cmd += ["--account", account]
    subprocess.check_call(cmd, env=env)


def send_slack(webhook_url: str, text: str) -> None:
    import urllib.request

    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def summarize_tasks(text: str, env: Dict[str, str]) -> List[Dict[str, Any]]:
    prompt = (
        "Extract actionable tasks from the message. "
        "Return ONLY a JSON array of objects with keys: title, due_date, project, note. "
        "due_date must be YYYY-MM-DD or empty string. If no tasks, return []."
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
        "1200",
    ]
    out = subprocess.check_output(cmd, input=text.encode("utf-8"), env=env, timeout=60)
    data = json.loads(out)
    summary = data.get("summary", "")
    if not summary:
        return []
    # summary should be JSON array
    try:
        return json.loads(summary)
    except Exception:
        # try to extract JSON array
        m = re.search(r"\[.*\]", summary, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return []
        return []


def send_neuronic(tasks: List[Dict[str, Any]], env: Dict[str, str]) -> Dict[str, Any]:
    if not tasks:
        return {"created": 0, "updated": 0, "skipped": 0}
    import urllib.request
    import urllib.error

    url = env.get("NEURONIC_URL", "http://127.0.0.1:5174/api/v1/tasks/import")
    fallback_url = env.get("NEURONIC_FALLBACK_URL", "http://127.0.0.1:5174/api/v1/tasks/bulk")
    token = env.get("NEURONIC_TOKEN") or env.get("TASKD_AUTH_TOKEN")
    payload_items = []
    for item in tasks:
        row = dict(item)
        if "parent_origin_id" in row:
            row["parentOriginId"] = row.get("parent_origin_id")
        if "sibling_order" in row:
            row["siblingOrder"] = row.get("sibling_order")
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


def _stable_origin_id(task: Dict[str, Any], source_key: str = "") -> str:
    raw = "|".join([
        (task.get("title") or "").strip(),
        (task.get("project") or "").strip(),
        (task.get("due_date") or "").strip(),
        (task.get("assignee") or "").strip(),
        (source_key or "").strip(),
    ])
    sha1_12 = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"roby:auto:{sha1_12}"


def _dedupe_tags(tags: List[str]) -> List[str]:
    seen = set()
    out = []
    for t in tags:
        if not t:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def classify_message(subject: str, sender: str, body: str, rules: Dict[str, Any] | None = None, cc: str = "") -> Tuple[str, List[str], bool, str | None]:
    text = f"{subject} {sender} {cc} {body}".lower()
    header_text = f"{subject} {sender} {cc}".lower()
    tags = []
    needs_reply = False
    sender_lower = (sender or "").lower()
    cc_lower = (cc or "").lower()
    subject_lower = (subject or "").lower()
    is_noreply = "no-reply" in sender_lower or "noreply" in sender_lower

    def _tool_match(tool: str) -> bool:
        t = tool.lower()
        if re.fullmatch(r"[a-z0-9!+._-]+", t):
            # Avoid substring false-positives like "line" in "pipeline".
            return re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", header_text) is not None
        return t in header_text

    related = [tool for tool in RELATED_TOOLS if _tool_match(tool)]
    if not related:
        for dom, label in RELATED_DOMAINS.items():
            if dom in sender_lower:
                related = [label]
                break
    if related:
        tags.extend([f"tool:{t}" for t in related])

    override_category, override_rule = match_user_override(subject, sender, rules or {}, cc=cc)
    if override_category:
        return override_category, _dedupe_tags(tags + [f"rule:{override_rule}"]), (override_category == "needs_reply"), override_rule

    # Internal company domain in sender/CC should always be reviewed.
    if "tokiwa-gi.com" in sender_lower or "tokiwa-gi.com" in cc_lower:
        return "needs_review", _dedupe_tags(tags + ["rule:internal_domain_review"]), needs_reply, "internal_domain_review"

    urgent = any(k in text for k in IMPORTANT_KEYWORDS)
    is_alert = any(k in text for k in ALERT_HINTS)
    is_ad_hint = any(h in text for h in AD_HINTS)
    is_promo_subject = any(h.lower() in subject_lower for h in PROMO_SUBJECT_HINTS)
    is_actionable_notice = any(h.lower() in text for h in ACTIONABLE_NOTICE_HINTS)

    is_marketing_sender = any(x in sender_lower for x in [
        "seminar",
        "event",
        "marketing",
        "news",
        "mailmag",
        "メルマガ",
        "運営事務局",
    ])
    is_promo_sender_domain = any(dom in sender_lower for dom in PROMO_SENDER_DOMAINS)

    # Sender-domain blacklist is authoritative for known promotional sources.
    # Their bodies often contain words like "更新", "確認", "reply-to" that trigger false positives.
    if is_promo_sender_domain:
        return "archive", tags, False, None

    # Tool-specific operational notifications we still want to see.
    if ("support@crmstyle.com" in sender_lower or "synergy" in text) and "アカウント発行" in (subject or ""):
        return "needs_review", tags, needs_reply, None

    # AWS / batch job notifications are operationally important even on success.
    if "aws" in text and ("pipeline" in text or "etl" in text):
        return "needs_review", tags, needs_reply, None

    # Meeting / coordination mails should remain visible.
    if any(k in (subject or "") for k in ["定例ミーティング", "ミーティングの件", "打ち合わせ", "日程"]):
        return ("needs_reply" if needs_reply else "needs_review"), tags, needs_reply, None

    # Frequent ad-platform auto notices (approval/budget consumed/news) are noisy by default.
    if ("line.me" in sender_lower or "mail.yahoo.co.jp" in sender_lower) and is_noreply:
        if any(k in (subject or "") for k in ["広告が承認されました", "広告アカウントが承認されました", "予算が消化されました"]):
            return "archive", tags, needs_reply, None
        if "ads update" in subject_lower or "新着情報" in (subject or ""):
            return "archive", tags, needs_reply, None

    # Strong promotional signals override reply heuristics to reduce false positives.
    if (is_promo_subject or (is_ad_hint and is_marketing_sender)) and not is_alert and not is_actionable_notice:
        return "archive", tags, False, None

    reply_text = re.sub(r"reply-to", " ", text)
    has_reply_phrase = any(k in reply_text for k in ["返信", "ご返信", "ご回答", "ご対応"])
    has_reply_en = any(k in reply_text for k in ["please reply", "reply requested", "reply by"])
    if (not is_noreply) and (has_reply_phrase or has_reply_en):
        needs_reply = True

    if related:
        if is_noreply:
            if is_alert:
                return "needs_review", tags, needs_reply, None
            return "later_check", tags, needs_reply, None
        if urgent:
            return ("needs_reply" if needs_reply else "needs_review"), tags, needs_reply, None
        return ("needs_reply" if needs_reply else "later_check"), tags, needs_reply, None

    if urgent:
        return ("needs_reply" if needs_reply else "needs_review"), tags, needs_reply, None

    if is_ad_hint and is_noreply:
        return "archive", tags, needs_reply, None

    return "needs_review", tags, needs_reply, None


def build_tasks(
    extracted: List[Dict[str, Any]],
    msg: Dict[str, Any],
    category: str,
    tags: List[str],
    run_id: str,
) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    base_tags = ["source:gmail", f"category:{category}"] + tags
    assignee = "私"
    msg_subject = (msg.get("subject") or "").strip()
    msg_thread_id = (msg.get("threadId") or "").strip()
    msg_id = (msg.get("id") or "").strip()
    msg_url = f"https://mail.google.com/mail/u/0/#inbox/{msg_thread_id}"

    parent_task = {
        "title": f"メール確認: {msg_subject}" if msg_subject else "メール確認タスク",
        "project": "email",
        "due_date": "",
        "assignee": assignee,
        "note": (
            f"Email: {msg_subject}\n"
            f"From: {msg.get('from','')}\n"
            f"Date: {msg.get('date','')}\n"
            f"Link: {msg_url}"
        ),
        "source": "roby",
        "status": "inbox",
        "priority": 1 if category in ("needs_reply", "needs_review") else 0,
        "tags": _dedupe_tags(base_tags + ["project:email", f"assignee:{assignee}", "task_type:email_review"]),
        "parent_origin_id": None,
        "sibling_order": 0,
        "run_id": run_id,
        "feedback_state": "pending",
        "source_doc_id": msg_id or msg_thread_id,
        "source_doc_title": msg_subject,
    }
    parent_origin = _stable_origin_id(parent_task, f"{msg_thread_id}|parent")
    parent_task["origin_id"] = parent_origin
    parent_task["external_ref"] = f"group:{parent_origin}"
    tasks.append(parent_task)

    for i, item in enumerate(extracted):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        due = (item.get("due_date") or "").strip()
        project = (item.get("project") or "").strip() or "email"
        note = (item.get("note") or "").strip()
        item_tags = _dedupe_tags(base_tags + [f"project:{project}", f"assignee:{assignee}", "task_type:action"])
        note = (
            (note + "\n\n" if note else "")
            + f"Parent: {parent_task['title']}\n"
            + f"Email: {msg_subject}\n"
            + f"From: {msg.get('from','')}\n"
            + f"Date: {msg.get('date','')}\n"
            + f"Link: {msg_url}"
        )
        task = {
            "title": title,
            "project": project,
            "due_date": due,
            "assignee": assignee,
            "note": note,
            "source": "roby",
            "status": "inbox",
            "priority": 1 if category in ("needs_reply", "needs_review") else 0,
            "tags": item_tags,
            "parent_origin_id": parent_origin,
            "sibling_order": i,
            "run_id": run_id,
            "feedback_state": "pending",
            "source_doc_id": msg_id or msg_thread_id,
            "source_doc_title": msg_subject,
            "external_ref": f"group:{parent_origin}",
        }
        task["origin_id"] = _stable_origin_id(task, f"{msg_thread_id}|child|{i}")
        tasks.append(task)
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-tasks", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--rules-path", default="")
    parser.add_argument("--archive-ads", dest="archive_ads", action="store_true")
    parser.add_argument("--no-archive-ads", dest="archive_ads", action="store_false")
    parser.set_defaults(archive_ads=True)
    args = parser.parse_args()

    env = load_env()
    rules_path = Path(args.rules_path).expanduser() if args.rules_path else Path(env.get("ROBY_GMAIL_TRIAGE_RULES_PATH", str(RULES_PATH))).expanduser()
    rules = load_rules(rules_path)
    state = ensure_state()
    processed = state.get("processed", {})

    messages = gog_search(args.account, args.query, args.max, env)
    summary = {
        "total": len(messages),
        "new": 0,
        "archived": 0,
        "notified": 0,
        "tasks": 0,
        "neuronic_errors": 0,
    }
    run_id = build_run_id("gmail")
    summary["run_id"] = run_id
    last_neuronic_error: str | None = None
    category_counts: Dict[str, int] = {}

    skip_tasks = args.skip_tasks or args.dry_run

    for msg in messages:
        msg_id = msg.get("id")
        if not msg_id:
            continue
        if msg_id in processed:
            continue

        body = strip_html(msg.get("body", ""))
        subject = msg.get("subject", "")
        sender = msg.get("from", "")

        cc = msg.get("cc", "") or msg.get("ccs", "") or ""
        category, tags, needs_reply, rule_applied = classify_message(subject, sender, body, rules=rules, cc=cc)
        category_counts[category] = category_counts.get(category, 0) + 1
        summary["new"] += 1

        # Slack notify
        slack_url = env.get("SLACK_WEBHOOK_URL")
        if slack_url and not args.dry_run and category in ("needs_reply", "needs_review", "later_check"):
            msg_url = f"https://mail.google.com/mail/u/0/#inbox/{msg.get('threadId','')}"
            text = (
                f"[Gmail:{category}] {subject}\n"
                f"From: {sender}\n"
                f"Date: {msg.get('date','')}\n"
                f"{msg_url}"
            )
            send_slack(slack_url, text)
            summary["notified"] += 1

        # Task extraction
        tasks = []
        if (not skip_tasks) and category in ("needs_reply", "needs_review", "later_check"):
            try:
                extracted = summarize_tasks(
                    f"Subject: {subject}\n"
                    f"From: {sender}\n"
                    f"Date: {msg.get('date','')}\n\n"
                    f"{body}",
                    env,
                )
            except Exception:
                extracted = []
            if not extracted:
                if category == "needs_reply":
                    fallback_title = f"メール返信: {subject}"
                elif category == "later_check":
                    fallback_title = f"メール確認: {subject}"
                else:
                    fallback_title = f"メール対応: {subject}"
                extracted = [{"title": fallback_title, "due_date": "", "project": "email", "note": ""}]
            tasks = build_tasks(extracted, msg, category, tags, run_id=run_id)
            if tasks and not args.dry_run:
                write_feedback_manifest(tasks, run_id)
                resp = send_neuronic(tasks, env)
                if isinstance(resp, dict) and resp.get("error"):
                    summary["neuronic_errors"] += 1
                    last_neuronic_error = resp.get("detail") or resp.get("error")
                else:
                    summary["tasks"] += len(tasks)

        # Archive ads
        if category == "archive" and args.archive_ads and not args.dry_run:
            try:
                archive_thread(args.account, msg.get("threadId", ""), env)
                summary["archived"] += 1
            except Exception:
                pass

        if args.verbose:
            row = {
                "id": msg_id,
                "category": category,
                "subject": subject,
                "from": sender,
                "cc": cc,
                "tags": tags,
            }
            if rule_applied:
                row["rule"] = rule_applied
            print(json.dumps(row, ensure_ascii=False))

        if not args.dry_run:
            processed[msg_id] = int(time.time())

    if not args.dry_run:
        state["processed"] = processed
        save_state(state)

    log_run({
        "ts": int(time.time()),
        "query": args.query,
        "summary": summary,
    })

    summary["categories"] = category_counts
    summary["rules_path"] = str(rules_path)
    if args.verbose and last_neuronic_error:
        summary["last_neuronic_error"] = last_neuronic_error
        summary["neuronic_config"] = {
            "url": env.get("NEURONIC_URL", "http://127.0.0.1:5174/api/v1/tasks/import"),
            "token_present": bool(env.get("NEURONIC_TOKEN")),
        }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

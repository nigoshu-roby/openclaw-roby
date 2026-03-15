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
from email.utils import parseaddr
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OPENCLAW_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(OPENCLAW_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(OPENCLAW_SCRIPTS_DIR))

from roby_context_seed import load_context_seed
from roby_local_first import env_flag as shared_env_flag, int_from_env as shared_int_from_env, run_ollama_json

STATE_PATH = Path.home() / ".openclaw" / "roby" / "gmail_triage_state.json"
RUN_LOG_PATH = Path.home() / ".openclaw" / "roby" / "gmail_triage_runs.jsonl"
RULES_PATH = Path.home() / ".openclaw" / "roby" / "gmail_triage_rules.json"
FEEDBACK_MANIFEST_PATH = Path.home() / ".openclaw" / "roby" / "feedback_candidates.jsonl"
CONTACT_INDEX_PATH = Path.home() / ".openclaw" / "roby" / "gmail_contact_index.json"
ENV_PATH = Path.home() / ".openclaw" / ".env"
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

DEFAULT_QUERY = "newer_than:2d in:inbox"
DEFAULT_MAX = 50

WORK_BUCKETS = ("archive", "digest", "review", "task")
WORK_BUCKET_LABELS = {
    "archive": "アーカイブ候補",
    "digest": "後で確認",
    "review": "要確認",
    "task": "タスク化",
}


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
    "請求書",
    "支払い",
    "支払",
    "入金",
    "未払い",
    "見積",
    "見積もり",
    "見積書",
    "契約",
    "発注",
    "申込",
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

BUSINESS_REVIEW_KEYWORDS = [
    "請求",
    "請求書",
    "支払",
    "支払い",
    "入金",
    "未払い",
    "見積",
    "見積もり",
    "見積書",
    "契約",
    "更新",
    "発注",
    "申込",
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
    "請求",
    "請求書",
    "見積",
    "見積書",
    "契約更新",
    "更新手続き",
    "pipeline",
    "etl結果",
    "定例ミーティング",
    "ミーティングの件",
]

EXPLICIT_ACTION_REQUEST_PATTERNS = (
    r"(契約書|申込書|見積書)\s*(?:の)?\s*(準備|送付|再送|返送|提出|確認)\s*(?:を)?\s*(?:お願いします|お願い致します|お願いいたします|ください)",
    r"(準備|送付|再送|返送|提出|署名|押印|記入|共有)\s*(?:を)?\s*(?:お願いします|お願い致します|お願いいたします|ください)",
)

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

EXPLICIT_REPLY_PATTERNS = (
    r"(ご返信|返信|ご返答|返答|ご回答|回答)\s*(?:を)?\s*(?:お願いします|ください|願います|お願いいたします|いただけますか|頂けますか)",
    r"(返信|返答|回答)\s*(?:期日|期限|締切|締め切り|by)\b",
    r"(please|kindly)\s+reply\b",
    r"reply\s+(?:requested|required|needed)\b",
    r"respond\s+(?:by|required|needed)\b",
)

PROMO_REPLY_SUPPRESS_HINTS = (
    "クーポン",
    "coupon",
    "プレゼント",
    "gift card",
    "ギフトカード",
    "資金調達",
    "セミナー",
    "webinar",
    "キャンペーン",
    "event",
    "イベント",
)

CHATWORK_MENTION_HINTS = (
    "メンション",
    "mention",
    "[to:",
    "to you",
    "あなた宛",
)

RELATED_DOMAINS = {
    "line.me": "line",
    "linecorp.com": "line",
    "linebiz.com": "line",
    "autoro.io": "autoro",
    "notion.so": "notion",
}

PROMO_SENDER_DOMAINS = [
    "ma.accordiagolf.com",
    "toridori.co.jp",
    "diggle.team",
    "innovation.co.jp",
    "sales-skygroup.jp",
    "billage.space",
    "one-stream.jp",
    "mapbox.com",
    "necfru.com",
    "facebookmail.com",
    "mail.instagram.com",
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
    env_path = Path(env.get("ROBY_ENV_FILE", str(ENV_PATH))).expanduser()
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


def load_contact_index(path: Path | None = None) -> Dict[str, Any]:
    target = (path or CONTACT_INDEX_PATH).expanduser()
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def build_context_sender_hints(seed: Dict[str, Any] | None) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    sender_hints: Dict[str, Dict[str, Any]] = {}
    domain_hints: Dict[str, Dict[str, Any]] = {}
    if not isinstance(seed, dict):
        return sender_hints, domain_hints
    for row in ((seed.get("email") or {}).get("important_senders") or []):
        if not isinstance(row, dict):
            continue
        importance = str(row.get("importance") or "").strip().lower()
        name = str(row.get("name") or "").strip()
        company = str(row.get("company") or "").strip()
        topics = str(row.get("topics") or "").strip()
        emails = [str(x).strip().lower() for x in (row.get("emails") or []) if str(x).strip()]
        domains = [str(x).strip().lower() for x in (row.get("domains") or []) if str(x).strip()]
        payload = {
            "name": name,
            "company": company,
            "importance": importance,
            "topics": topics,
        }
        for email in emails:
            sender_hints[email] = payload
        for domain in domains:
            domain_hints.setdefault(domain, payload)
    return sender_hints, domain_hints


def contact_importance(
    thread_id: str,
    sender: str,
    index: Dict[str, Any] | None,
    *,
    context_sender_hints: Dict[str, Dict[str, Any]] | None = None,
    context_domain_hints: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    info = {
        "known": False,
        "thread_replied": False,
        "sender_email": "",
        "sender_domain": "",
        "sender_thread_count": 0,
        "domain_thread_count": 0,
        "tier": "none",
        "score": 0,
        "context_seed": False,
    }
    _sender_name, sender_email = parseaddr(sender or "")
    sender_email = (sender_email or "").strip().lower()
    sender_domain = sender_email.split("@", 1)[1] if "@" in sender_email else ""
    thread_index = (index or {}).get("thread_index") or {}
    sender_index = (index or {}).get("sender_index") or {}
    domain_index = (index or {}).get("domain_index") or {}
    thread_info = thread_index.get((thread_id or "").strip())
    sender_info = sender_index.get(sender_email, {})
    domain_info = domain_index.get(sender_domain, {})

    info["sender_email"] = sender_email
    info["sender_domain"] = sender_domain
    info["thread_replied"] = bool(thread_info)
    info["sender_thread_count"] = int(sender_info.get("thread_count", 0) or 0)
    info["domain_thread_count"] = int(domain_info.get("thread_count", 0) or 0)
    info["known"] = info["thread_replied"] or info["sender_thread_count"] > 0 or info["domain_thread_count"] > 0

    score = 0
    if info["thread_replied"]:
        score += 6
    if info["sender_thread_count"] >= 6:
        score += 4
    elif info["sender_thread_count"] >= 3:
        score += 3
    elif info["sender_thread_count"] >= 1:
        score += 2
    if info["domain_thread_count"] >= 12:
        score += 3
    elif info["domain_thread_count"] >= 6:
        score += 2
    elif info["domain_thread_count"] >= 2:
        score += 1

    context_sender = (context_sender_hints or {}).get(sender_email, {})
    context_domain = (context_domain_hints or {}).get(sender_domain, {})
    context_match = context_sender or context_domain
    if context_match:
        info["known"] = True
        info["context_seed"] = True
        importance = str(context_match.get("importance") or "").lower()
        if importance == "高":
            score = max(score, 6)
        elif importance == "中":
            score = max(score, 4)
        elif importance == "低":
            score = max(score, 2)

    info["score"] = score
    if score >= 8:
        info["tier"] = "high"
    elif score >= 4:
        info["tier"] = "medium"
    elif score >= 2:
        info["tier"] = "low"
    return info


def apply_contact_override(
    category: str,
    tags: List[str],
    meta: Dict[str, Any],
    contact_meta: Dict[str, Any],
    *,
    is_noreply: bool,
) -> Tuple[str, List[str], Dict[str, Any]]:
    if not contact_meta.get("known"):
        return category, tags, meta
    tier = contact_meta.get("tier")
    if category == "archive":
        if contact_meta.get("thread_replied") or (tier in {"high", "medium"} and not is_noreply):
            category = "needs_review"
            tags = _dedupe_tags(tags + ["contact:override"])
            meta["contact_reason"] = "known_contact_promoted_from_archive"
    elif category == "later_check" and tier in {"high", "medium"}:
        category = "needs_review"
        tags = _dedupe_tags(tags + ["contact:override"])
        meta["contact_reason"] = "known_contact_promoted_from_later_check"
    return category, tags, meta


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
    timeout_sec = int((env.get("GMAIL_TRIAGE_ARCHIVE_TIMEOUT_SEC", "20") or "20").strip())
    subprocess.run(cmd, env=env, check=True, timeout=timeout_sec)


def send_slack(webhook_url: str, text: str) -> None:
    import urllib.request

    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def format_gmail_slack_message(msg: Dict[str, Any], category: str) -> str:
    category_label = WORK_BUCKET_LABELS.get(category, category)
    subject = str(msg.get("subject", "") or "(件名なし)").strip()
    sender = str(msg.get("from", "") or "-").strip()
    date = str(msg.get("date", "") or "-").strip()
    thread_id = str(msg.get("threadId", "") or "").strip()
    msg_url = f"https://mail.google.com/mail/u/0/#inbox/{thread_id}" if thread_id else "-"
    lines = [
        "【Roby Gmail通知】",
        f"・分類: {category_label}",
        f"・件名: {subject}",
        f"・送信者: {sender}",
        f"・日時: {date}",
        f"・リンク: {msg_url}",
    ]
    return "\n".join(lines)


def summarize_tasks(text: str, env: Dict[str, str]) -> List[Dict[str, Any]]:
    prompt = (
        "Extract actionable tasks from the message. "
        "Return ONLY a JSON array of objects with keys: title, due_date, project, note, task_kind. "
        "task_kind must be one of reply or action. "
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


GENERIC_ACTION_PREFIXES = (
    "対応:",
    "対応：",
    "タスク:",
    "タスク：",
    "要対応:",
    "要対応：",
    "ネクストアクション:",
    "ネクストアクション：",
    "アクション:",
    "アクション：",
)


def _looks_like_reply_task(title: str, note: str = "") -> bool:
    text = f"{title} {note}".lower()
    hints = ("返信", "返答", "回答", "reply", "respond", "返事", "メール返信")
    return any(h in text for h in hints)


def _rewrite_email_action_title(title: str, raw_category: str, note: str = "") -> str:
    text = (title or "").strip()
    for prefix in GENERIC_ACTION_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    text = re.sub(r"^(確認事項|対応事項|タスク候補)\s*[:：-]\s*", "", text).strip()
    if not text:
        return "返信内容を確認して返信する" if raw_category == "needs_reply" else "メール内容を確認して対応する"

    generic_only = {
        "確認",
        "確認する",
        "対応",
        "対応する",
        "返信",
        "返信する",
        "返答する",
        "回答する",
        "連絡する",
    }
    if text in generic_only:
        if raw_category == "needs_reply" or _looks_like_reply_task(text, note):
            return "返信内容を確認して返信する"
        return "メール内容を確認して対応する"
    return text


def normalize_extracted_actions(
    extracted: List[Dict[str, Any]],
    *,
    raw_category: str,
    subject: str,
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    has_reply = False

    for item in extracted:
        title = _rewrite_email_action_title(str(item.get("title") or ""), raw_category, str(item.get("note") or ""))
        note = str(item.get("note") or "").strip()
        task_kind = str(item.get("task_kind") or "").strip().lower()
        if task_kind not in {"reply", "action"}:
            task_kind = "reply" if _looks_like_reply_task(title, note) else "action"
        if task_kind == "reply":
            has_reply = True
        key = (task_kind, title)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "title": title,
                "due_date": str(item.get("due_date") or "").strip(),
                "project": str(item.get("project") or "").strip() or "email",
                "note": note,
                "task_kind": task_kind,
            }
        )

    if raw_category == "needs_reply" and not has_reply:
        reply_title = f"【返信】{subject}" if subject else "返信内容を確認して返信する"
        normalized.insert(
            0,
            {
                "title": _rewrite_email_action_title(reply_title, raw_category),
                "due_date": "",
                "project": "email",
                "note": "",
                "task_kind": "reply",
            },
        )

    return normalized


GENERIC_EMAIL_TASK_TITLES = {
    "返信内容を確認して返信する",
    "メール内容を確認して対応する",
}


def _is_specific_email_task(item: Dict[str, Any]) -> bool:
    title = str(item.get("title") or "").strip()
    if not title:
        return False
    if title in GENERIC_EMAIL_TASK_TITLES:
        return False
    return len(title) >= 8


def decide_task_gate(
    raw_category: str,
    work_bucket: str,
    extracted: List[Dict[str, Any]],
    meta: Dict[str, Any],
    tags: List[str] | None = None,
) -> Tuple[str, str, Dict[str, Any]]:
    if work_bucket != "task":
        gate = {"applied": False, "confidence": None, "reason": "not_task_bucket"}
        if isinstance(meta, dict):
            meta["task_gate"] = gate
        return work_bucket, "task_gate_not_applicable", meta

    signals = meta.get("signals") if isinstance(meta, dict) else {}
    if not isinstance(signals, dict):
        signals = {}
    bucket_scores = meta.get("bucket_scores") if isinstance(meta, dict) else {}
    if not isinstance(bucket_scores, dict):
        bucket_scores = {}
    contact_meta = meta.get("contact_importance") if isinstance(meta, dict) else {}
    if not isinstance(contact_meta, dict):
        contact_meta = {}

    confidence = 0.0
    reasons: List[str] = []
    tag_list = tags or []
    has_reply_task = any(str(item.get("task_kind") or "") == "reply" for item in extracted)
    has_specific_task = any(_is_specific_email_task(item) for item in extracted)
    has_due_date = any(str(item.get("due_date") or "").strip() for item in extracted)

    if raw_category == "needs_reply":
        confidence += 4.0
        reasons.append("raw_needs_reply")
    if has_reply_task:
        confidence += 2.0
        reasons.append("reply_task_present")
    if signals.get("meeting_coordination"):
        confidence += 4.0
        reasons.append("meeting_coordination")
    if signals.get("business_review"):
        confidence += 2.0
        reasons.append("business_review")
    if signals.get("actionable_notice"):
        confidence += 2.0
        reasons.append("actionable_notice")
    if signals.get("alert"):
        confidence += 2.0
        reasons.append("alert")
    if has_specific_task:
        confidence += 2.0
        reasons.append("specific_task")
    if has_due_date:
        confidence += 1.0
        reasons.append("due_date")
    if any(str(tag).startswith("contact:known") for tag in tag_list):
        confidence += 1.0
        reasons.append("known_contact")

    tier = str(contact_meta.get("tier") or "none")
    if contact_meta.get("thread_replied"):
        confidence += 2.0
        reasons.append("replied_thread")
    elif tier == "high":
        confidence += 1.5
        reasons.append("high_contact_tier")
    elif tier == "medium":
        confidence += 1.0
        reasons.append("medium_contact_tier")

    if float(bucket_scores.get("newsletter", 0) or 0) >= 4 and not signals.get("business_review"):
        confidence -= 3.0
        reasons.append("newsletter_risk")
    if signals.get("promo_sender_domain") and not signals.get("business_review") and not signals.get("actionable_notice") and not signals.get("alert"):
        confidence -= 3.0
        reasons.append("promo_sender_domain")
    if signals.get("is_noreply") and not signals.get("business_review") and not signals.get("actionable_notice") and not signals.get("alert"):
        confidence -= 1.0
        reasons.append("noreply_penalty")
    if extracted and not has_specific_task and raw_category != "needs_reply":
        confidence -= 2.0
        reasons.append("generic_only")

    applied = confidence >= 4.0
    reason = "high_confidence_task" if applied else "low_confidence_downgraded_to_review"
    gate = {
        "applied": applied,
        "confidence": round(confidence, 2),
        "reason": reason,
        "signals": reasons,
        "has_specific_task": has_specific_task,
        "task_count": len(extracted),
    }
    if isinstance(meta, dict):
        meta["task_gate"] = gate
    return ("task" if applied else "review"), reason, meta


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

    headers = {"Content-Type": "application/json"}
    if token:
        header_name = env.get("NEURONIC_AUTH_HEADER", "Authorization")
        headers[header_name] = f"Bearer {token}"

    def _is_payload_too_large(resp: Dict[str, Any]) -> bool:
        status = str(resp.get("status_code") or "")
        detail = f"{resp.get('error','')} {resp.get('detail','')}".lower()
        return status == "413" or "payload too large" in detail or "request entity too large" in detail

    def _post(target_url: str, batch_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload = {"items": batch_items}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(target_url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            status_code = getattr(resp, "status", 200)
            body = resp.read().decode("utf-8", "ignore")
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                parsed.setdefault("status_code", status_code)
                parsed.setdefault("endpoint_used", target_url)
                return parsed
            return {"status_code": status_code, "endpoint_used": target_url, "response": parsed}
        except Exception:
            return {"status_code": status_code, "endpoint_used": target_url, "response": body}

    def _send_once(batch_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            return _post(url, batch_items)
        except urllib.error.HTTPError as e:
            if e.code == 404 and url.endswith("/tasks/import"):
                try:
                    return _post(fallback_url, batch_items)
                except urllib.error.HTTPError as e2:
                    return {
                        "error": f"HTTP {e2.code}",
                        "status_code": e2.code,
                        "detail": e2.read().decode("utf-8", "ignore"),
                        "endpoint_used": fallback_url,
                    }
            return {
                "error": f"HTTP {e.code}",
                "status_code": e.code,
                "detail": e.read().decode("utf-8", "ignore"),
                "endpoint_used": url,
            }
        except Exception as e:
            return {"error": str(e), "endpoint_used": url}

    batch_size = int(env.get("NEURONIC_BATCH_SIZE", "100") or "100")
    if batch_size < 1:
        batch_size = 100
    queue: List[List[Dict[str, Any]]] = [payload_items[i:i + batch_size] for i in range(0, len(payload_items), batch_size)]

    aggregate: Dict[str, Any] = {
        "ok": True,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "error_count": 0,
        "errors": [],
        "endpoint_used": url,
        "fallback_used": False,
    }

    while queue:
        batch = queue.pop(0)
        resp = _send_once(batch)
        endpoint_used = str(resp.get("endpoint_used") or "")
        if endpoint_used:
            aggregate["endpoint_used"] = endpoint_used
        if endpoint_used.endswith("/tasks/bulk"):
            aggregate["fallback_used"] = True

        if resp.get("error"):
            if len(batch) > 1 and _is_payload_too_large(resp):
                mid = max(1, len(batch) // 2)
                queue.insert(0, batch[mid:])
                queue.insert(0, batch[:mid])
                continue
            aggregate["ok"] = False
            aggregate["error_count"] = int(aggregate.get("error_count", 0)) + 1
            aggregate["errors"].append({"batch_size": len(batch), "reason": resp.get("detail") or resp.get("error")})
            aggregate["detail"] = resp.get("detail") or resp.get("error")
            continue

        aggregate["created"] += int(resp.get("created", 0) or 0)
        aggregate["updated"] += int(resp.get("updated", 0) or 0)
        aggregate["skipped"] += int(resp.get("skipped", 0) or 0)
        resp_errors = resp.get("errors") or []
        if isinstance(resp_errors, list) and resp_errors:
            aggregate["errors"].extend(resp_errors)
            aggregate["error_count"] = int(aggregate.get("error_count", 0)) + len(resp_errors)
        if "hierarchy_applied" in resp:
            aggregate["hierarchy_applied"] = resp.get("hierarchy_applied")
        if "order_applied" in resp:
            aggregate["order_applied"] = resp.get("order_applied")

    return aggregate


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


def _sender_label(raw_from: str) -> str:
    display_name, address = parseaddr((raw_from or "").strip())
    label = (display_name or address or "").strip().strip("\"'")
    if not label:
        return "送信者不明"
    return re.sub(r"\s+", " ", label)[:48]


def _decorate_email_task_title(title: str, sender_label: str) -> str:
    base = (title or "").strip() or "メール確認タスク"
    return f"【{sender_label}】{base}"


def decide_work_bucket(
    category: str,
    needs_reply: bool,
    meta: Dict[str, Any],
    tags: List[str] | None = None,
) -> Tuple[str, str]:
    signals = meta.get("signals") if isinstance(meta, dict) else {}
    if not isinstance(signals, dict):
        signals = {}
    contact_meta = meta.get("contact_importance") if isinstance(meta, dict) else {}
    if not isinstance(contact_meta, dict):
        contact_meta = {}
    tag_list = tags or []
    has_tool_tag = any(str(tag).startswith("tool:") for tag in tag_list)

    newsletter_score = 0
    review_score = 0
    task_score = 0

    if signals.get("promo_subject"):
        newsletter_score += 3
    if signals.get("marketing_sender"):
        newsletter_score += 2
    if signals.get("promo_sender_domain"):
        newsletter_score += 3
    if signals.get("ad_hint"):
        newsletter_score += 1
    if signals.get("is_noreply"):
        newsletter_score += 1

    if signals.get("business_review"):
        review_score += 4
    if signals.get("actionable_notice"):
        review_score += 3
    if signals.get("alert"):
        review_score += 3
    if signals.get("urgent"):
        review_score += 1
    if has_tool_tag:
        review_score += 1

    tier = str(contact_meta.get("tier") or "none")
    if contact_meta.get("thread_replied"):
        review_score += 3
    elif tier == "high":
        review_score += 2
    elif tier == "medium":
        review_score += 1

    if needs_reply:
        task_score += 4
    if signals.get("meeting_coordination"):
        task_score += 3
    if signals.get("urgent"):
        task_score += 1
    if signals.get("actionable_notice"):
        task_score += 1
    if signals.get("explicit_action_request"):
        task_score += 4

    meta["bucket_scores"] = {
        "newsletter": newsletter_score,
        "review": review_score,
        "task": task_score,
        "has_tool_tag": has_tool_tag,
    }

    if category == "archive":
        if task_score >= 4:
            return "task", "weighted_action_override"
        if review_score >= 4:
            return "review", "weighted_review_override"
        return "archive", "promo_or_low_value"
    if category == "later_check":
        if task_score >= 4:
            return "task", "weighted_task_from_tool_notice"
        if review_score >= 3:
            return "review", "weighted_review_from_tool_notice"
        if newsletter_score >= 5 and not has_tool_tag:
            return "archive", "newsletter_low_value"
        return "digest", "tool_notice_or_digest"
    if category == "needs_reply" or needs_reply:
        return "task", "explicit_reply_or_action"

    if category == "needs_review":
        if task_score >= 3:
            return "task", "coordination_requires_followup"
        if newsletter_score >= 4 and review_score == 0:
            return "digest", "newsletter_review_downgraded"
        return "review", "human_review_needed"

    return "review", "default_review"


def _parse_jsonish_text(raw: str) -> Any:
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", s, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            return None


def _extract_summary_text(data: Dict[str, Any]) -> str:
    for k in ("summary", "output", "text", "result"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def llm_triage_decision(subject: str, sender: str, cc: str, body: str, env: Dict[str, str]) -> Tuple[Optional[str], str]:
    if (env.get("GMAIL_TRIAGE_LLM_ENABLE", "0") or "0").strip() not in {"1", "true", "yes", "on"}:
        return None, ""

    model = (env.get("GMAIL_TRIAGE_LLM_MODEL", "ollama/llama3.2:3b") or "").strip()
    if not model:
        return None, ""
    timeout_sec = int((env.get("GMAIL_TRIAGE_LLM_TIMEOUT_SEC", "45") or "45").strip())
    max_input_chars = int((env.get("GMAIL_TRIAGE_LLM_MAX_INPUT_CHARS", "5000") or "5000").strip())
    max_output_tokens = (env.get("GMAIL_TRIAGE_LLM_MAX_OUTPUT_TOKENS", "180") or "180").strip()
    length = (env.get("GMAIL_TRIAGE_LLM_LENGTH", "s") or "s").strip()

    prompt = (
        "You classify business email triage for a Japanese solo operator. "
        "Return ONLY JSON object: {\"category\":\"archive|later_check|needs_review|needs_reply\",\"reason\":\"short\"}. "
        "Rules: "
        "archive for obvious promo/newsletter/seminar announcements. "
        "needs_review for internal coordination, ops notices, and anything involving decisions. "
        "needs_reply only when explicit response/action is requested from recipient. "
        "later_check for tool/platform notices worth checking later. "
        "Be conservative with needs_reply."
    )
    body_trim = (body or "")[:max_input_chars]
    source_text = (
        f"Subject: {subject}\n"
        f"From: {sender}\n"
        f"CC: {cc}\n\n"
        f"Body:\n{body_trim}\n"
    )
    cmd = [
        "summarize", "-",
        "--json", "--plain",
        "--metrics", "off",
        "--model", model,
        "--length", length,
        "--force-summary",
        "--prompt", prompt,
        "--max-output-tokens", max_output_tokens,
    ]
    try:
        out = subprocess.check_output(cmd, input=source_text.encode("utf-8"), env=env, timeout=timeout_sec)
        data = json.loads(out)
        raw = _extract_summary_text(data)
        parsed = _parse_jsonish_text(raw)
        if isinstance(parsed, dict):
            cat = str(parsed.get("category") or "").strip()
            reason = str(parsed.get("reason") or "").strip()
            if cat in {"archive", "later_check", "needs_review", "needs_reply"}:
                return cat, reason
    except Exception:
        return None, ""
    return None, ""


def local_preclassify_email(
    subject: str,
    sender: str,
    cc: str,
    body: str,
    env: Dict[str, str],
) -> Tuple[Optional[str], str, Dict[str, Any]]:
    if not shared_env_flag(env, "GMAIL_TRIAGE_LOCAL_PRECLASSIFY_ENABLE", False):
        return None, "", {"enabled": False, "reason": "disabled"}

    model = (env.get("GMAIL_TRIAGE_LOCAL_PRECLASSIFY_MODEL", "ollama/llama3.2:3b") or "").strip()
    if not model:
        return None, "", {"enabled": False, "reason": "missing_model"}

    body_trim = (body or "")[: shared_int_from_env(env, "GMAIL_TRIAGE_LOCAL_PRECLASSIFY_MAX_INPUT_CHARS", 3500)]
    prompt = (
        "You do a local-first preclassification pass for Japanese business email triage. "
        "Return ONLY JSON object with keys: category, reason, confidence, signals. "
        "category must be one of archive, later_check, needs_review, needs_reply. "
        "Use archive only for clear newsletters, promotions, webinar notices, or auto-generated low-value alerts. "
        "Use later_check for tool/platform notices worth checking later. "
        "Use needs_review for important notices, billing, contracts, approvals, failures, internal coordination, or anything that should stay visible. "
        "Use needs_reply only when the recipient is explicitly expected to respond or take immediate action. "
        "Be conservative: when uncertain, prefer needs_review over archive."
    )
    source_text = (
        f"Subject: {subject}\n"
        f"From: {sender}\n"
        f"CC: {cc}\n\n"
        f"Body:\n{body_trim}\n"
    )
    parsed, meta = run_ollama_json(
        prompt=prompt,
        source_text=source_text,
        env=env,
        model=model,
        timeout_sec=shared_int_from_env(env, "GMAIL_TRIAGE_LOCAL_PRECLASSIFY_TIMEOUT_SEC", 30),
        num_predict=shared_int_from_env(env, "GMAIL_TRIAGE_LOCAL_PRECLASSIFY_NUM_PREDICT", 220),
        temperature=0.1,
        top_p=0.9,
        repeat_penalty=1.03,
    )
    if not isinstance(parsed, dict):
        return None, "", meta
    category = str(parsed.get("category") or "").strip()
    if category not in {"archive", "later_check", "needs_review", "needs_reply"}:
        return None, "", {**meta, "error": "invalid_category"}
    reason = str(parsed.get("reason") or "").strip()
    return category, reason, meta


def should_apply_llm_override(
    current_category: str,
    llm_category: str,
    sender: str,
    subject: str,
) -> bool:
    if llm_category == current_category:
        return False
    sender_lower = (sender or "").lower()
    subject_lower = (subject or "").lower()

    # Avoid downgrading explicit reply-required mail to archive.
    if current_category == "needs_reply" and llm_category == "archive":
        return False

    # Archive override only when strong promotional signals exist.
    if llm_category == "archive":
        is_noreply = ("no-reply" in sender_lower) or ("noreply" in sender_lower)
        promo_domain = any(dom in sender_lower for dom in PROMO_SENDER_DOMAINS)
        promo_subject = any(h.lower() in subject_lower for h in PROMO_SUBJECT_HINTS)
        if not (is_noreply or promo_domain or promo_subject):
            return False
        return True

    return True


def should_apply_local_override(
    current_category: str,
    local_category: str,
    sender: str,
    subject: str,
) -> bool:
    if local_category == current_category:
        return False
    sender_lower = (sender or "").lower()
    subject_lower = (subject or "").lower()

    if local_category == "archive":
        is_noreply = ("no-reply" in sender_lower) or ("noreply" in sender_lower)
        promo_domain = any(dom in sender_lower for dom in PROMO_SENDER_DOMAINS)
        promo_subject = any(h.lower() in subject_lower for h in PROMO_SUBJECT_HINTS)
        has_business_keyword = any(k.lower() in subject_lower for k in BUSINESS_REVIEW_KEYWORDS)
        if has_business_keyword:
            return False
        return is_noreply or promo_domain or promo_subject

    if current_category == "archive" and local_category in {"needs_review", "needs_reply"}:
        return True
    if current_category == "later_check" and local_category in {"needs_review", "needs_reply"}:
        return True
    if current_category == "needs_review" and local_category == "needs_reply":
        return True
    return False


def classify_message(
    subject: str,
    sender: str,
    body: str,
    rules: Dict[str, Any] | None = None,
    cc: str = "",
    env: Dict[str, str] | None = None,
    thread_id: str = "",
    contact_index: Dict[str, Any] | None = None,
    context_sender_hints: Dict[str, Dict[str, Any]] | None = None,
    context_domain_hints: Dict[str, Dict[str, Any]] | None = None,
) -> Tuple[str, List[str], bool, str | None, Dict[str, Any]]:
    text = f"{subject} {sender} {cc} {body}".lower()
    header_text = f"{subject} {sender} {cc}".lower()
    tags = []
    needs_reply = False
    meta: Dict[str, Any] = {}
    sender_lower = (sender or "").lower()
    cc_lower = (cc or "").lower()
    subject_lower = (subject or "").lower()
    is_noreply = "no-reply" in sender_lower or "noreply" in sender_lower
    contact_meta = contact_importance(
        thread_id,
        sender,
        contact_index,
        context_sender_hints=context_sender_hints,
        context_domain_hints=context_domain_hints,
    )
    meta["contact_importance"] = contact_meta.copy()
    if contact_meta.get("known"):
        tags.append("contact:known")
        tier = str(contact_meta.get("tier") or "none")
        if tier and tier != "none":
            tags.append(f"contact:tier:{tier}")
        if contact_meta.get("thread_replied"):
            tags.append("contact:replied_thread")

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

    is_calendar_response = subject_lower.startswith(("承諾:", "辞退:", "accepted:", "declined:"))
    is_pipeline_success = ("[aws pipeline]" in subject_lower and "成功" in subject_lower and "etl結果" in subject_lower)
    is_tokiwagi_base_info = (
        "tokiwagi-base" in subject_lower
        and any(
            hint in subject_lower
            for hint in (
                "最新版ではありません",
                "新しいログイン動作を検知しました",
                "synology nas への新しいログイン",
            )
        )
    )
    is_internal_instagram_recap = (
        "instagram" in sender_lower
        and "info@tokiwa-gi.com" in sender_lower
        and any(hint in subject_lower for hint in ("チェックしよう", "見逃したコンテンツ", "フィードで"))
    )
    is_chatwork_mail = "chatwork" in sender_lower or "ns.chatwork.com" in sender_lower
    is_chatwork_mention = is_chatwork_mail and any(hint in text for hint in CHATWORK_MENTION_HINTS)

    if is_calendar_response:
        return "archive", _dedupe_tags(tags + ["rule:calendar_response"]), False, "calendar_response", meta
    if is_pipeline_success:
        return "archive", _dedupe_tags(tags + ["rule:pipeline_success_archive"]), False, "pipeline_success_archive", meta
    if is_tokiwagi_base_info:
        return "archive", _dedupe_tags(tags + ["rule:tokiwagi_base_info_archive"]), False, "tokiwagi_base_info_archive", meta
    if is_internal_instagram_recap:
        return "archive", _dedupe_tags(tags + ["rule:internal_instagram_recap_archive"]), False, "internal_instagram_recap_archive", meta
    if is_chatwork_mail and not is_chatwork_mention:
        return "archive", _dedupe_tags(tags + ["rule:chatwork_non_mention_archive"]), False, "chatwork_non_mention_archive", meta

    override_category, override_rule = match_user_override(subject, sender, rules or {}, cc=cc)
    if override_category:
        return override_category, _dedupe_tags(tags + [f"rule:{override_rule}"]), (override_category == "needs_reply"), override_rule, meta

    # Internal company domain in sender/CC should always be reviewed.
    if "tokiwa-gi.com" in sender_lower or "tokiwa-gi.com" in cc_lower:
        return "needs_review", _dedupe_tags(tags + ["rule:internal_domain_review"]), needs_reply, "internal_domain_review", meta

    urgent = any(k in text for k in IMPORTANT_KEYWORDS)
    is_alert = any(k in text for k in ALERT_HINTS)
    is_ad_hint = any(h in text for h in AD_HINTS)
    is_promo_subject = any(h.lower() in subject_lower for h in PROMO_SUBJECT_HINTS)
    is_actionable_notice = any(h.lower() in text for h in ACTIONABLE_NOTICE_HINTS)
    has_business_review_signal = any(k in text for k in BUSINESS_REVIEW_KEYWORDS)
    meeting_coordination = any(k in (subject or "") for k in ["定例ミーティング", "ミーティングの件", "打ち合わせ", "日程"])

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
    meta["signals"] = {
        "urgent": urgent,
        "alert": is_alert,
        "promo_subject": is_promo_subject,
        "ad_hint": is_ad_hint,
        "actionable_notice": is_actionable_notice,
        "business_review": has_business_review_signal,
        "marketing_sender": is_marketing_sender,
        "promo_sender_domain": is_promo_sender_domain,
        "meeting_coordination": meeting_coordination,
        "is_noreply": is_noreply,
    }

    # Sender-domain blacklist is authoritative for known promotional sources.
    # Their bodies often contain words like "更新", "確認", "reply-to" that trigger false positives.
    if is_promo_sender_domain:
        if has_business_review_signal or is_actionable_notice or is_alert:
            category = "needs_review"
        else:
            category = "archive"
            needs_reply = False
        if env:
            local_category, local_reason, local_meta = local_preclassify_email(subject, sender, cc, body, env)
            meta["local_preclassify"] = {**local_meta, "category": local_category, "reason": local_reason}
            if local_category and should_apply_local_override(category, local_category, sender, subject):
                category = local_category
                tags = _dedupe_tags(tags + ["local:override"])
                if local_category == "needs_reply":
                    needs_reply = True
                if local_reason:
                    meta["local_reason"] = local_reason
        category, tags, meta = apply_contact_override(category, tags, meta, contact_meta, is_noreply=is_noreply)
        return category, tags, needs_reply, None, meta

    # Tool-specific operational notifications we still want to see.
    if ("support@crmstyle.com" in sender_lower or "synergy" in text) and "アカウント発行" in (subject or ""):
        category, tags, meta = apply_contact_override("needs_review", tags, meta, contact_meta, is_noreply=is_noreply)
        return category, tags, needs_reply, None, meta

    # AWS / batch job notifications are operationally important even on success.
    if "aws" in text and ("pipeline" in text or "etl" in text):
        category, tags, meta = apply_contact_override("needs_review", tags, meta, contact_meta, is_noreply=is_noreply)
        return category, tags, needs_reply, None, meta

    # Meeting / coordination mails should remain visible.
    if meeting_coordination:
        category, tags, meta = apply_contact_override(("needs_reply" if needs_reply else "needs_review"), tags, meta, contact_meta, is_noreply=is_noreply)
        return category, tags, needs_reply, None, meta

    # Frequent ad-platform auto notices (approval/budget consumed/news) are noisy by default.
    if ("line.me" in sender_lower or "mail.yahoo.co.jp" in sender_lower) and is_noreply:
        if has_business_review_signal or is_actionable_notice or is_alert:
            category, tags, meta = apply_contact_override("needs_review", tags, meta, contact_meta, is_noreply=is_noreply)
            return category, tags, needs_reply, None, meta
        if any(k in (subject or "") for k in ["広告が承認されました", "広告アカウントが承認されました", "予算が消化されました"]):
            category, tags, meta = apply_contact_override("archive", tags, meta, contact_meta, is_noreply=is_noreply)
            return category, tags, needs_reply, None, meta
        if "ads update" in subject_lower or "新着情報" in (subject or ""):
            category, tags, meta = apply_contact_override("archive", tags, meta, contact_meta, is_noreply=is_noreply)
            return category, tags, needs_reply, None, meta

    # Strong promotional signals override reply heuristics to reduce false positives.
    if (is_promo_subject or (is_ad_hint and is_marketing_sender)) and not is_alert and not is_actionable_notice and not has_business_review_signal:
        category = "archive"
        if env:
            local_category, local_reason, local_meta = local_preclassify_email(subject, sender, cc, body, env)
            meta["local_preclassify"] = {**local_meta, "category": local_category, "reason": local_reason}
            if local_category and should_apply_local_override(category, local_category, sender, subject):
                category = local_category
                tags = _dedupe_tags(tags + ["local:override"])
                if local_category == "needs_reply":
                    needs_reply = True
                if local_reason:
                    meta["local_reason"] = local_reason
        category, tags, meta = apply_contact_override(category, tags, meta, contact_meta, is_noreply=is_noreply)
        return category, tags, needs_reply, None, meta

    reply_text = re.sub(r"reply-to", " ", text)
    has_reply_phrase = any(re.search(pattern, reply_text) for pattern in EXPLICIT_REPLY_PATTERNS)
    has_explicit_action_request = any(re.search(pattern, reply_text) for pattern in EXPLICIT_ACTION_REQUEST_PATTERNS)
    promo_reply_risk = any(h.lower() in reply_text for h in PROMO_REPLY_SUPPRESS_HINTS)
    if (
        (not is_noreply)
        and has_reply_phrase
        and not (
            promo_reply_risk
            and not urgent
            and not is_actionable_notice
            and not has_business_review_signal
            and not is_alert
            and not contact_meta.get("known")
        )
    ):
        needs_reply = True
    if has_explicit_action_request:
        meta["signals"]["explicit_action_request"] = True

    if related:
        if is_noreply:
            if is_alert:
                category = "needs_review"
            else:
                category = "later_check"
            if env:
                local_category, local_reason, local_meta = local_preclassify_email(subject, sender, cc, body, env)
                meta["local_preclassify"] = {**local_meta, "category": local_category, "reason": local_reason}
                if local_category and should_apply_local_override(category, local_category, sender, subject):
                    category = local_category
                    tags = _dedupe_tags(tags + ["local:override"])
                    if local_category == "needs_reply":
                        needs_reply = True
                    if local_reason:
                        meta["local_reason"] = local_reason
            return category, tags, needs_reply, None, meta
        if urgent:
            category = "needs_reply" if needs_reply else "needs_review"
        else:
            category = "needs_reply" if needs_reply else "later_check"
        if env:
            local_category, local_reason, local_meta = local_preclassify_email(subject, sender, cc, body, env)
            meta["local_preclassify"] = {**local_meta, "category": local_category, "reason": local_reason}
            if local_category and should_apply_local_override(category, local_category, sender, subject):
                category = local_category
                tags = _dedupe_tags(tags + ["local:override"])
                if local_category == "needs_reply":
                    needs_reply = True
                if local_reason:
                    meta["local_reason"] = local_reason
        category, tags, meta = apply_contact_override(category, tags, meta, contact_meta, is_noreply=is_noreply)
        return category, tags, needs_reply, None, meta

    if urgent:
        category = "needs_reply" if needs_reply else "needs_review"
    elif is_ad_hint and is_noreply:
        category = "archive"
    else:
        category = "needs_review"

    if env:
        local_category, local_reason, local_meta = local_preclassify_email(subject, sender, cc, body, env)
        meta["local_preclassify"] = {**local_meta, "category": local_category, "reason": local_reason}
        if local_category and should_apply_local_override(category, local_category, sender, subject):
            category = local_category
            tags = _dedupe_tags(tags + ["local:override"])
            if local_category == "needs_reply":
                needs_reply = True
            if local_reason:
                meta["local_reason"] = local_reason

    category, tags, meta = apply_contact_override(category, tags, meta, contact_meta, is_noreply=is_noreply)
    return category, _dedupe_tags(tags), needs_reply, None, meta


def build_tasks(
    extracted: List[Dict[str, Any]],
    msg: Dict[str, Any],
    category: str,
    tags: List[str],
    run_id: str,
    *,
    raw_category: str = "",
) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    base_tags = ["source:gmail", f"category:{category}"] + tags
    assignee = "私"
    msg_subject = (msg.get("subject") or "").strip()
    sender_label = _sender_label(msg.get("from", ""))
    msg_thread_id = (msg.get("threadId") or "").strip()
    msg_id = (msg.get("id") or "").strip()
    msg_url = f"https://mail.google.com/mail/u/0/#inbox/{msg_thread_id}"

    parent_task = {
        "title": _decorate_email_task_title(
            f"メール確認: {msg_subject}" if msg_subject else "メール確認タスク",
            sender_label,
        ),
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
        "priority": 1 if category == "task" else 0,
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
        task_kind = str(item.get("task_kind") or "").strip().lower()
        if task_kind not in {"reply", "action"}:
            task_kind = "reply" if _looks_like_reply_task(title, note) else "action"
        task_type_tag = "task_type:reply" if task_kind == "reply" else "task_type:action"
        item_tags = _dedupe_tags(base_tags + [f"project:{project}", f"assignee:{assignee}", task_type_tag])
        note_prefix = "返信対応" if task_kind == "reply" else "実行タスク"
        note = (
            (note + "\n\n" if note else "")
            + f"Task Type: {note_prefix}\n"
            + f"Parent: {parent_task['title']}\n"
            + f"Email: {msg_subject}\n"
            + f"From: {msg.get('from','')}\n"
            + f"Date: {msg.get('date','')}\n"
            + f"Link: {msg_url}"
        )
        task = {
            "title": _decorate_email_task_title(_rewrite_email_action_title(title, raw_category, note), sender_label),
            "project": project,
            "due_date": due,
            "assignee": assignee,
            "note": note,
            "source": "roby",
            "status": "inbox",
            "priority": 1 if category == "task" else 0,
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


def cap_extracted_actions(extracted: List[Dict[str, Any]], max_actions: int) -> List[Dict[str, Any]]:
    if max_actions <= 0:
        return extracted
    return extracted[:max_actions]


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
    contact_index = load_contact_index(Path(env.get("ROBY_GMAIL_CONTACT_INDEX_PATH", str(CONTACT_INDEX_PATH))))
    context_sender_hints, context_domain_hints = build_context_sender_hints(load_context_seed())
    state = ensure_state()
    processed = state.get("processed", {})

    messages = gog_search(args.account, args.query, args.max, env)
    summary = {
        "total": len(messages),
        "new": 0,
        "archived": 0,
        "notified": 0,
        "notify_suppressed": 0,
        "tasks": 0,
        "task_actions_capped": 0,
        "task_run_cap_reached": False,
        "neuronic_errors": 0,
        "llm_reviewed": 0,
        "llm_overrides": 0,
        "local_preclassified": 0,
        "local_overrides": 0,
        "task_gate_downgraded": 0,
    }
    run_id = build_run_id("gmail")
    summary["run_id"] = run_id
    last_neuronic_error: str | None = None
    category_counts: Dict[str, int] = {}
    raw_category_counts: Dict[str, int] = {}
    task_gate_reason_counts: Dict[str, int] = {}

    skip_tasks = args.skip_tasks or args.dry_run
    llm_enabled = (env.get("GMAIL_TRIAGE_LLM_ENABLE", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
    llm_max_reviews = int((env.get("GMAIL_TRIAGE_LLM_MAX_REVIEWS", "0") or "0").strip())
    llm_review_count = 0
    notify_max_per_run = int((env.get("GMAIL_TRIAGE_NOTIFY_MAX_PER_RUN", "12") or "12").strip())
    task_actions_max_per_mail = int((env.get("GMAIL_TRIAGE_TASK_MAX_ACTIONS_PER_MAIL", "6") or "6").strip())
    task_items_max_per_run = int((env.get("GMAIL_TRIAGE_TASK_MAX_ITEMS_PER_RUN", "120") or "120").strip())

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
        category, tags, needs_reply, rule_applied, classify_meta = classify_message(
            subject,
            sender,
            body,
            rules=rules,
            cc=cc,
            env=env,
            thread_id=str(msg.get("threadId", "") or ""),
            contact_index=contact_index,
            context_sender_hints=context_sender_hints,
            context_domain_hints=context_domain_hints,
        )
        local_meta = classify_meta.get("local_preclassify") if isinstance(classify_meta, dict) else None
        if isinstance(local_meta, dict) and local_meta.get("enabled") is not False:
            summary["local_preclassified"] += 1
        if "local:override" in tags:
            summary["local_overrides"] += 1
        llm_category = None
        llm_reason = ""
        if llm_enabled and llm_max_reviews > 0 and llm_review_count < llm_max_reviews and not rule_applied and category in ("needs_review", "later_check", "needs_reply"):
            llm_category, llm_reason = llm_triage_decision(subject, sender, cc, body, env)
            llm_review_count += 1
            summary["llm_reviewed"] += 1
            if llm_category and should_apply_llm_override(category, llm_category, sender, subject):
                category = llm_category
                summary["llm_overrides"] += 1
                tags = _dedupe_tags(tags + ["llm:override"])
        work_bucket, bucket_reason = decide_work_bucket(category, needs_reply, classify_meta, tags)
        classify_meta["work_bucket"] = work_bucket
        classify_meta["work_bucket_reason"] = bucket_reason
        raw_category_counts[category] = raw_category_counts.get(category, 0) + 1
        summary["new"] += 1

        # Task extraction
        tasks = []
        if (not skip_tasks) and work_bucket == "task":
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
                    fallback_title = f"【返信】{subject}" if subject else "返信内容を確認して返信する"
                else:
                    fallback_title = f"メール対応: {subject}"
                extracted = [{"title": fallback_title, "due_date": "", "project": "email", "note": "", "task_kind": ("reply" if category == "needs_reply" else "action")}]
            extracted = normalize_extracted_actions(extracted, raw_category=category, subject=subject)
            before_cap = len(extracted)
            extracted = cap_extracted_actions(extracted, task_actions_max_per_mail)
            if len(extracted) < before_cap:
                summary["task_actions_capped"] += (before_cap - len(extracted))
            final_bucket, gate_reason, classify_meta = decide_task_gate(category, work_bucket, extracted, classify_meta, tags)
            classify_meta["final_bucket"] = final_bucket
            classify_meta["task_gate_reason"] = gate_reason
            task_gate_reason_counts[gate_reason] = task_gate_reason_counts.get(gate_reason, 0) + 1
            if final_bucket != work_bucket:
                summary["task_gate_downgraded"] += 1
            work_bucket = final_bucket

            if work_bucket == "task":
                tasks = build_tasks(extracted, msg, work_bucket, tags, run_id=run_id, raw_category=category)
                if task_items_max_per_run > 0:
                    remaining = task_items_max_per_run - int(summary.get("tasks", 0))
                    if remaining <= 0:
                        summary["task_run_cap_reached"] = True
                        tasks = []
                    elif len(tasks) > remaining:
                        tasks = tasks[:remaining]
                        summary["task_run_cap_reached"] = True
                if tasks and not args.dry_run:
                    write_feedback_manifest(tasks, run_id)
                    resp = send_neuronic(tasks, env)
                    if isinstance(resp, dict) and (resp.get("error") or resp.get("ok") is False or int(resp.get("error_count", 0) or 0) > 0):
                        summary["neuronic_errors"] += 1
                        last_neuronic_error = resp.get("detail") or resp.get("error")
                    else:
                        summary["tasks"] += len(tasks)
                        summary["neuronic_created"] = int(summary.get("neuronic_created", 0)) + int(resp.get("created", 0) or 0)
                        summary["neuronic_updated"] = int(summary.get("neuronic_updated", 0)) + int(resp.get("updated", 0) or 0)
                        summary["neuronic_skipped"] = int(summary.get("neuronic_skipped", 0)) + int(resp.get("skipped", 0) or 0)
                        if "hierarchy_applied" in resp:
                            summary["hierarchy_applied"] = resp.get("hierarchy_applied")
                        if "order_applied" in resp:
                            summary["order_applied"] = resp.get("order_applied")

        category_counts[work_bucket] = category_counts.get(work_bucket, 0) + 1

        # Slack notify
        slack_url = env.get("SLACK_WEBHOOK_URL")
        if slack_url and not args.dry_run and work_bucket in ("task", "review", "digest"):
            text = format_gmail_slack_message(msg, work_bucket)
            if notify_max_per_run <= 0 or summary["notified"] < notify_max_per_run:
                send_slack(slack_url, text)
                summary["notified"] += 1
            else:
                summary["notify_suppressed"] += 1

        # Archive ads
        if work_bucket == "archive" and args.archive_ads and not args.dry_run:
            try:
                archive_thread(args.account, msg.get("threadId", ""), env)
                summary["archived"] += 1
            except Exception:
                pass

        if args.verbose:
            row = {
                "id": msg_id,
                "category": work_bucket,
                "raw_category": category,
                "subject": subject,
                "from": sender,
                "cc": cc,
                "tags": tags,
            }
            if rule_applied:
                row["rule"] = rule_applied
            if llm_category:
                row["llm_category"] = llm_category
            if llm_reason:
                row["llm_reason"] = llm_reason
            if isinstance(classify_meta, dict) and classify_meta.get("local_reason"):
                row["local_reason"] = classify_meta.get("local_reason")
            row["bucket_reason"] = bucket_reason
            if isinstance(classify_meta, dict):
                row["task_gate_reason"] = classify_meta.get("task_gate_reason")
                row["task_gate"] = classify_meta.get("task_gate")
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
    summary["raw_categories"] = raw_category_counts
    summary["task_gate_reasons"] = task_gate_reason_counts
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

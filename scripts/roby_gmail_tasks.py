#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from email.utils import parseaddr
from typing import Any, Dict, List, Tuple


def _dedupe_tags(tags: List[str]) -> List[str]:
    seen = set()
    out = []
    for tag in tags:
        if not tag:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def summarize_tasks(text: str, env: Dict[str, str]) -> List[Dict[str, Any]]:
    prompt = (
        "Extract actionable tasks from the message. "
        "Return ONLY a JSON array of objects with keys: title, due_date, project, note, task_kind. "
        "task_kind must be one of reply or action. "
        "due_date must be YYYY-MM-DD or empty string. "
        "Use concise, executable Japanese titles. "
        "A task must have a clear next action, an owner, a cost if ignored, and a checkable notion of done. "
        "Do not output generic titles like '対応' or '確認'. "
        "Do not output pure status reports, completed work, or commentary as tasks. "
        "If the mail asks for a concrete deliverable, include that deliverable in title. "
        "Split sequential actions into multiple tasks when useful. "
        "If no tasks, return []."
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
    try:
        parsed = json.loads(summary)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        m = re.search(r"\[.*\]", summary, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return []
        return []


def extract_explicit_email_actions(
    subject: str,
    body: str,
    *,
    raw_category: str,
    meta: Dict[str, Any] | None = None,
    tags: List[str] | None = None,
) -> List[Dict[str, Any]]:
    text = f"{subject}\n{body}"
    actions: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add_action(title: str, *, task_kind: str = "action", note: str = "") -> None:
        normalized = title.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        actions.append(
            {
                "title": normalized,
                "due_date": "",
                "project": "email",
                "note": note,
                "task_kind": task_kind,
            }
        )

    if raw_category == "needs_reply":
        add_action(f"【返信】{subject}" if subject else "返信内容を確認して返信する", task_kind="reply")

    doc_patterns = [
        ("契約書", "準備", "契約書を準備する"),
        ("契約書", "送付", "契約書を送付する"),
        ("契約書", "提出", "契約書を提出する"),
        ("見積書", "送付", "見積書を送付する"),
        ("見積書", "再送", "見積書を再送する"),
        ("申込書", "提出", "申込書を提出する"),
        ("申込書", "記入", "申込書を記入する"),
    ]
    for noun, verb, title in doc_patterns:
        if noun in text and verb in text:
            add_action(title)

    if (meta or {}).get("signals", {}).get("contract_followup_subject"):
        if not actions:
            add_action("契約内容を確認して対応する")

    tag_list = tags or []
    if "tool:autoro" in tag_list and (meta or {}).get("signals", {}).get("alert"):
        add_action("AUTOROのエラー内容を確認する")

    if not actions:
        if "確認" in text and ("お願い" in text or "ください" in text):
            add_action("依頼内容を確認して対応する")

    return actions


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
    has_autoro_tag = any(str(tag) == "tool:autoro" for tag in tag_list)

    if raw_category == "needs_reply":
        confidence += 4.0
        reasons.append("raw_needs_reply")
    if has_reply_task:
        confidence += 2.0
        reasons.append("reply_task_present")
    if signals.get("meeting_coordination"):
        confidence += 4.0
        reasons.append("meeting_coordination")
    if signals.get("review_only_notice") and raw_category != "needs_reply" and not signals.get("explicit_action_request"):
        confidence -= 4.0
        reasons.append("review_only_notice")
    if signals.get("business_review"):
        confidence += 2.0
        reasons.append("business_review")
    if signals.get("actionable_notice"):
        confidence += 2.0
        reasons.append("actionable_notice")
    if signals.get("explicit_action_request"):
        confidence += 4.0
        reasons.append("explicit_action_request")
    if signals.get("contract_followup_subject"):
        confidence += 4.0
        reasons.append("contract_followup_subject")
    if signals.get("alert"):
        confidence += 2.0
        reasons.append("alert")
    if has_autoro_tag and (signals.get("alert") or signals.get("actionable_notice")):
        confidence += 3.0
        reasons.append("autoro_operational_notice")
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
    if signals.get("promo_reply_risk") and not signals.get("business_review") and not signals.get("actionable_notice") and not signals.get("alert"):
        confidence -= 3.0
        reasons.append("promo_reply_risk")
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


def _sender_label(raw_from: str) -> str:
    display_name, address = parseaddr((raw_from or "").strip())
    label = (display_name or address or "").strip().strip("\"'")
    if not label:
        return "送信者不明"
    return re.sub(r"\s+", " ", label)[:48]


def _decorate_email_task_title(title: str, sender_label: str) -> str:
    base = (title or "").strip() or "メール確認タスク"
    return f"【{sender_label}】{base}"


def _clean_email_subject(subject: str) -> str:
    text = (subject or "").strip()
    while True:
        cleaned = re.sub(r"^(?:re|fw|fwd)\s*[:：]\s*", "", text, flags=re.IGNORECASE).strip()
        if cleaned == text:
            return cleaned or text
        text = cleaned


def _display_email_action_title(title: str, raw_category: str, note: str, subject: str) -> str:
    rewritten = _rewrite_email_action_title(title, raw_category, note)
    if _looks_like_reply_task(rewritten, note):
        reply_prefix = "【返信】"
        body = rewritten
        if body.startswith(reply_prefix):
            body = body[len(reply_prefix):].strip()
        if not body or body in {"返信内容を確認して返信する", "返信内容を確認する"}:
            body = subject or "返信内容を確認する"
        body = _clean_email_subject(body)
        return f"{reply_prefix}{body}" if body else "返信内容を確認して返信する"
    return rewritten


def _email_task_note(
    *,
    note: str,
    task_kind: str,
    msg_subject: str,
    msg: Dict[str, Any],
    msg_url: str,
    parent_title: str = "",
) -> str:
    note_prefix = "返信対応" if task_kind == "reply" else "実行タスク"
    lines = []
    if note:
        lines.extend([note, ""])
    lines.append(f"Task Type: {note_prefix}")
    if parent_title:
        lines.append(f"Parent: {parent_title}")
    lines.extend(
        [
            f"Email: {msg_subject}",
            f"From: {msg.get('from','')}",
            f"Date: {msg.get('date','')}",
            f"Link: {msg_url}",
        ]
    )
    return "\n".join(lines)


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

    normalized_items: List[Dict[str, Any]] = []
    for item in extracted:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        note = (item.get("note") or "").strip()
        task_kind = str(item.get("task_kind") or "").strip().lower()
        if task_kind not in {"reply", "action"}:
            task_kind = "reply" if _looks_like_reply_task(title, note) else "action"
        normalized_items.append(
            {
                **item,
                "title": title,
                "note": note,
                "task_kind": task_kind,
            }
        )

    if not normalized_items:
        return []

    parent_origin = ""
    parent_title = ""
    use_parent = len(normalized_items) > 1

    if not use_parent:
        item = normalized_items[0]
        task_kind = str(item.get("task_kind") or "action")
        project = (item.get("project") or "").strip() or "email"
        due = (item.get("due_date") or "").strip()
        note = str(item.get("note") or "").strip()
        task_type_tag = "task_type:reply" if task_kind == "reply" else "task_type:action"
        display_title = _display_email_action_title(str(item.get("title") or ""), raw_category, note, msg_subject)
        task = {
            "title": _decorate_email_task_title(display_title, sender_label),
            "project": project,
            "due_date": due,
            "assignee": assignee,
            "note": _email_task_note(
                note=note,
                task_kind=task_kind,
                msg_subject=msg_subject,
                msg=msg,
                msg_url=msg_url,
            ),
            "source": "roby",
            "status": "inbox",
            "priority": 1 if category == "task" else 0,
            "tags": _dedupe_tags(base_tags + [f"project:{project}", f"assignee:{assignee}", task_type_tag]),
            "parent_origin_id": None,
            "sibling_order": 0,
            "run_id": run_id,
            "feedback_state": "pending",
            "source_doc_id": msg_id or msg_thread_id,
            "source_doc_title": msg_subject,
        }
        task["origin_id"] = _stable_origin_id(task, f"{msg_thread_id}|single|{task_kind}")
        task["external_ref"] = f"group:{task['origin_id']}"
        return [task]

    parent_task = {
        "title": _decorate_email_task_title(
            f"メール対応: {msg_subject}" if msg_subject else "メール対応タスク",
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
    parent_title = parent_task["title"]
    tasks.append(parent_task)

    for i, item in enumerate(normalized_items):
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
        task = {
            "title": _decorate_email_task_title(
                _display_email_action_title(title, raw_category, note, msg_subject),
                sender_label,
            ),
            "project": project,
            "due_date": due,
            "assignee": assignee,
            "note": _email_task_note(
                note=note,
                task_kind=task_kind,
                msg_subject=msg_subject,
                msg=msg,
                msg_url=msg_url,
                parent_title=parent_title,
            ),
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

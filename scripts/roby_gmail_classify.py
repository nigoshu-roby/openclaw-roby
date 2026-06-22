#!/usr/bin/env python3
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


MARKETING_SENDER_HINTS = [
    "seminar",
    "event",
    "marketing",
    "news",
    "mailmag",
    "メルマガ",
    "運営事務局",
]

BROADCAST_SUBJECT_HINTS = [
    "newsletter",
    "ニュースレター",
    "メルマガ",
    "通信",
    "vol.",
    "vol ",
    "ご案内",
    "お知らせ",
    "セミナー",
    "ウェビナー",
    "キャンペーン",
    "リリース予定",
]

BROADCAST_BODY_HINTS = [
    "unsubscribe",
    "配信停止",
    "メールマガジン",
    "本メールは",
    "このメールは",
    "配信専用",
]


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


def detect_related_tools(
    sender: str,
    header_text: str,
    *,
    related_tools: List[str],
    related_domains: Dict[str, str],
) -> List[str]:
    sender_lower = (sender or "").lower()
    header_lower = (header_text or "").lower()

    def tool_match(tool: str) -> bool:
        value = tool.lower()
        if re.fullmatch(r"[a-z0-9!+._-]+", value):
            # Avoid substring false-positives like "line" in "pipeline".
            return re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", header_lower) is not None
        return value in header_lower

    related = [tool for tool in related_tools if tool_match(tool)]
    if related:
        return related
    for domain, label in related_domains.items():
        if domain in sender_lower:
            return [label]
    return []


def build_email_signals(
    subject: str,
    sender: str,
    cc: str,
    body: str,
    *,
    contact_meta: Dict[str, Any] | None = None,
    matched_projects: List[Dict[str, Any]] | None = None,
    important_keywords: List[str],
    alert_hints: List[str],
    ad_hints: List[str],
    promo_subject_hints: List[str],
    actionable_notice_hints: List[str],
    business_review_keywords: List[str],
    promo_sender_domains: List[str],
) -> Dict[str, Any]:
    text = f"{subject} {sender} {cc} {body}".lower()
    sender_lower = (sender or "").lower()
    subject_lower = (subject or "").lower()
    contact = contact_meta or {}
    projects = matched_projects or []

    urgent = any(keyword in text for keyword in important_keywords)
    is_alert = any(keyword in text for keyword in alert_hints)
    is_ad_hint = any(hint in text for hint in ad_hints)
    is_promo_subject = any(hint.lower() in subject_lower for hint in promo_subject_hints)
    is_actionable_notice = any(hint.lower() in text for hint in actionable_notice_hints)
    has_business_review_signal = any(keyword in text for keyword in business_review_keywords)
    is_contract_followup_subject = (
        subject_lower.startswith("re:")
        and "契約" in subject_lower
        and contact.get("known")
    )
    meeting_coordination = any(keyword in (subject or "") for keyword in ["定例ミーティング", "ミーティングの件", "打ち合わせ", "日程"])
    review_only_notice = subject_lower.startswith(("招待:", "invitation:", "updated invitation:")) or "事前review" in subject_lower
    is_noreply = "no-reply" in sender_lower or "noreply" in sender_lower
    is_marketing_sender = any(hint in sender_lower for hint in MARKETING_SENDER_HINTS)
    is_promo_sender_domain = any(domain in sender_lower for domain in promo_sender_domains)
    is_broadcast_like = bool(
        is_promo_subject
        or is_marketing_sender
        or is_promo_sender_domain
        or any(hint.lower() in subject_lower for hint in BROADCAST_SUBJECT_HINTS)
        or any(hint.lower() in text for hint in BROADCAST_BODY_HINTS)
    )

    return {
        "urgent": urgent,
        "alert": is_alert,
        "promo_subject": is_promo_subject,
        "ad_hint": is_ad_hint,
        "actionable_notice": is_actionable_notice,
        "business_review": has_business_review_signal,
        "contract_followup_subject": is_contract_followup_subject,
        "marketing_sender": is_marketing_sender,
        "promo_sender_domain": is_promo_sender_domain,
        "broadcast_like": is_broadcast_like,
        "broadcast_business_review": bool(is_broadcast_like and has_business_review_signal),
        "meeting_coordination": meeting_coordination,
        "review_only_notice": review_only_notice,
        "is_noreply": is_noreply,
        "context_project_match": bool(projects),
        "context_project_strong": any(str(row.get("match_kind") or "") in {"project", "client"} for row in projects),
    }


def detect_early_archive_rule(
    subject: str,
    sender: str,
    body: str,
    *,
    chatwork_mention_hints: Tuple[str, ...],
    non_actionable_subject_patterns: List[str],
) -> Tuple[str | None, bool]:
    text = f"{subject} {sender} {body}".lower()
    sender_lower = (sender or "").lower()
    subject_lower = (subject or "").lower()

    if subject_lower.startswith(("承諾:", "辞退:", "accepted:", "declined:")):
        return "calendar_response", False
    if "[aws pipeline]" in subject_lower and "成功" in subject_lower and "etl結果" in subject_lower:
        return "pipeline_success_archive", False
    if (
        "tokiwagi-base" in subject_lower
        and any(
            hint in subject_lower
            for hint in (
                "最新版ではありません",
                "新しいログイン動作を検知しました",
                "synology nas への新しいログイン",
            )
        )
    ):
        return "tokiwagi_base_info_archive", False
    if (
        "instagram" in sender_lower
        and "info@tokiwa-gi.com" in sender_lower
        and any(hint in subject_lower for hint in ("チェックしよう", "見逃したコンテンツ", "フィードで"))
    ):
        return "internal_instagram_recap_archive", False
    is_chatwork_mail = "chatwork" in sender_lower or "ns.chatwork.com" in sender_lower
    is_chatwork_mention = is_chatwork_mail and any(hint in text for hint in chatwork_mention_hints)
    if is_chatwork_mail and not is_chatwork_mention:
        return "chatwork_non_mention_archive", False
    if "asobi-yoyaku@bornelund.co.jp" in sender_lower:
        return "bornelund_asobi_promo_archive", True
    if "アンバサダー通信" in subject_lower:
        return "ambassador_newsletter_archive", False
    if any(re.search(pattern, subject_lower) for pattern in non_actionable_subject_patterns):
        return "non_actionable_subject_archive", False
    return None, False


def detect_reply_intent(
    text: str,
    *,
    explicit_reply_patterns: Tuple[str, ...],
    explicit_action_request_patterns: Tuple[str, ...],
    promo_reply_suppress_hints: Tuple[str, ...],
) -> Tuple[bool, bool, bool]:
    reply_text = re.sub(r"reply-to", " ", (text or "").lower())
    has_reply_phrase = any(re.search(pattern, reply_text) for pattern in explicit_reply_patterns)
    has_explicit_action_request = any(re.search(pattern, reply_text) for pattern in explicit_action_request_patterns)
    promo_reply_risk = any(hint.lower() in reply_text for hint in promo_reply_suppress_hints)
    return has_reply_phrase, has_explicit_action_request, promo_reply_risk


def should_apply_local_override(
    current_category: str,
    local_category: str,
    sender: str,
    subject: str,
    *,
    promo_sender_domains: List[str],
    promo_subject_hints: List[str],
    business_review_keywords: List[str],
) -> bool:
    if local_category == current_category:
        return False
    sender_lower = (sender or "").lower()
    subject_lower = (subject or "").lower()

    if local_category == "archive":
        is_noreply = ("no-reply" in sender_lower) or ("noreply" in sender_lower)
        promo_domain = any(domain in sender_lower for domain in promo_sender_domains)
        promo_subject = any(hint.lower() in subject_lower for hint in promo_subject_hints)
        has_business_keyword = any(keyword.lower() in subject_lower for keyword in business_review_keywords)
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


def apply_local_preclassify_result(
    category: str,
    tags: List[str],
    meta: Dict[str, Any],
    needs_reply: bool,
    *,
    local_category: str | None,
    local_reason: str,
    local_meta: Dict[str, Any],
    sender: str,
    subject: str,
    promo_sender_domains: List[str],
    promo_subject_hints: List[str],
    business_review_keywords: List[str],
) -> Tuple[str, List[str], Dict[str, Any], bool]:
    meta["local_preclassify"] = {**local_meta, "category": local_category, "reason": local_reason}
    if local_category and should_apply_local_override(
        category,
        local_category,
        sender,
        subject,
        promo_sender_domains=promo_sender_domains,
        promo_subject_hints=promo_subject_hints,
        business_review_keywords=business_review_keywords,
    ):
        category = local_category
        tags = _dedupe_tags(tags + ["local:override"])
        if local_category == "needs_reply":
            needs_reply = True
        if local_reason:
            meta["local_reason"] = local_reason
    return category, tags, meta, needs_reply


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
    if signals.get("broadcast_like"):
        newsletter_score += 2
    if signals.get("ad_hint"):
        newsletter_score += 1
    if signals.get("is_noreply"):
        newsletter_score += 1

    explicit_task_signal = bool(
        needs_reply
        or signals.get("explicit_action_request")
        or signals.get("contract_followup_subject")
        or (has_tool_tag and signals.get("actionable_notice") and signals.get("alert"))
    )
    direct_task_signal = bool(
        signals.get("explicit_action_request")
        or signals.get("contract_followup_subject")
        or (has_tool_tag and signals.get("actionable_notice") and signals.get("alert"))
    )
    newsletter_low_value = bool(
        newsletter_score >= 4
        and not signals.get("business_review")
        and not signals.get("actionable_notice")
        and not signals.get("alert")
        and not signals.get("contract_followup_subject")
        and not explicit_task_signal
    )
    review_only_notice = bool(signals.get("review_only_notice"))

    if signals.get("business_review"):
        review_score += 4
    if signals.get("actionable_notice"):
        review_score += 3
    if signals.get("contract_followup_subject"):
        review_score += 2
    if signals.get("alert"):
        review_score += 3
    if signals.get("urgent"):
        review_score += 1
    if has_tool_tag:
        review_score += 1
    if signals.get("context_project_match"):
        review_score += 3
    if signals.get("context_project_strong"):
        review_score += 2

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
    if signals.get("contract_followup_subject"):
        task_score += 4
    if has_tool_tag and signals.get("actionable_notice") and signals.get("alert"):
        task_score += 3
    if signals.get("context_project_match") and (signals.get("meeting_coordination") or signals.get("explicit_action_request")):
        task_score += 2

    meta["bucket_scores"] = {
        "newsletter": newsletter_score,
        "review": review_score,
        "task": task_score,
        "has_tool_tag": has_tool_tag,
        "newsletter_low_value": newsletter_low_value,
        "review_only_notice": review_only_notice,
        "broadcast_business_review": bool(signals.get("broadcast_business_review")),
        "direct_task_signal": direct_task_signal,
    }

    if category == "archive":
        if newsletter_low_value and not contact_meta.get("thread_replied"):
            return "archive", "newsletter_low_value"
        if task_score >= 4:
            return "task", "weighted_action_override"
        if review_score >= 4:
            return "review", "weighted_review_override"
        return "archive", "promo_or_low_value"
    if category == "later_check":
        if newsletter_low_value and not has_tool_tag and not contact_meta.get("thread_replied"):
            return "archive", "newsletter_low_value"
        if task_score >= 4:
            return "task", "weighted_task_from_tool_notice"
        if review_score >= 3:
            return "review", "weighted_review_from_tool_notice"
        if newsletter_score >= 5 and not has_tool_tag:
            return "archive", "newsletter_low_value"
        return "digest", "tool_notice_or_digest"
    if category == "needs_reply" or needs_reply:
        if signals.get("broadcast_business_review") and not direct_task_signal and not contact_meta.get("known"):
            return "review", "broadcast_business_review"
        return "task", "explicit_reply_or_action"

    if category == "needs_review":
        if signals.get("broadcast_business_review") and not direct_task_signal and not contact_meta.get("thread_replied"):
            return "review", "broadcast_business_review"
        if newsletter_low_value and not contact_meta.get("thread_replied"):
            return "digest", "newsletter_review_downgraded"
        if review_only_notice and not explicit_task_signal:
            return "review", "review_only_notice"
        if task_score >= 3:
            return "task", "coordination_requires_followup"
        return "review", "human_review_needed"

    return "review", "default_review"

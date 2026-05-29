#!/usr/bin/env python3
from __future__ import annotations

import re
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


def _hint_matches_text(text: str, hint: str) -> bool:
    needle = (hint or "").strip().lower()
    if not needle:
        return False
    if re.fullmatch(r"[a-z0-9!+._ -]+", needle):
        pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return needle in text


def build_context_project_hints(seed: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    hints: List[Dict[str, Any]] = []
    if not isinstance(seed, dict):
        return hints
    for row in seed.get("projects") or []:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "").strip()
        if not project:
            continue
        values: List[Tuple[str, str]] = [(project, "project")]
        client_name = str(row.get("client_name") or "").strip()
        if client_name:
            values.append((client_name, "client"))
        for alias in row.get("aliases") or []:
            label = str(alias).strip()
            if label:
                values.append((label, "alias"))
        for entity in row.get("related_entities") or []:
            label = str(entity).strip()
            if label:
                values.append((label, "related"))
        seen = set()
        terms: List[Dict[str, str]] = []
        for value, kind in values:
            low = value.lower()
            if low in seen:
                continue
            seen.add(low)
            terms.append({"value": value, "kind": kind})
        hints.append({"project": project, "terms": terms})
    return hints


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


def match_context_projects(
    subject: str,
    sender: str,
    cc: str,
    body: str,
    project_hints: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    text = f"{subject} {sender} {cc} {body}".lower()
    matches: List[Dict[str, Any]] = []
    for row in project_hints or []:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "").strip()
        if not project:
            continue
        matched_term: Dict[str, str] | None = None
        for term in row.get("terms") or []:
            if not isinstance(term, dict):
                continue
            value = str(term.get("value") or "").strip()
            if value and _hint_matches_text(text, value):
                matched_term = {"value": value, "kind": str(term.get("kind") or "").strip() or "alias"}
                break
        if matched_term:
            matches.append({"project": project, "matched_term": matched_term["value"], "match_kind": matched_term["kind"]})
    return matches


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


def apply_project_override(
    category: str,
    tags: List[str],
    meta: Dict[str, Any],
) -> Tuple[str, List[str], Dict[str, Any]]:
    if meta.get("suppress_project_override"):
        return category, tags, meta
    context_projects = meta.get("context_projects") if isinstance(meta, dict) else None
    if not isinstance(context_projects, list) or not context_projects:
        return category, tags, meta
    project_names = [str(row.get("project") or "").strip() for row in context_projects if str(row.get("project") or "").strip()]
    tags = _dedupe_tags(tags + ["context:project", *[f"context_project:{name}" for name in project_names]])
    signals = meta.get("signals") if isinstance(meta, dict) else {}
    if not isinstance(signals, dict):
        signals = {}
    promo_noise = bool(
        (
            signals.get("promo_subject")
            or signals.get("marketing_sender")
            or signals.get("promo_sender_domain")
            or (signals.get("ad_hint") and signals.get("is_noreply"))
        )
        and not signals.get("business_review")
        and not signals.get("actionable_notice")
        and not signals.get("alert")
        and not signals.get("contract_followup_subject")
        and not signals.get("explicit_action_request")
        and not signals.get("meeting_coordination")
    )
    if promo_noise:
        meta["project_reason"] = "context_project_suppressed_for_promo"
        return category, tags, meta
    if category == "archive":
        meta["project_reason"] = "context_project_promoted_from_archive"
        return "needs_review", tags, meta
    if category == "later_check":
        meta["project_reason"] = "context_project_promoted_from_later_check"
        return "needs_review", tags, meta
    return category, tags, meta

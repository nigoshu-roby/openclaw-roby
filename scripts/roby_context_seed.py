#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

CONTEXT_SEED_PATH = Path(__file__).resolve().parents[1] / 'docs' / 'pbs_context_seed.md'


def _split_section(text: str, heading: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return ""
    start = m.end()
    next_heading = re.search(r"^##\s+.+$", text[start:], re.MULTILINE)
    if next_heading:
        return text[start:start + next_heading.start()]
    return text[start:]


def _extract_backticked_values(text: str) -> List[str]:
    return [m.group(1).strip() for m in re.finditer(r"`([^`]+)`", text) if m.group(1).strip()]


def _split_inline_values(raw: str) -> List[str]:
    text = (raw or "").strip()
    if not text:
        return []
    for needle in ("（", "("):
        idx = text.find(needle)
        if idx > 0:
            text = text[:idx].strip()
            break
    values: List[str] = []
    for part in re.split(r"[,、/]|\s+", text):
        item = part.strip().strip('・')
        if item:
            values.append(item)
    return values


def _split_phrase_values(raw: str) -> List[str]:
    text = (raw or "").strip()
    if not text:
        return []
    values: List[str] = []
    for part in re.split(r"[\n、,]+", text):
        item = part.strip().strip("・")
        if item:
            values.append(item)
    return values


def _extract_person_names(text: str) -> List[str]:
    names: List[str] = []
    patterns = [
        r"([一-龯ぁ-んァ-ヶA-Za-z]+(?:[　 ][一-龯ぁ-んァ-ヶA-Za-z]+)?)\s*(?:さん|様|氏|本部長)",
        r'([一-龯ぁ-んァ-ヶA-Za-z]+)（[^）]*?）',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            raw = (match.group(1) or "").strip()
            if raw:
                names.append(raw)
    return list(dict.fromkeys(names))


def _parse_role_section(section: str) -> Dict[str, Any]:
    aliases: List[str] = []
    m = re.search(r"-\s*表示名の揺れ:\s*(.*?)(?=\n-\s|\Z)", section, re.S)
    if m:
        aliases.extend(_extract_backticked_values(m.group(1)))
    return {"self_aliases": list(dict.fromkeys(aliases))}


def _parse_owner_section(section: str) -> Dict[str, Any]:
    self_aliases: List[str] = []
    m = re.search(r"-\s*自分扱いにしてよい表現:\s*(.*?)(?=\n-\s|\Z)", section, re.S)
    if m:
        self_aliases.extend(_extract_backticked_values(m.group(1)))
    other_names: List[str] = []
    m = re.search(r"-\s*他担当としてよく出る人:\s*(.*?)(?=\n-\s*「|\Z)", section, re.S)
    if m:
        mm = re.search(r"-\s*名前:\s*(.+)", m.group(1))
        if mm:
            other_names.extend(_extract_backticked_values(mm.group(1)))
            if not other_names:
                other_names.extend(_split_inline_values(mm.group(1)))
    return {
        "self_aliases": list(dict.fromkeys(self_aliases)),
        "other_owner_names": list(dict.fromkeys(other_names)),
    }


def _extract_field(block: str, label: str) -> str:
    m = re.search(rf"^-\s*{re.escape(label)}:\s*(.*)$", block, re.M)
    return (m.group(1) or "").strip() if m else ""


def _extract_subblock(block: str, label: str) -> str:
    m = re.search(rf"^-\s*{re.escape(label)}:\s*(.*?)(?=\n^-\s+[^\s]|\Z)", block, re.S | re.M)
    return (m.group(1) or "").strip() if m else ""


def _parse_projects(section: str) -> List[Dict[str, Any]]:
    projects: List[Dict[str, Any]] = []
    blocks = re.split(r"^###\s+Project\s*$", section, flags=re.M)
    for block in blocks[1:]:
        project = _extract_field(block, '正式名')
        if not project:
            continue
        aliases = _split_inline_values(_extract_field(block, '略称 / 別名'))
        keywords = _split_inline_values(_extract_field(block, '会議や議事録でよく出る固有語'))
        relation_text = _extract_subblock(block, '関係者')
        owner_hints = _extract_person_names(relation_text)
        owner_hints.extend(_split_inline_values(_extract_field(block, 'クライアント担当者')))
        owner_hints.extend(_split_inline_values(_extract_field(block, '社内担当者')))
        task_block = _extract_subblock(block, 'よくある作業')
        action_hints = []
        for line in task_block.splitlines():
            m = re.match(r"\s*-\s*(.+)", line)
            if m:
                val = m.group(1).strip()
                if val:
                    action_hints.append(val)
        positive_task_hints = _split_phrase_values(_extract_field(block, 'task にしやすいもの'))
        negative_task_hints = _split_phrase_values(_extract_field(block, 'task にしなくてよいもの'))
        self_scope = _extract_field(block, 'この project で自分が担当する範囲')
        non_self_scope = _extract_field(block, '自分が担当しない範囲')
        projects.append(
            {
                "project": project,
                "aliases": list(dict.fromkeys([a for a in aliases if a and a != project])),
                "keywords": list(dict.fromkeys([k for k in keywords if k])),
                "owner_hints": list(dict.fromkeys([n for n in owner_hints if n])),
                "action_hints": list(dict.fromkeys(action_hints)),
                "positive_task_hints": list(dict.fromkeys([x for x in positive_task_hints if x])),
                "negative_task_hints": list(dict.fromkeys([x for x in negative_task_hints if x])),
                "self_scope": self_scope,
                "non_self_scope": non_self_scope,
            }
        )
    return projects


def _parse_important_senders(section: str) -> List[Dict[str, Any]]:
    senders: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    for raw_line in section.splitlines():
        line = raw_line.rstrip()
        m = re.match(r"\s*-\s*名前:\s*(.+)$", line)
        if m:
            if current:
                senders.append(current)
            current = {"name": m.group(1).strip(), "emails": [], "company": "", "importance": "", "topics": ""}
            continue
        if not current:
            continue
        m = re.match(r"\s*-\s*メール:\s*(.*)$", line)
        if m:
            emails = [x.strip() for x in re.split(r"\s*/\s*|\s*,\s*", m.group(1).strip()) if x.strip()]
            current["emails"] = emails
            continue
        m = re.match(r"\s*-\s*会社:\s*(.*)$", line)
        if m:
            current["company"] = m.group(1).strip()
            continue
        m = re.match(r"\s*-\s*重要度:\s*(.*)$", line)
        if m:
            current["importance"] = m.group(1).strip()
            continue
        m = re.match(r"\s*-\s*どういう内容が多いか:\s*(.*)$", line)
        if m:
            current["topics"] = m.group(1).strip()
            continue
    if current:
        senders.append(current)
    for sender in senders:
        domains = []
        for email in sender.get('emails', []):
            if '@' in email:
                domains.append(email.split('@',1)[1].lower())
        sender['domains'] = list(dict.fromkeys(domains))
    return senders


def parse_context_seed(text: str) -> Dict[str, Any]:
    role = _parse_role_section(_split_section(text, '1. 自分の役割'))
    owner = _parse_owner_section(_split_section(text, '3. Owner / 担当者ルール'))
    projects = _parse_projects(_split_section(text, '2. Project / Client テンプレート'))
    email_section = _split_section(text, '4. Email 判断ルール')
    senders = _parse_important_senders(email_section)
    return {
        'role': role,
        'owner_rules': owner,
        'projects': projects,
        'email': {
            'important_senders': senders,
        },
    }


def load_context_seed(path: Path | None = None) -> Dict[str, Any]:
    target = (path or CONTEXT_SEED_PATH).expanduser()
    if not target.exists():
        return {}
    try:
        return parse_context_seed(target.read_text(encoding='utf-8'))
    except Exception:
        return {}

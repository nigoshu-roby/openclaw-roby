#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse


DEFAULT_IMPORT_URL = "http://127.0.0.1:5174/api/v1/tasks/import"
DEFAULT_FALLBACK_URL = "http://127.0.0.1:5174/api/v1/tasks/bulk"

FIELD_ALIASES = {
    "parent_origin_id": "parentOriginId",
    "sibling_order": "siblingOrder",
    "outline_path": "outlinePath",
    "external_ref": "externalRef",
    "run_id": "runId",
    "feedback_state": "feedbackState",
    "source_doc_id": "sourceDocId",
    "source_doc_title": "sourceDocTitle",
}


def get_neuronic_urls(env: Dict[str, str]) -> Tuple[str, str]:
    return (
        env.get("NEURONIC_URL", DEFAULT_IMPORT_URL),
        env.get("NEURONIC_FALLBACK_URL", DEFAULT_FALLBACK_URL),
    )


def build_neuronic_headers(env: Dict[str, str]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = env.get("NEURONIC_TOKEN") or env.get("TASKD_AUTH_TOKEN")
    if token:
        header_name = env.get("NEURONIC_AUTH_HEADER", "Authorization")
        headers[header_name] = f"Bearer {token}"
    return headers


def build_neuronic_items(tasks: List[Dict[str, Any]], *, include_outline_path: bool = True) -> List[Dict[str, Any]]:
    payload_items: List[Dict[str, Any]] = []
    for item in tasks:
        row = dict(item)
        for source_key, target_key in FIELD_ALIASES.items():
            if source_key == "outline_path" and not include_outline_path:
                continue
            if source_key in row:
                row[target_key] = row.get(source_key)
        payload_items.append(row)
    return payload_items


def endpoint_used_value(target_url: str, endpoint_style: str = "url") -> str:
    if endpoint_style == "path":
        parsed = urlparse(target_url)
        if parsed.path:
            return parsed.path
    return target_url


def post_neuronic_items(
    target_url: str,
    items: List[Dict[str, Any]],
    *,
    headers: Dict[str, str],
    timeout: int = 10,
    endpoint_style: str = "url",
) -> Dict[str, Any]:
    data = json.dumps({"items": items}).encode("utf-8")
    req = urllib.request.Request(target_url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status_code = getattr(resp, "status", 200)
        body = resp.read().decode("utf-8", "ignore")
    try:
        parsed: Any = json.loads(body)
    except Exception:
        parsed = {"response": body}
    return {
        "ok": True,
        "status_code": status_code,
        "endpoint_used": endpoint_used_value(target_url, endpoint_style),
        "body": parsed,
    }

#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, Tuple


def is_gemini_model(model: str) -> bool:
    normalized = (model or "").strip().lower()
    return normalized.startswith("google/gemini") or normalized.startswith("gemini")


def normalize_gemini_model(model: str) -> str:
    text = (model or "").strip()
    if "/" in text:
        provider, rest = text.split("/", 1)
        if provider.strip().lower() == "google":
            return rest.strip()
    return text


def parse_jsonish_text(raw: str) -> Any:
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        match = re.search(r"(\{.*\}|\[.*\])", s, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except Exception:
            return None


def run_gemini_json_prompt(
    *,
    prompt: str,
    source_text: str,
    env: Dict[str, str],
    model: str,
    timeout_sec: int = 60,
    max_output_tokens: int = 1200,
    temperature: float = 0.1,
) -> Tuple[Any, str]:
    api_key = (
        env.get("GEMINI_API_KEY")
        or env.get("GOOGLE_GENERATIVE_AI_API_KEY")
        or env.get("GOOGLE_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError("missing_gemini_api_key")

    model_id = normalize_gemini_model(model or "google/gemini-3-flash-preview")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + urllib.parse.quote(model_id, safe="")
        + ":generateContent?key="
        + urllib.parse.quote(api_key, safe="")
    )
    text = f"{prompt.strip()}\n\n[INPUT]\n{(source_text or '').strip()}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw_http = resp.read().decode("utf-8", "ignore")
    data = json.loads(raw_http)
    parts = (
        (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        if isinstance(data, dict)
        else []
    )
    raw_text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
    return parse_jsonish_text(raw_text), raw_text

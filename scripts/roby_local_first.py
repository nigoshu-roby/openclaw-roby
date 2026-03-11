#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


def env_flag(env: Dict[str, str], key: str, default: bool = False) -> bool:
    value = env.get(key)
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def int_from_env(env: Dict[str, str], key: str, default: int) -> int:
    value = env.get(key)
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except Exception:
        return default


def float_from_env(env: Dict[str, str], key: str, default: float) -> float:
    value = env.get(key)
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except Exception:
        return default


def ollama_cli_present() -> bool:
    return shutil.which("ollama") is not None


def run_ollama_json(
    *,
    prompt: str,
    source_text: str,
    env: Dict[str, str],
    model: str,
    timeout_sec: int = 60,
    num_predict: int = 1200,
    temperature: float = 0.2,
    top_p: float = 0.9,
    repeat_penalty: float = 1.05,
    base_url_key: str = "ROBY_ORCH_OLLAMA_BASE_URL",
) -> Tuple[Optional[Any], Dict[str, Any]]:
    if not ollama_cli_present():
        return None, {"ok": False, "error": "ollama_not_installed", "backend": "ollama_api"}

    base_url = (env.get(base_url_key) or "http://127.0.0.1:11434").strip().rstrip("/")
    normalized_model = (model or "").strip()
    if normalized_model.lower().startswith("ollama/"):
        normalized_model = normalized_model.split("/", 1)[1].strip()
    payload = {
        "model": normalized_model,
        "prompt": f"{prompt.strip()}\n\n[INPUT]\n{source_text.strip()}",
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "num_predict": num_predict,
        },
    }
    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return None, {
            "ok": False,
            "error": f"ollama_http_{e.code}",
            "detail": e.read().decode("utf-8", "ignore"),
            "backend": "ollama_api",
            "model": normalized_model,
        }
    except urllib.error.URLError as e:
        return None, {
            "ok": False,
            "error": f"ollama_connection_error: {e.reason}",
            "backend": "ollama_api",
            "model": normalized_model,
        }
    except Exception as e:
        return None, {
            "ok": False,
            "error": f"ollama_runtime_error: {e}",
            "backend": "ollama_api",
            "model": normalized_model,
        }

    try:
        response_obj = json.loads(raw)
    except Exception:
        return None, {
            "ok": False,
            "error": "ollama_invalid_http_json",
            "backend": "ollama_api",
            "model": normalized_model,
            "raw": raw[:1000],
        }

    output = str(response_obj.get("response") or "").strip()
    if not output:
        return None, {
            "ok": False,
            "error": "ollama_empty_output",
            "backend": "ollama_api",
            "model": normalized_model,
        }

    try:
        parsed = json.loads(output)
    except Exception:
        parsed = None
        for start, end in (("{", "}"), ("[", "]")):
            left = output.find(start)
            right = output.rfind(end)
            if left >= 0 and right > left:
                try:
                    parsed = json.loads(output[left : right + 1])
                    break
                except Exception:
                    continue
        if parsed is None:
            return None, {
                "ok": False,
                "error": "ollama_invalid_output_json",
            "backend": "ollama_api",
            "model": normalized_model,
            "raw": output[:1000],
        }

    return parsed, {
        "ok": True,
        "backend": "ollama_api",
        "model": normalized_model,
        "base_url": base_url,
        "raw_length": len(output),
    }

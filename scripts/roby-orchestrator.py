#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from roby_audit import append_audit_event

ENV_PATH = Path.home() / ".openclaw" / ".env"
STATE_DIR = Path.home() / ".openclaw" / "roby"
RUN_LOG_PATH = STATE_DIR / "orchestrator_runs.jsonl"
JST = timezone(timedelta(hours=9))
OPENCLAW_REPO = Path("/Users/<user>/OpenClaw")
MINUTES_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-minutes.py"
SELF_GROWTH_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-self-growth.py"
GMAIL_TRIAGE_SCRIPT = OPENCLAW_REPO / "skills" / "roby-mail" / "scripts" / "gmail_triage.py"
NOTION_SYNC_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-notion-sync.py"
EVAL_HARNESS_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-eval-harness.py"
DRILL_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-drill.py"
WEEKLY_REPORT_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-weekly-report.py"
AB_ROUTER_CONFIG_PATH = OPENCLAW_REPO / "config" / "pbs" / "ab_router.json"
AB_RUN_LOG_PATH = STATE_DIR / "ab_router_runs.jsonl"

ROUTE_QA = "qa_gemini"
ROUTE_QA_LOCAL = "qa_ollama"
ROUTE_CODING = "coding_codex"
ROUTE_MINUTES = "minutes_pipeline"
ROUTE_SELF_GROWTH = "self_growth"
ROUTE_GMAIL = "gmail_pipeline"
ROUTE_NOTION_SYNC = "notion_sync"
ROUTE_EVAL = "evaluation_harness"
ROUTE_DRILL = "runbook_drill"
ROUTE_WEEKLY_REPORT = "weekly_report"

CODING_HINTS = [
    "実装", "修正", "バグ", "テスト", "リファクタ", "コーディング", "コード", "ui", "ux", "画面", "api", "連携", "デプロイ", "再起動", "改善", "追加", "変更"
]
OLLAMA_HINTS = [
    "ollama", "ローカルllm", "local llm", "ローカルで回答", "ローカル回答"
]
MINUTES_HINTS = [
    "議事録", "notion", "gdocs", "google docs", "googlemeet", "google meet", "タスク抽出", "細分化", "neuronic", "tokiwagi"
]
GMAIL_HINTS = [
    "gmail", "メール", "受信箱", "inbox", "返信リマインド", "返信が必要", "triage", "アーカイブ", "広告メール"
]
NOTION_SYNC_HINTS = [
    "notion同期", "notion sync", "notion更新", "weekly focusをnotion", "done this weekをnotion", "githubからnotion"
]
EVAL_HINTS = [
    "評価", "eval", "harness", "品質評価", "回帰テスト", "評価ハーネス", "品質検証", "回帰"
]
DRILL_HINTS = [
    "drill", "ドリル", "runbook", "ランブック", "運用確認", "疎通確認", "運用テスト"
]
WEEKLY_REPORT_HINTS = [
    "週次レポート", "weekly report", "週報", "運用レポート", "サマリレポート"
]


def bool_from_env(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default
GMAIL_EXEC_HINTS = [
    "整理", "仕分け", "実行", "triage", "アーカイブ", "通知", "確認して", "走らせて"
]
SELF_GROWTH_HINTS = [
    "自己成長", "self-growth", "self growth", "自己改修", "自己修正", "自動パッチ", "毎時改善", "roby-self-growth"
]
MINUTES_EXEC_HINTS = [
    "実行", "取り込み", "連携", "同期", "抽出して", "タスク化して", "一覧", "list", "--select", "--run"
]
CONSULT_HINTS = [
    "どう", "改善", "方針", "相談", "設計", "考え", "おすすめ", "べき", "案"
]

FEATURE_LIST_HINTS = [
    "機能", "一覧", "リスト", "何ができる", "現状", "実装済み", "対応済み"
]

IMAGE_TEXT_HINTS = [
    "画像", "添付", "ocr", "文字", "テキスト", "読み取", "読取", "抽出", "写っている内容"
]


def load_env() -> Dict[str, str]:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            env[key] = val
    return env


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def float_from_env(value: Optional[str], default: float) -> float:
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except Exception:
        return default


def int_from_env(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except Exception:
        return default


def load_ab_router_config(env: Dict[str, str]) -> Dict[str, Any]:
    path = Path(env.get("ROBY_ORCH_AB_ROUTER_CONFIG", str(AB_ROUTER_CONFIG_PATH)))
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def choose_weighted_arm(
    arms: List[Dict[str, Any]],
    seed_key: str,
) -> Tuple[Optional[Dict[str, Any]], int, int]:
    normalized: List[Dict[str, Any]] = []
    total = 0
    for arm in arms:
        if not isinstance(arm, dict):
            continue
        weight = int(arm.get("weight", 0) or 0)
        if weight <= 0:
            continue
        normalized.append({"arm": arm, "weight": weight})
        total += weight
    if total <= 0:
        return None, 0, 0
    digest = hashlib.sha256(seed_key.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % total
    acc = 0
    for row in normalized:
        acc += row["weight"]
        if bucket < acc:
            return row["arm"], bucket, total
    return normalized[-1]["arm"], bucket, total


def pick_ab_router_for_qa(message: str, env: Dict[str, str]) -> Tuple[Dict[str, str], Optional[Dict[str, Any]]]:
    config = load_ab_router_config(env)
    enabled = bool_from_env(env.get("ROBY_ORCH_AB_ROUTER", ""), default=bool(config.get("enabled", False)))
    if not enabled:
        return {}, None
    qa_conf = config.get("qa_gemini")
    if not isinstance(qa_conf, dict) or not bool(qa_conf.get("enabled", True)):
        return {}, None
    arms = qa_conf.get("arms")
    if not isinstance(arms, list) or not arms:
        return {}, None

    seed = str(config.get("seed", "pbs-ab-router-v1"))
    rotate_daily = bool(config.get("rotate_daily", False))
    msg_norm = re.sub(r"\s+", " ", (message or "").strip().lower())
    day_tag = datetime.now(JST).strftime("%Y-%m-%d") if rotate_daily else "stable"
    seed_key = f"{seed}|qa_gemini|{day_tag}|{msg_norm}"
    selected, bucket, total = choose_weighted_arm(arms, seed_key)
    if not selected:
        return {}, None

    overrides: Dict[str, str] = {}
    if selected.get("model"):
        overrides["ROBY_ORCH_GEMINI_MODEL"] = str(selected["model"])
    if selected.get("length"):
        overrides["ROBY_ORCH_GEMINI_LENGTH"] = str(selected["length"])
    if selected.get("qa_max_tokens"):
        overrides["ROBY_ORCH_QA_MAX_TOKENS"] = str(selected["qa_max_tokens"])
    if selected.get("qa_retry_max_tokens"):
        overrides["ROBY_ORCH_QA_RETRY_MAX_TOKENS"] = str(selected["qa_retry_max_tokens"])
    if selected.get("qa_timeout_sec"):
        overrides["ROBY_ORCH_QA_TIMEOUT_SEC"] = str(selected["qa_timeout_sec"])
    if selected.get("prompt"):
        overrides["ROBY_ORCH_GEMINI_QA_PROMPT"] = str(selected["prompt"])
    elif selected.get("prompt_env_key"):
        prompt_env_key = str(selected.get("prompt_env_key"))
        prompt_env_value = env.get(prompt_env_key, "").strip()
        if prompt_env_value:
            overrides["ROBY_ORCH_GEMINI_QA_PROMPT"] = prompt_env_value

    decision = {
        "enabled": True,
        "route": ROUTE_QA,
        "arm_id": str(selected.get("id", "unknown")),
        "label": str(selected.get("label", "")),
        "bucket": bucket,
        "total_weight": total,
        "seed": seed,
        "rotate_daily": rotate_daily,
        "overrides": sorted(list(overrides.keys())),
        "ts": datetime.now(JST).isoformat(),
    }
    return overrides, decision


def classify_intent_heuristic(message: str) -> str:
    lower = message.lower()
    if any(k in lower for k in OLLAMA_HINTS):
        return ROUTE_QA_LOCAL
    if any(k in lower for k in SELF_GROWTH_HINTS):
        return ROUTE_SELF_GROWTH
    if any(k in lower for k in NOTION_SYNC_HINTS):
        return ROUTE_NOTION_SYNC
    if any(k in lower for k in WEEKLY_REPORT_HINTS):
        return ROUTE_WEEKLY_REPORT
    if any(k in lower for k in DRILL_HINTS):
        return ROUTE_DRILL
    if any(k in lower for k in EVAL_HINTS):
        return ROUTE_EVAL
    has_gmail = any(k in lower for k in GMAIL_HINTS)
    has_gmail_exec = any(k in lower for k in GMAIL_EXEC_HINTS)
    has_consult = any(k in lower for k in CONSULT_HINTS)
    if has_gmail:
        if has_gmail_exec and not has_consult:
            return ROUTE_GMAIL
        return ROUTE_QA
    has_minutes = any(k in lower for k in MINUTES_HINTS)
    has_minutes_exec = any(k in lower for k in MINUTES_EXEC_HINTS)
    has_consult = any(k in lower for k in CONSULT_HINTS)
    if has_minutes:
        if has_minutes_exec and not has_consult:
            return ROUTE_MINUTES
        return ROUTE_QA
    if any(k in lower for k in CODING_HINTS):
        return ROUTE_CODING
    return ROUTE_QA


def parse_jsonish(raw: str) -> Any:
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


def run_summarize_json(
    prompt: str,
    text: str,
    env: Dict[str, str],
    max_tokens: str = "1800",
    timeout_sec: int = 120,
    force_summary: bool = True,
) -> Tuple[Any, str]:
    cmd = [
        "summarize", "-",
        "--json", "--plain",
        "--metrics", "off",
        "--model", env.get("ROBY_ORCH_GEMINI_MODEL", env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview")),
        "--length", env.get("ROBY_ORCH_GEMINI_LENGTH", "xl"),
        "--prompt", prompt,
        "--max-output-tokens", max_tokens,
    ]
    if force_summary:
        cmd.insert(-4, "--force-summary")
    out = subprocess.check_output(cmd, input=text.encode("utf-8"), env=env, timeout=timeout_sec)
    data = json.loads(out)
    raw = ""
    for key in ("summary", "output", "text", "result"):
        if isinstance(data.get(key), str) and data.get(key).strip():
            raw = data.get(key)
            break
    return parse_jsonish(raw), raw


def classify_intent_gemini(message: str, env: Dict[str, str]) -> Optional[Dict[str, Any]]:
    prompt = (
        "Classify the user request for orchestration. Return ONLY JSON object with keys: route, reason, confidence. "
        f"route must be one of: {ROUTE_QA}, {ROUTE_QA_LOCAL}, {ROUTE_CODING}, {ROUTE_MINUTES}, {ROUTE_SELF_GROWTH}, {ROUTE_GMAIL}, {ROUTE_NOTION_SYNC}, {ROUTE_EVAL}, {ROUTE_DRILL}, {ROUTE_WEEKLY_REPORT}."
    )
    parsed, raw = run_summarize_json(prompt, message, env, max_tokens="300", timeout_sec=45)
    if isinstance(parsed, dict) and parsed.get("route") in {ROUTE_QA, ROUTE_QA_LOCAL, ROUTE_CODING, ROUTE_MINUTES, ROUTE_SELF_GROWTH, ROUTE_GMAIL, ROUTE_NOTION_SYNC, ROUTE_EVAL, ROUTE_DRILL, ROUTE_WEEKLY_REPORT}:
        parsed["raw"] = raw
        return parsed
    return None


def is_feature_list_request(message: str) -> bool:
    lower = message.lower()
    if "機能" in message and ("一覧" in message or "リスト" in message):
        return True
    if "何ができる" in message or "実装済み" in message or "現状" in message:
        return True
    return sum(1 for k in FEATURE_LIST_HINTS if k in lower or k in message) >= 2


def is_greeting_request(message: str) -> bool:
    normalized = (message or "").strip().lower()
    greeting_tokens = [
        "こんにちは", "こんばんは", "おはよう", "やあ", "hello", "hi", "hey"
    ]
    return normalized in greeting_tokens


def prefers_short_answer(message: str) -> bool:
    text = (message or "").strip().lower()
    short_markers = [
        "短く", "簡潔", "一言", "要点だけ", "3行", "2行", "箇条書きで3",
        "in 3 lines", "in two lines", "briefly",
    ]
    return any(marker in text for marker in short_markers)


def contains_japanese(text: str) -> bool:
    if not text:
        return False
    return re.search(r"[ぁ-んァ-ヶ一-龥]", text) is not None


def is_low_detail_output(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return True
    lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
    if len(normalized) < 80:
        return True
    if len(lines) <= 2 and any("目的" in ln or "案" in ln for ln in lines):
        return True
    return False


def is_broken_qa_output(text: str, message: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return True
    low = normalized.lower()
    bad_markers = [
        "extracted content length",
        "hard limit",
        "let's look at the prompt",
        "system constraint",
    ]
    if any(marker in low for marker in bad_markers):
        return True
    if contains_japanese(message) and not contains_japanese(normalized):
        return True
    return False


def is_truncated_qa_output(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return True
    tail = normalized[-40:]
    bad_tail_patterns = [
        r"##\s*$",
        r"##\s*[^\n]{0,12}$",
        r"[：:]\s*$",
        r"\*\*$",
        r"[\(\[]\s*$",
        r"^(?:##\s*目的|##\s*実行)\s*$",
    ]
    if any(re.search(pat, tail) for pat in bad_tail_patterns):
        return True
    line_count = len([ln for ln in normalized.splitlines() if ln.strip()])
    if line_count <= 2 and len(normalized) < 120:
        return True
    if "## 目的" in normalized:
        required_sections = ["## 提案", "## 推奨案", "## 次のアクション"]
        present = sum(1 for section in required_sections if section in normalized)
        if present <= 1:
            return True
    if normalized.endswith(("## 実行", "## 提案", "## 推奨案", "## 次のアクション")):
        return True
    return False


def is_likely_cutoff_output(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return True
    tail = normalized[-40:]
    if re.search(r"##\s*$", tail):
        return True
    if re.search(r"[：:]\s*$", tail):
        return True
    if re.search(r"\*\*$", tail):
        return True
    if re.search(r"[\(\[]\s*$", tail):
        return True
    return False


def compact_qa_message(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return text
    m_ctx = re.search(
        r"\[直近会話コンテキスト\]\s*(.*?)\s*\[ユーザーの最新依頼\]\s*(.*?)\s*(?:上記コンテキストを前提に回答してください。不要な文脈は無視して構いません。)?\s*$",
        text,
        flags=re.DOTALL,
    )
    if not m_ctx:
        return text
    ctx_raw = m_ctx.group(1).strip()
    latest = m_ctx.group(2).strip()
    user_lines: List[str] = []
    for line in ctx_raw.splitlines():
        ln = line.strip()
        if not ln.startswith("あなた:"):
            continue
        content = ln.split(":", 1)[1].strip()
        if content:
            user_lines.append(content)
    deduped: List[str] = []
    seen: set[str] = set()
    for item in reversed(user_lines):
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
        if len(deduped) >= 2:
            break
    deduped.reverse()
    if not deduped:
        return latest
    points = "\n".join(f"- {item}" for item in deduped)
    return f"[会話要点]\n{points}\n\n[ユーザーの最新依頼]\n{latest}"


def run_qa_generation(qa_prompt: str, qa_input: str, env: Dict[str, str]) -> Tuple[Any, str]:
    base_max_tokens = max(int(env.get("ROBY_ORCH_QA_MAX_TOKENS", "3200") or 3200), 2200)
    parsed, raw = run_summarize_json(
        qa_prompt,
        qa_input,
        env,
        max_tokens=str(base_max_tokens),
        timeout_sec=int(env.get("ROBY_ORCH_QA_TIMEOUT_SEC", "600")),
        force_summary=False,
    )
    text = raw
    if isinstance(parsed, (dict, list)):
        text = json.dumps(parsed, ensure_ascii=False)
    if not is_truncated_qa_output(text):
        return parsed, raw
    retry_prompt = (
        qa_prompt
        + "\n必ず最後まで完結した回答を返してください。途中で切らず、次の見出しをこの順で必ず含めてください: "
        "『## 目的』『## 実行可能な提案（優先順）』『## 判断基準』『## 推奨案』『## 次のアクション』。"
    )
    retry_max_tokens = max(int(env.get("ROBY_ORCH_QA_RETRY_MAX_TOKENS", "4800") or 4800), 4200)
    retry_parsed, retry_raw = run_summarize_json(
        retry_prompt,
        qa_input,
        env,
        max_tokens=str(retry_max_tokens),
        timeout_sec=int(env.get("ROBY_ORCH_QA_TIMEOUT_SEC", "600")),
        force_summary=False,
    )
    retry_text = retry_raw
    if isinstance(retry_parsed, (dict, list)):
        retry_text = json.dumps(retry_parsed, ensure_ascii=False)
    if retry_text and (not is_truncated_qa_output(retry_text) or len(retry_text) > len(text)):
        return retry_parsed, retry_raw
    return parsed, raw


def build_greeting_response() -> str:
    return (
        "こんにちは。対応できます。\n"
        "次のどれを進めますか？\n"
        "1. 相談/設計の回答（qa_gemini）\n"
        "2. 実装の着手（coding_codex）\n"
        "3. 議事録→タスク抽出（minutes_pipeline）\n"
        "4. Gmail仕分け実行（gmail_pipeline）"
    )


def build_local_capability_summary() -> str:
    has_ollama = shutil.which("ollama") is not None
    checks = [
        ("UIチャット + オーケストレーター表示", OPENCLAW_REPO / "ui" / "src" / "ui" / "controllers" / "chat.ts"),
        ("オーケストレーター本体", OPENCLAW_REPO / "scripts" / "roby-orchestrator.py"),
        ("議事録処理（Notion/GDocs）", MINUTES_SCRIPT),
        ("Gmail仕分け", GMAIL_TRIAGE_SCRIPT),
        ("自己成長ジョブ", SELF_GROWTH_SCRIPT),
        ("GitHub→Notion同期", NOTION_SYNC_SCRIPT),
    ]
    lines = ["現在の主要機能一覧（ローカル検出）"]
    for label, path in checks:
        status = "有効" if path.exists() else "未検出"
        lines.append(f"- {label}: {status}")
    lines.extend(
        [
            "",
            "利用可能ルート",
            f"- {ROUTE_QA}",
            f"- {ROUTE_QA_LOCAL} ({'有効' if has_ollama else 'ollama未導入'})",
            f"- {ROUTE_CODING}",
            f"- {ROUTE_MINUTES}",
            f"- {ROUTE_GMAIL}",
            f"- {ROUTE_SELF_GROWTH}",
            f"- {ROUTE_NOTION_SYNC}",
            f"- {ROUTE_EVAL}",
            f"- {ROUTE_DRILL}",
            f"- {ROUTE_WEEKLY_REPORT}",
        ]
    )
    return "\n".join(lines)


def run_qa_ollama_local(message: str, env: Dict[str, str]) -> Dict[str, Any]:
    if shutil.which("ollama") is None:
        return {"ok": False, "error": "ollama_not_installed", "backend": "none"}
    model = env.get("ROBY_ORCH_OLLAMA_MODEL", "qwen2.5:7b").strip()
    if not model:
        model = "qwen2.5:7b"
    prompt = env.get(
        "ROBY_ORCH_OLLAMA_QA_PROMPT",
        "あなたはRobyです。日本語で簡潔に、実務に役立つ回答を返してください。",
    ).strip()
    user_text = compact_qa_message(message)
    composed = f"{prompt}\n\nユーザー:\n{user_text}\n\n回答:"
    timeout = int_from_env(env.get("ROBY_ORCH_OLLAMA_TIMEOUT_SEC", "90"), 90)
    base_url = env.get("ROBY_ORCH_OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
    endpoint = f"{base_url}/api/generate"
    payload = {
        "model": model,
        "prompt": composed,
        "stream": False,
        "options": {
            "temperature": float_from_env(env.get("ROBY_ORCH_OLLAMA_TEMPERATURE", "0.25"), 0.25),
            "top_p": float_from_env(env.get("ROBY_ORCH_OLLAMA_TOP_P", "0.9"), 0.9),
            "repeat_penalty": float_from_env(env.get("ROBY_ORCH_OLLAMA_REPEAT_PENALTY", "1.05"), 1.05),
            "num_predict": int_from_env(env.get("ROBY_ORCH_OLLAMA_NUM_PREDICT", "2200"), 2200),
        },
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            data = json.loads(raw) if raw else {}
    except TimeoutError:
        return {"ok": False, "error": "ollama_timeout", "backend": "ollama_api"}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore").strip()
        except Exception:
            detail = ""
        return {
            "ok": False,
            "error": f"ollama_http_{e.code}",
            "detail": detail[:400],
            "backend": "ollama_api",
        }
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"ollama_connection_error: {e.reason}", "backend": "ollama_api"}
    except Exception as e:
        return {"ok": False, "error": f"ollama_runtime_error: {e}", "backend": "ollama_api"}

    output = str(data.get("response") or "").strip()
    if not output:
        return {"ok": False, "error": "ollama_empty_output", "backend": "ollama_api"}

    min_chars = int_from_env(env.get("ROBY_ORCH_OLLAMA_MIN_OUTPUT_CHARS", "40"), 40)
    if prefers_short_answer(message):
        min_chars = min(30, min_chars)
    if is_broken_qa_output(output, message):
        return {"ok": False, "error": "ollama_low_quality_output", "backend": "ollama_api", "model": model}
    if is_likely_cutoff_output(output):
        return {"ok": False, "error": "ollama_truncated_output", "backend": "ollama_api", "model": model}
    if len(output) < min_chars and not is_greeting_request(message):
        return {"ok": False, "error": "ollama_too_short_output", "backend": "ollama_api", "model": model}
    return {"ok": True, "output": output, "model": model, "backend": "ollama_api"}


def build_coding_requirements(message: str, env: Dict[str, str]) -> Dict[str, Any]:
    prompt = (
        "You are a product/engineering requirements organizer for an autonomous coding agent. "
        "Convert the user's request into implementation-ready requirements in Japanese. "
        "Return ONLY JSON object with keys: objective, scope, constraints, acceptance_criteria, implementation_notes, open_questions. "
        "Rules: "
        "scope must be an array of concrete changed areas/files/components (>=2 items when inferable). "
        "acceptance_criteria must be testable bullet-like strings (>=3 items when inferable). "
        "implementation_notes should include tradeoffs/risks and rollout notes. "
        "If information is missing, infer practical defaults and list assumptions in constraints or implementation_notes instead of leaving arrays empty. "
        "open_questions should only contain blocking decisions."
    )
    parsed, raw = run_summarize_json(prompt, message, env, max_tokens="1400", timeout_sec=90)
    if isinstance(parsed, dict):
        parsed = normalize_coding_requirements(parsed, message)
        parsed["_raw"] = raw
        return parsed
    return normalize_coding_requirements({
        "objective": message.strip(),
        "scope": [],
        "constraints": [],
        "acceptance_criteria": [],
        "implementation_notes": [],
        "open_questions": [],
    }, message) | {"_raw": raw}


def normalize_coding_requirements(req: Dict[str, Any], message: str) -> Dict[str, Any]:
    out = {
        "objective": (req.get("objective") or message).strip(),
        "scope": req.get("scope") if isinstance(req.get("scope"), list) else [],
        "constraints": req.get("constraints") if isinstance(req.get("constraints"), list) else [],
        "acceptance_criteria": req.get("acceptance_criteria") if isinstance(req.get("acceptance_criteria"), list) else [],
        "implementation_notes": req.get("implementation_notes") if isinstance(req.get("implementation_notes"), list) else [],
        "open_questions": req.get("open_questions") if isinstance(req.get("open_questions"), list) else [],
    }
    if not out["scope"]:
        inferred_scope = []
        lower = message.lower()
        if "チャット" in message or "chat" in lower:
            inferred_scope.extend(["チャット画面UI", "入力欄/送信導線"])
        if "ux" in lower or "ui" in lower or "画面" in message:
            inferred_scope.extend(["文言/視認性", "操作フロー"])
        if "api" in lower or "連携" in message:
            inferred_scope.extend(["連携処理", "エラーハンドリング"])
        out["scope"] = list(dict.fromkeys(inferred_scope)) or ["対象機能の既存実装", "関連UI/処理フロー"]
    if not out["acceptance_criteria"]:
        out["acceptance_criteria"] = [
            "ユーザー要求の挙動が再現できること",
            "既存の主要フローを壊さないこと",
            "変更内容と確認結果を実行ログで報告できること",
        ]
    if not out["implementation_notes"]:
        out["implementation_notes"] = [
            "既存実装を確認し、最小差分で変更する",
            "必要ならテスト/ビルドを実行して結果を記録する",
        ]
    return out


def shell_run(cmd: str, env: Dict[str, str], cwd: Optional[Path] = None, timeout: int = 1800) -> Dict[str, Any]:
    try:
        out = subprocess.check_output(["bash", "-lc", cmd], cwd=str(cwd) if cwd else None, env=env, stderr=subprocess.STDOUT, timeout=timeout)
        return {"ok": True, "output": out.decode("utf-8", "ignore")}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "status": e.returncode, "output": (e.output or b"").decode("utf-8", "ignore")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def parse_attachment_files(env: Dict[str, str]) -> List[Dict[str, Any]]:
    raw = (env.get("ROBY_ORCH_ATTACHMENT_FILES") or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    files: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        p = str(item.get("path") or "").strip()
        if not p:
            continue
        files.append(
            {
                "index": int(item.get("index") or (len(files) + 1)),
                "path": p,
                "mimeType": str(item.get("mimeType") or "").strip(),
                "bytes": int(item.get("bytes") or 0),
            }
        )
    return files


def is_image_text_request(message: str) -> bool:
    lower = (message or "").lower()
    return any(token in lower or token in message for token in IMAGE_TEXT_HINTS)


def run_macos_ocr(
    image_path: str,
    timeout_sec: int = 45,
    *,
    level: str = "accurate",
    language_correction: bool = True,
) -> Dict[str, Any]:
    level = (level or "accurate").strip().lower()
    swift_script = r"""
import Foundation
import Vision
import AppKit

func printJson(_ obj: [String: Any]) {
  guard let data = try? JSONSerialization.data(withJSONObject: obj, options: []),
        let s = String(data: data, encoding: .utf8) else {
    print("{\"ok\":false,\"error\":\"json_encode_failed\"}")
    return
  }
  print(s)
}

let args = CommandLine.arguments
guard args.count >= 2 else {
  printJson(["ok": false, "error": "missing_path"])
  exit(0)
}
let path = args[1]
let url = URL(fileURLWithPath: path)
guard let image = NSImage(contentsOf: url) else {
  printJson(["ok": false, "error": "image_load_failed"])
  exit(0)
}
guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
  printJson(["ok": false, "error": "cgimage_failed"])
  exit(0)
}

let request = VNRecognizeTextRequest()
let env = ProcessInfo.processInfo.environment
let level = (env["ROBY_OCR_LEVEL"] ?? "accurate").lowercased()
if level == "fast" {
  request.recognitionLevel = .fast
} else {
  request.recognitionLevel = .accurate
}
let langCorrection = (env["ROBY_OCR_LANG_CORRECTION"] ?? "1") == "1"
request.usesLanguageCorrection = langCorrection
request.recognitionLanguages = ["ja-JP", "en-US"]

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
  try handler.perform([request])
  let observations = (request.results as? [VNRecognizedTextObservation]) ?? []
  var lines: [String] = []
  for ob in observations {
    if let best = ob.topCandidates(1).first {
      let t = best.string.trimmingCharacters(in: .whitespacesAndNewlines)
      if !t.isEmpty {
        lines.append(t)
      }
    }
  }
  let text = lines.joined(separator: "\n")
  printJson([
    "ok": true,
    "text": text,
    "line_count": lines.count
  ])
} catch {
  printJson(["ok": false, "error": "vision_failed: \(error.localizedDescription)"])
}
"""
    child_env = dict(os.environ)
    child_env["ROBY_OCR_LEVEL"] = level
    child_env["ROBY_OCR_LANG_CORRECTION"] = "1" if language_correction else "0"
    try:
        proc = subprocess.run(
            ["swift", "-", image_path],
            input=swift_script.encode("utf-8"),
            capture_output=True,
            timeout=timeout_sec,
            env=child_env,
        )
    except Exception as e:
        return {"ok": False, "error": f"swift_runtime_failed: {e}"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": (proc.stderr.decode("utf-8", "ignore") or "").strip() or f"swift_exit_{proc.returncode}",
        }

    stdout = (proc.stdout.decode("utf-8", "ignore") or "").strip()
    parsed = parse_jsonish(stdout)
    if isinstance(parsed, dict):
        return parsed
    return {"ok": False, "error": "swift_output_parse_failed"}


def map_ocr_error_ja(error: str, text: str = "") -> str:
    normalized = (error or "").strip()
    if not normalized and text:
        return "文字を検出できませんでした。"
    low = normalized.lower()
    if "too small in at least one dimension" in low:
        return "画像解像度が小さすぎます（最低3px以上が必要です）。"
    if "image_load_failed" in low:
        return "画像ファイルを読み込めませんでした。"
    if "cgimage_failed" in low:
        return "画像データの変換に失敗しました。"
    if "swift_runtime_failed" in low:
        return "OCR実行環境の起動に失敗しました。"
    if "swift_output_parse_failed" in low:
        return "OCR結果の解析に失敗しました。"
    if "vision_failed" in low:
        return "OCRエンジンの実行に失敗しました。"
    if "missing_path" in low:
        return "OCR対象の画像パスが見つかりません。"
    if "swift_exit_" in low:
        return "OCR処理が異常終了しました。"
    return normalized or "OCRに失敗しました。"


def should_retry_ocr(first: Dict[str, Any]) -> bool:
    if first.get("ok") and str(first.get("text") or "").strip():
        return False
    err = str(first.get("error") or "").lower()
    if "too small in at least one dimension" in err:
        return False
    if "missing_path" in err:
        return False
    return True


def run_macos_ocr_with_retry(image_path: str, timeout_sec: int = 45) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    first = run_macos_ocr(
        image_path,
        timeout_sec=timeout_sec,
        level="accurate",
        language_correction=True,
    )
    attempts.append({"mode": "accurate", "lang_correction": True, "ok": bool(first.get("ok"))})
    if not should_retry_ocr(first):
        first["attempts"] = attempts
        return first

    second = run_macos_ocr(
        image_path,
        timeout_sec=timeout_sec,
        level="fast",
        language_correction=False,
    )
    attempts.append({"mode": "fast", "lang_correction": False, "ok": bool(second.get("ok"))})

    first_text = str(first.get("text") or "").strip()
    second_text = str(second.get("text") or "").strip()
    chosen = second if second_text and not first_text else first
    if second_text and len(second_text) > len(first_text):
        chosen = second
    chosen["attempts"] = attempts
    return chosen


def read_attachment_texts(env: Dict[str, str]) -> List[Dict[str, Any]]:
    files = parse_attachment_files(env)
    outputs: List[Dict[str, Any]] = []
    for item in files:
        path = item.get("path")
        mime = (item.get("mimeType") or "").lower()
        if not path or (mime and not mime.startswith("image/")):
            continue
        ocr = run_macos_ocr_with_retry(str(path), timeout_sec=int(env.get("ROBY_OCR_TIMEOUT_SEC", "45")))
        text = str(ocr.get("text") or "").strip() if isinstance(ocr, dict) else ""
        ok = bool(isinstance(ocr, dict) and ocr.get("ok") and text)
        raw_error = str(ocr.get("error") or "").strip() if isinstance(ocr, dict) else ""
        error_ja = map_ocr_error_ja(raw_error, text=text if not ok else "")
        attempts = ocr.get("attempts") if isinstance(ocr, dict) else []
        outputs.append(
            {
                "index": item.get("index"),
                "path": path,
                "mimeType": item.get("mimeType"),
                "bytes": item.get("bytes"),
                "ok": ok,
                "text": text,
                "error": error_ja if not ok else "",
                "line_count": int(ocr.get("line_count") or 0) if isinstance(ocr, dict) else 0,
                "attempts": attempts if isinstance(attempts, list) else [],
            }
        )
    return outputs


def format_attachment_text_result(ocr_items: List[Dict[str, Any]]) -> str:
    lines = ["添付画像から抽出したテキストです。"]
    success_items = [item for item in ocr_items if item.get("ok") and item.get("text")]
    if len(success_items) >= 2:
        lines.append("\n## 結合テキスト")
        lines.append("\n\n".join(str(item["text"]) for item in success_items))
    for item in ocr_items:
        idx = item.get("index")
        if item.get("ok") and item.get("text"):
            lines.append(f"\n## 画像{idx}")
            lines.append(str(item["text"]))
        else:
            err = str(item.get("error") or "テキストを抽出できませんでした")
            lines.append(f"\n## 画像{idx}")
            lines.append(f"(抽出失敗: {err})")
    return "\n".join(lines).strip()


def git_status_short(repo: Path, env: Dict[str, str]) -> str:
    res = shell_run("git status --short", env, cwd=repo, timeout=30)
    if not res.get("ok"):
        return ""
    return (res.get("output") or "").strip()


def auto_commit_if_dirty(repo: Path, env: Dict[str, str], objective: str) -> Dict[str, Any]:
    status_before = git_status_short(repo, env)
    if not status_before:
        return {"committed": False, "dirty": False}

    add_res = shell_run("git add -A", env, cwd=repo, timeout=60)
    if not add_res.get("ok"):
        return {"committed": False, "dirty": True, "error": "git add failed", "detail": add_res.get("output", "")}

    safe_obj = re.sub(r"\\s+", " ", (objective or "").strip())
    safe_obj = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龥ー\\-_: /]", "", safe_obj)[:60]
    msg = f"roby orchestrator: {safe_obj or 'auto commit'}"
    commit_res = shell_run(f"git commit -m {shlex.quote(msg)}", env, cwd=repo, timeout=120)
    if not commit_res.get("ok"):
        out = commit_res.get("output", "")
        if "nothing to commit" in out.lower():
            return {"committed": False, "dirty": False, "note": "nothing_to_commit"}
        return {"committed": False, "dirty": True, "error": "git commit failed", "detail": out}

    sha_res = shell_run("git rev-parse --short HEAD", env, cwd=repo, timeout=30)
    return {
        "committed": True,
        "dirty": False,
        "commit": (sha_res.get("output") or "").strip() if sha_res.get("ok") else "",
        "message": msg,
    }


def _drop_agent_flag(cmd: str) -> str:
    # Fallback for environments without a configured named agent (e.g. roby-dev).
    return re.sub(r"\s+--agent\s+\S+", "", cmd).strip()


def _replace_agent_flag(cmd: str, agent_id: str) -> str:
    if re.search(r"\s+--agent\s+\S+", cmd):
        return re.sub(r"(\s+--agent\s+)\S+", rf"\1{agent_id}", cmd, count=1)
    return cmd


def handle_minutes_pipeline(message: str, env: Dict[str, str], execute: bool, verbose: bool) -> Dict[str, Any]:
    select_match = re.search(r"--select\s+\"([^\"]+)\"|--select\s+'([^']+)'|--select\s+(\S+)", message)
    select_val = None
    if select_match:
        select_val = next((g for g in select_match.groups() if g), None)

    run_mode = "list"
    if any(k in message for k in ["実行", "取り込み", "連携", "Neuronic", "タスク化"]):
        run_mode = "run"

    cmd = ["python3", str(MINUTES_SCRIPT)]
    if run_mode == "run":
        cmd.append("--run")
    else:
        cmd.append("--list")
    if select_val:
        cmd.extend(["--select", select_val])
    elif run_mode == "run":
        policy = env.get("ROBY_ORCH_MINUTES_POLICY", "").strip()
        if policy:
            cmd.extend(["--policy", policy])
        if env.get("ROBY_ORCH_MINUTES_FORCE", "0") == "1":
            cmd.append("--force")
        if env.get("ROBY_ORCH_MINUTES_REFRESH", "0") == "1":
            cmd.append("--refresh")
        if env.get("ROBY_ORCH_MINUTES_SKIP_NOTION", "0") == "1":
            cmd.append("--skip-notion")
        if env.get("ROBY_ORCH_MINUTES_SKIP_GDOCS", "0") == "1":
            cmd.append("--skip-gdocs")
        if env.get("ROBY_ORCH_MINUTES_DAYS", "").strip():
            cmd.extend(["--days", env["ROBY_ORCH_MINUTES_DAYS"].strip()])
        if env.get("ROBY_ORCH_MINUTES_MAX", "").strip():
            cmd.extend(["--max", env["ROBY_ORCH_MINUTES_MAX"].strip()])
    if verbose:
        cmd.append("--debug")

    result = {
        "route": ROUTE_MINUTES,
        "mode": run_mode,
        "command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
    }
    if execute:
        proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), env=env, capture_output=True, text=True)
        result["executed"] = True
        result["ok"] = proc.returncode == 0
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
        result["returncode"] = proc.returncode
    return result


def handle_self_growth(env: Dict[str, str], execute: bool) -> Dict[str, Any]:
    cmd = ["python3", str(SELF_GROWTH_SCRIPT)]
    result = {
        "route": ROUTE_SELF_GROWTH,
        "command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
    }
    if execute:
        proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), env=env, capture_output=True, text=True)
        result["executed"] = True
        result["ok"] = proc.returncode == 0
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
        result["returncode"] = proc.returncode
    return result


def handle_gmail_pipeline(message: str, env: Dict[str, str], execute: bool, verbose: bool) -> Dict[str, Any]:
    account = env.get("ROBY_GMAIL_ACCOUNT") or env.get("GOG_ACCOUNT") or ""
    query = env.get("ROBY_GMAIL_QUERY", "newer_than:1d in:inbox")
    max_items = env.get("ROBY_GMAIL_MAX", "20")

    m_query = re.search(r"(newer_than:\S+.*|in:inbox.*)$", message, flags=re.IGNORECASE)
    if m_query:
        query = m_query.group(1).strip()
    m_max = re.search(r"--max\s+(\d+)|(\d+)\s*件", message)
    if m_max:
        max_items = next((g for g in m_max.groups() if g), max_items)

    cmd = [
        "python3", str(GMAIL_TRIAGE_SCRIPT),
        "--account", account,
        "--query", query,
        "--max", str(max_items),
    ]
    if verbose:
        cmd.append("--verbose")
    if any(k in message for k in ["dry-run", "ドライラン", "確認だけ", "一覧だけ"]):
        cmd.append("--dry-run")

    result = {
        "route": ROUTE_GMAIL,
        "command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
        "account": account,
        "query": query,
        "max": int(max_items),
    }
    if execute:
        proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), env=env, capture_output=True, text=True)
        result["executed"] = True
        result["ok"] = proc.returncode == 0
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
        result["returncode"] = proc.returncode
    return result


def handle_notion_sync(env: Dict[str, str], execute: bool, dry_run: bool = False) -> Dict[str, Any]:
    owner = env.get("ROBY_GH_OWNER", "nigoshu-roby")
    project_number = env.get("ROBY_GH_PROJECT_NUMBER", "1")
    page_id = env.get("ROBY_NOTION_SYNC_PAGE_ID", "")
    cmd = [
        "python3", str(NOTION_SYNC_SCRIPT),
        "--owner", owner,
        "--project-number", str(project_number),
    ]
    if page_id:
        cmd.extend(["--page-id", page_id])
    if dry_run:
        cmd.append("--dry-run")

    result: Dict[str, Any] = {
        "route": ROUTE_NOTION_SYNC,
        "command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
    }
    if execute:
        proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), env=env, capture_output=True, text=True)
        result["executed"] = True
        result["ok"] = proc.returncode == 0
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
        result["returncode"] = proc.returncode
    return result


def handle_eval_harness(env: Dict[str, str], execute: bool, verbose: bool) -> Dict[str, Any]:
    cmd = [
        "python3", str(EVAL_HARNESS_SCRIPT),
        "--json",
    ]
    if verbose:
        cmd.append("--verbose")
    result: Dict[str, Any] = {
        "route": ROUTE_EVAL,
        "command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
    }
    if execute:
        proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), env=env, capture_output=True, text=True)
        result["executed"] = True
        result["ok"] = proc.returncode == 0
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
        result["returncode"] = proc.returncode
    return result


def handle_runbook_drill(env: Dict[str, str], execute: bool) -> Dict[str, Any]:
    cmd = [
        "python3", str(DRILL_SCRIPT), "--json",
    ]
    result: Dict[str, Any] = {
        "route": ROUTE_DRILL,
        "command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
    }
    if execute:
        proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), env=env, capture_output=True, text=True)
        result["executed"] = True
        result["ok"] = proc.returncode == 0
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
        result["returncode"] = proc.returncode
    return result


def handle_weekly_report(env: Dict[str, str], execute: bool) -> Dict[str, Any]:
    cmd = [
        "python3", str(WEEKLY_REPORT_SCRIPT), "--json",
    ]
    result: Dict[str, Any] = {
        "route": ROUTE_WEEKLY_REPORT,
        "command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
    }
    if execute:
        proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), env=env, capture_output=True, text=True)
        result["executed"] = True
        result["ok"] = proc.returncode == 0
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
        result["returncode"] = proc.returncode
    return result


def handle_qa_gemini(
    message: str,
    env: Dict[str, str],
    execute: bool,
    qa_overrides: Optional[Dict[str, str]] = None,
    ab_decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    qa_env = dict(env)
    if qa_overrides:
        qa_env.update({k: v for k, v in qa_overrides.items() if v is not None})
    attachment_files = parse_attachment_files(env)
    has_attachments = len(attachment_files) > 0
    ocr_items: List[Dict[str, Any]] = []
    if has_attachments and is_image_text_request(message):
        ocr_items = read_attachment_texts(env)
        success_items = [item for item in ocr_items if item.get("ok") and item.get("text")]
        if success_items:
            return {
                "route": ROUTE_QA,
                "executed": True,
                "ok": True,
                "mode": "local_ocr",
                "attachments_count": len(attachment_files),
                "ocr_success_count": len(success_items),
                "output": format_attachment_text_result(ocr_items),
            }
        return {
            "route": ROUTE_QA,
            "executed": True,
            "ok": True,
            "mode": "local_ocr_failed",
            "attachments_count": len(attachment_files),
            "ocr_success_count": 0,
            "output": (
                "添付画像の文字抽出を試しましたが、テキストを取得できませんでした。\n"
                "- 画像が小さい/ぼけている\n"
                "- コントラストが低い\n"
                "- 手書き/装飾フォント\n"
                "が主な原因です。解像度の高い画像を再送してください。"
            ),
            "ocr_results": ocr_items,
        }

    if is_greeting_request(message):
        return {
            "route": ROUTE_QA,
            "executed": True,
            "ok": True,
            "mode": "local_greeting",
            "output": build_greeting_response(),
        }
    if qa_env.get("ROBY_ORCH_GEMINI_QA_NATIVE", "1") == "1":
        qa_prompt = qa_env.get(
            "ROBY_ORCH_GEMINI_QA_PROMPT",
            (
                "あなたはRobyです。日本語で実務的に回答してください。"
                "ユーザーの目的を先に1-2文で要約し、次に実行可能な提案を優先順で示してください。"
                "相談内容なら、判断基準・推奨案・代替案・次の一手を明示してください。"
                "出力は簡潔に、見出し付きで『結論』『理由』『次のアクション』を基本形としてください。"
                "コーディング実装が必要な相談の場合は、この段階では要件整理と進め方に留めてください。"
            ),
        )
        qa_input = message
        if has_attachments:
            if not ocr_items:
                ocr_items = read_attachment_texts(env)
            if ocr_items:
                ocr_lines = ["\n[添付画像OCR結果]"]
                for item in ocr_items:
                    idx = item.get("index")
                    if item.get("ok") and item.get("text"):
                        text = str(item.get("text", ""))
                        ocr_lines.append(f"画像{idx}:")
                        ocr_lines.append(text[:6000])
                    else:
                        ocr_lines.append(f"画像{idx}: 抽出失敗 ({item.get('error') or 'unknown'})")
                qa_input = f"{message}\n" + "\n".join(ocr_lines)
        qa_input = compact_qa_message(qa_input)
        parsed, raw = run_qa_generation(qa_prompt, qa_input, qa_env)
        text = raw
        if isinstance(parsed, (dict, list)):
            text = json.dumps(parsed, ensure_ascii=False)
        if is_feature_list_request(message) and is_low_detail_output(text):
            text = build_local_capability_summary()
        elif is_broken_qa_output(text, message):
            text = (
                "回答品質が不安定だったため、再質問を推奨します。\n"
                "必要なら以下の形式で指示してください。\n"
                "- 目的:\n"
                "- 前提:\n"
                "- 期待する出力:"
            )
        payload = {
            "route": ROUTE_QA,
            "executed": True,
            "ok": True,
            "mode": "native_gemini",
            "output": text,
        }
        if ab_decision:
            payload["ab_router"] = ab_decision
        return payload

    qa_cmd = qa_env.get("ROBY_ORCH_GEMINI_QA_CMD", "").strip()
    result = {"route": ROUTE_QA, "executed": False}
    if not qa_cmd:
        result["mode"] = "unconfigured"
        result["note"] = "Set ROBY_ORCH_GEMINI_QA_CMD to enable direct Gemini QA execution."
        if ab_decision:
            result["ab_router"] = ab_decision
        return result
    child_env = dict(qa_env)
    child_env["ROBY_ORCH_MESSAGE"] = message
    run = shell_run(qa_cmd, child_env, cwd=OPENCLAW_REPO, timeout=int(qa_env.get("ROBY_ORCH_QA_TIMEOUT_SEC", "600")))
    result.update({"executed": True, **run, "command": qa_cmd})
    if ab_decision:
        result["ab_router"] = ab_decision
    return result


def handle_qa_ollama(
    message: str,
    env: Dict[str, str],
    execute: bool,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "route": ROUTE_QA_LOCAL,
        "mode": "local_ollama",
        "executed": False,
    }
    if not execute:
        result["note"] = "ローカルLLM(Ollama)で回答する準備ができています。"
        return result

    local = run_qa_ollama_local(message, env)
    if local.get("ok"):
        result.update(
            {
                "executed": True,
                "ok": True,
                "output": local.get("output", ""),
                "model": local.get("model", ""),
                "backend": local.get("backend", "ollama_api"),
            }
        )
        return result

    if env.get("ROBY_ORCH_OLLAMA_FALLBACK_QA", "1") == "1":
        fallback = handle_qa_gemini(message, env, execute=True)
        fallback["route"] = ROUTE_QA_LOCAL
        fallback["mode"] = "ollama_fallback_gemini"
        fallback["fallback_reason"] = local.get("error", "unknown")
        fallback["fallback_from"] = "local_ollama"
        return fallback

    result.update(
        {
            "executed": True,
            "ok": False,
            "error": local.get("error", "local_ollama_failed"),
        }
    )
    return result


def handle_coding_codex(message: str, env: Dict[str, str], execute: bool) -> Dict[str, Any]:
    requirements = build_coding_requirements(message, env)
    codex_cmd = env.get("ROBY_ORCH_CODEX_CMD", "").strip()
    result: Dict[str, Any] = {
        "route": ROUTE_CODING,
        "requirements": {k: v for k, v in requirements.items() if not k.startswith("_")},
        "executed": False,
    }
    if not codex_cmd:
        result["mode"] = "handoff_only"
        result["note"] = "Set ROBY_ORCH_CODEX_CMD to enable automatic Codex execution."
        return result
    result["command"] = codex_cmd
    if not execute:
        result["mode"] = "ready"
        return result

    child_env = dict(env)
    child_env["ROBY_ORCH_MESSAGE"] = message
    child_env["ROBY_ORCH_REQUIREMENTS_JSON"] = json.dumps(result["requirements"], ensure_ascii=False)
    run = shell_run(codex_cmd, child_env, cwd=OPENCLAW_REPO, timeout=int(env.get("ROBY_ORCH_CODEX_TIMEOUT_SEC", "3600")))
    fallback_used = False
    if not run.get("ok", False) and "Unknown agent id" in str(run.get("output", "")) and "--agent" in codex_cmd:
        fallback_cmd = _replace_agent_flag(codex_cmd, env.get("ROBY_ORCH_CODEX_FALLBACK_AGENT", "main"))
        fallback_run = shell_run(
            fallback_cmd,
            child_env,
            cwd=OPENCLAW_REPO,
            timeout=int(env.get("ROBY_ORCH_CODEX_TIMEOUT_SEC", "3600")),
        )
        fallback_used = True
        result["fallback_command"] = fallback_cmd
        run = fallback_run
        if not run.get("ok", False) and "Unknown agent id" in str(run.get("output", "")):
            # Final fallback to no agent flag (may still fail, but keeps the error explicit)
            fallback_cmd2 = _drop_agent_flag(codex_cmd)
            fallback_run2 = shell_run(
                fallback_cmd2,
                child_env,
                cwd=OPENCLAW_REPO,
                timeout=int(env.get("ROBY_ORCH_CODEX_TIMEOUT_SEC", "3600")),
            )
            result["fallback_command_2"] = fallback_cmd2
            run = fallback_run2
    result.update({"executed": True, **run, "command": codex_cmd})
    if fallback_used:
        result["fallback_used"] = True
    if run.get("ok", False) and env.get("ROBY_ORCH_AUTO_COMMIT", "1") == "1":
        commit_info = auto_commit_if_dirty(OPENCLAW_REPO, env, result["requirements"].get("objective", message))
        result["auto_commit"] = commit_info
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", default="")
    parser.add_argument("--message-stdin", action="store_true")
    parser.add_argument("--route", choices=["auto", ROUTE_QA, ROUTE_QA_LOCAL, ROUTE_CODING, ROUTE_MINUTES, ROUTE_SELF_GROWTH, ROUTE_GMAIL, ROUTE_NOTION_SYNC, ROUTE_EVAL, ROUTE_DRILL, ROUTE_WEEKLY_REPORT], default="auto")
    parser.add_argument("--cron-task", choices=["self_growth", "minutes_sync", "gmail_triage", "notion_sync", "eval_harness", "runbook_drill", "weekly_report", "none"], default="none")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    env = load_env()
    if args.message_stdin:
        stdin_text = sys.stdin.read()
        if stdin_text:
            args.message = stdin_text.strip()
    started = time.time()
    route = args.route
    classify_meta: Dict[str, Any] = {"method": "manual" if route != "auto" else "heuristic"}

    if args.cron_task != "none":
        args.execute = True
        if args.cron_task == "self_growth":
            route = ROUTE_SELF_GROWTH
            args.message = args.message or "[cron] self-growth"
            classify_meta = {"method": "cron_task", "cron_task": "self_growth"}
        elif args.cron_task == "minutes_sync":
            route = ROUTE_MINUTES
            if not args.message:
                args.message = env.get(
                    "ROBY_ORCH_MINUTES_CRON_MESSAGE",
                    "TOKIWAGIの議事録からタスク抽出してNeuronic連携を実行",
                )
            classify_meta = {"method": "cron_task", "cron_task": "minutes_sync"}
        elif args.cron_task == "gmail_triage":
            route = ROUTE_GMAIL
            if not args.message:
                args.message = env.get(
                    "ROBY_ORCH_GMAIL_CRON_MESSAGE",
                    "Gmailを整理して返信リマインドとNeuronic連携を実行",
                )
            classify_meta = {"method": "cron_task", "cron_task": "gmail_triage"}
        elif args.cron_task == "notion_sync":
            route = ROUTE_NOTION_SYNC
            if not args.message:
                args.message = env.get(
                    "ROBY_ORCH_NOTION_SYNC_CRON_MESSAGE",
                    "GitHub Weekly Focus/DoneをNotionへ同期",
                )
            classify_meta = {"method": "cron_task", "cron_task": "notion_sync"}
        elif args.cron_task == "eval_harness":
            route = ROUTE_EVAL
            if not args.message:
                args.message = env.get(
                    "ROBY_ORCH_EVAL_CRON_MESSAGE",
                    "PBSの評価ハーネスを実行して品質状況を記録",
                )
            classify_meta = {"method": "cron_task", "cron_task": "eval_harness"}
        elif args.cron_task == "runbook_drill":
            route = ROUTE_DRILL
            if not args.message:
                args.message = env.get(
                    "ROBY_ORCH_DRILL_CRON_MESSAGE",
                    "PBSのRunbook Drillを実行して運用健全性を確認",
                )
            classify_meta = {"method": "cron_task", "cron_task": "runbook_drill"}
        elif args.cron_task == "weekly_report":
            route = ROUTE_WEEKLY_REPORT
            if not args.message:
                args.message = env.get(
                    "ROBY_ORCH_WEEKLY_REPORT_CRON_MESSAGE",
                    "PBSの週次運用レポートを生成",
                )
            classify_meta = {"method": "cron_task", "cron_task": "weekly_report"}

    if route == "auto":
        if not args.message.strip():
            print("ERROR: --message is required when --route auto is used.")
            return 2
        route = classify_intent_heuristic(args.message)
        if env.get("ROBY_ORCH_GEMINI_CLASSIFIER", "0") == "1":
            gemini_cls = classify_intent_gemini(args.message, env)
            if gemini_cls:
                route = gemini_cls.get("route", route)
                classify_meta = {
                    "method": "gemini",
                    "reason": gemini_cls.get("reason", ""),
                    "confidence": gemini_cls.get("confidence", None),
                }

    qa_overrides: Dict[str, str] = {}
    ab_decision: Optional[Dict[str, Any]] = None
    if route == ROUTE_QA:
        qa_overrides, ab_decision = pick_ab_router_for_qa(args.message, env)

    if route == ROUTE_MINUTES:
        action = handle_minutes_pipeline(args.message, env, args.execute, args.verbose)
    elif route == ROUTE_GMAIL:
        action = handle_gmail_pipeline(args.message, env, args.execute, args.verbose)
    elif route == ROUTE_NOTION_SYNC:
        msg_low = args.message.lower()
        dry = ("--dry-run" in msg_low) or ("dry-run" in msg_low) or ("ドライラン" in args.message)
        action = handle_notion_sync(env, args.execute, dry_run=dry)
    elif route == ROUTE_EVAL:
        action = handle_eval_harness(env, args.execute, args.verbose)
    elif route == ROUTE_DRILL:
        action = handle_runbook_drill(env, args.execute)
    elif route == ROUTE_WEEKLY_REPORT:
        action = handle_weekly_report(env, args.execute)
    elif route == ROUTE_SELF_GROWTH:
        action = handle_self_growth(env, args.execute)
    elif route == ROUTE_CODING:
        action = handle_coding_codex(args.message, env, args.execute)
    elif route == ROUTE_QA_LOCAL:
        action = handle_qa_ollama(args.message, env, args.execute)
    else:
        action = handle_qa_gemini(
            args.message,
            env,
            args.execute,
            qa_overrides=qa_overrides,
            ab_decision=ab_decision,
        )

    result = {
        "ts": datetime.now(JST).isoformat(),
        "message": args.message,
        "route": route,
        "classify": classify_meta,
        "execute": args.execute,
        "action": action,
        "elapsed_ms": int((time.time() - started) * 1000),
    }
    append_jsonl(RUN_LOG_PATH, result)
    if ab_decision:
        append_jsonl(
            AB_RUN_LOG_PATH,
            {
                "ts": result["ts"],
                "message": args.message,
                "route": route,
                "arm_id": ab_decision.get("arm_id"),
                "label": ab_decision.get("label"),
                "bucket": ab_decision.get("bucket"),
                "total_weight": ab_decision.get("total_weight"),
                "ok": bool(action.get("ok", False)),
                "elapsed_ms": result["elapsed_ms"],
            },
        )
    if bool_from_env(env.get("ROBY_IMMUTABLE_AUDIT", "1"), default=True):
        try:
            append_audit_event(
                "orchestrator.run",
                {
                    "route": route,
                    "execute": bool(args.execute),
                    "elapsed_ms": int(result["elapsed_ms"]),
                    "ok": bool(action.get("ok", False)),
                    "action_route": str(action.get("route", route)),
                    "returncode": action.get("returncode"),
                    "message_preview": (args.message or "")[:240],
                    "classify_method": classify_meta.get("method"),
                    "ab_arm": (ab_decision or {}).get("arm_id"),
                },
                source="roby-orchestrator",
                run_id=str(result["ts"]),
                severity="error" if not bool(action.get("ok", False)) and args.execute else "info",
            )
        except Exception as exc:
            append_jsonl(
                RUN_LOG_PATH,
                {
                    "ts": datetime.now(JST).isoformat(),
                    "route": route,
                    "event": "audit_append_error",
                    "error": str(exc),
                },
            )

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0

    print(f"[orchestrator] route={route} classify={classify_meta.get('method')}")
    if classify_meta.get("reason"):
        print(f"[orchestrator] reason={classify_meta['reason']}")
    if route == ROUTE_CODING:
        req = action.get("requirements", {})
        print(f"[orchestrator] coding objective={req.get('objective', '')}")
        if req.get("acceptance_criteria"):
            print(f"[orchestrator] acceptance_criteria={len(req['acceptance_criteria'])}")
        if req.get("open_questions"):
            print(f"[orchestrator] open_questions={len(req['open_questions'])}")
    if route == ROUTE_SELF_GROWTH:
        print("[orchestrator] self-growth route selected")
    if route == ROUTE_GMAIL:
        print("[orchestrator] gmail triage route selected")
    if route == ROUTE_EVAL:
        print("[orchestrator] evaluation harness route selected")
    if route == ROUTE_DRILL:
        print("[orchestrator] runbook drill route selected")
    if route == ROUTE_WEEKLY_REPORT:
        print("[orchestrator] weekly report route selected")
    if route == ROUTE_QA_LOCAL:
        print("[orchestrator] local ollama qa route selected")
    if ab_decision:
        print(
            f"[orchestrator] ab_router arm={ab_decision.get('arm_id')} "
            f"label={ab_decision.get('label','')}"
        )
    if action.get("command"):
        print(f"[orchestrator] command={action['command']}")
    if action.get("note"):
        print(f"[orchestrator] note={action['note']}")
    if args.execute:
        print(f"[orchestrator] executed={action.get('executed', False)} ok={action.get('ok', False)}")
        if action.get("stdout"):
            print(action["stdout"].rstrip())
        if action.get("stderr"):
            print(action["stderr"].rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
OPENCLAW_REPO = Path(__file__).resolve().parent.parent
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
    "ollamaで回答", "ollamaで返答", "ローカルllm", "local llm", "ローカルで回答", "ローカル回答"
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
    "実行", "取り込み", "抽出して", "タスク化して", "一覧", "list", "--select", "--run",
    "登録", "タスク登録", "登録して", "追加して", "作成して", "入れ子", "親子", "配下", "配列してください",
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

SELF_STATUS_HINTS = [
    "自分の機能", "機能を確認", "機能一覧", "何ができる", "現状把握", "ステータス",
    "ollama導入", "ollama の導入", "ollama導入でき", "neuronic連携", "neuronic 連携",
]
DIRECT_NEURONIC_REGISTER_HINTS = [
    "タスク登録", "登録して", "登録をお願いします", "追加して", "作成して", "入れ子", "親子", "配下", "配列してください",
]
TASK_ADD_HINTS = [
    "タスク追加", "todo追加", "todo add", "task add", "add task",
]

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


def load_env() -> Dict[str, str]:
    env = dict(os.environ)
    env_file = Path(env.get("ROBY_ENV_FILE", str(ENV_PATH))).expanduser()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            # Keep already-exported values (e.g. op run injected secrets).
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


def apply_minutes_llm_profile(env: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
    profile = (env.get("ROBY_ORCH_MINUTES_LLM_PROFILE", "hybrid") or "hybrid").strip().lower()
    local_fast = (env.get("ROBY_ORCH_MINUTES_LOCAL_FAST_MODEL", "ollama/llama3.2:3b") or "").strip()
    local_quality = (env.get("ROBY_ORCH_MINUTES_LOCAL_QUALITY_MODEL", "ollama/qwen2.5:7b") or "").strip()
    cloud = (env.get("ROBY_ORCH_MINUTES_CLOUD_MODEL", env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview")) or "").strip()

    def _csv(*models: str) -> str:
        return ",".join([m for m in models if m])

    overrides: Dict[str, str] = {}
    if profile == "local":
        overrides = {
            "MINUTES_REVIEW_MODELS": _csv(local_quality, local_fast, cloud),
            "MINUTES_TASKS_MODELS": _csv(local_quality, local_fast, cloud),
            "MINUTES_SUMMARY_MODELS": _csv(local_quality, local_fast, cloud),
            "MINUTES_COMPACT_MODELS": _csv(local_fast, local_quality, cloud),
            "MINUTES_REPAIR_MODELS": _csv(local_quality, local_fast, cloud),
            "MINUTES_ENRICH_MODELS": _csv(local_fast, local_quality, cloud),
        }
    elif profile == "cloud":
        overrides = {
            "MINUTES_REVIEW_MODELS": _csv(cloud, local_quality),
            "MINUTES_TASKS_MODELS": _csv(cloud, local_quality),
            "MINUTES_SUMMARY_MODELS": _csv(cloud, local_quality),
            "MINUTES_COMPACT_MODELS": _csv(cloud, local_fast),
            "MINUTES_REPAIR_MODELS": _csv(cloud, local_quality),
            "MINUTES_ENRICH_MODELS": _csv(cloud, local_fast),
        }
    else:  # hybrid
        profile = "hybrid"
        overrides = {
            "MINUTES_REVIEW_MODELS": _csv(local_quality, cloud),
            "MINUTES_TASKS_MODELS": _csv(cloud, local_quality),
            "MINUTES_SUMMARY_MODELS": _csv(cloud, local_quality),
            "MINUTES_COMPACT_MODELS": _csv(local_fast, cloud),
            "MINUTES_REPAIR_MODELS": _csv(cloud, local_quality),
            "MINUTES_ENRICH_MODELS": _csv(local_fast, cloud),
        }
    return profile, overrides


def apply_gmail_profile(env: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
    profile = (env.get("ROBY_ORCH_GMAIL_PROFILE", "fast") or "fast").strip().lower()
    fast_model = (env.get("ROBY_ORCH_GMAIL_LLM_FAST_MODEL", "ollama/llama3.2:3b") or "").strip()
    quality_model = (env.get("ROBY_ORCH_GMAIL_LLM_QUALITY_MODEL", "ollama/qwen2.5:7b") or "").strip()
    overrides: Dict[str, str] = {}
    if profile == "quality":
        overrides = {
            "GMAIL_TRIAGE_LLM_ENABLE": "1",
            "GMAIL_TRIAGE_LLM_MODEL": quality_model or fast_model,
            "GMAIL_TRIAGE_LLM_MAX_REVIEWS": env.get("ROBY_ORCH_GMAIL_LLM_MAX_REVIEWS_QUALITY", "30"),
        }
    elif profile == "hybrid":
        overrides = {
            "GMAIL_TRIAGE_LLM_ENABLE": "1",
            "GMAIL_TRIAGE_LLM_MODEL": fast_model or quality_model,
            "GMAIL_TRIAGE_LLM_MAX_REVIEWS": env.get("ROBY_ORCH_GMAIL_LLM_MAX_REVIEWS_HYBRID", "10"),
        }
    else:  # fast
        profile = "fast"
        overrides = {
            "GMAIL_TRIAGE_LLM_ENABLE": "0",
            "GMAIL_TRIAGE_LLM_MODEL": fast_model or quality_model,
            "GMAIL_TRIAGE_LLM_MAX_REVIEWS": env.get("ROBY_ORCH_GMAIL_LLM_MAX_REVIEWS_FAST", "0"),
        }
    return profile, overrides


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


def build_qa_overrides_from_arm(selected: Dict[str, Any], env: Dict[str, str]) -> Dict[str, str]:
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
    return overrides


def find_arm_by_id(arms: List[Dict[str, Any]], arm_id: str) -> Optional[Dict[str, Any]]:
    target = str(arm_id or "").strip()
    if not target:
        return None
    for arm in arms:
        if not isinstance(arm, dict):
            continue
        if str(arm.get("id", "")).strip() == target:
            return arm
    return None


def read_ab_runs(path: Path, max_rows: int = 400) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    for raw in reversed(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
            if len(rows) >= max_rows:
                break
    rows.reverse()
    return rows


def apply_ab_health_guard(
    selected: Dict[str, Any],
    arms: List[Dict[str, Any]],
    qa_conf: Dict[str, Any],
    env: Dict[str, str],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    guard = qa_conf.get("health_guard")
    if not isinstance(guard, dict):
        return selected, None
    if not bool(guard.get("enabled", False)):
        return selected, None

    fallback_arm_id = str(guard.get("fallback_arm_id", "A")).strip() or "A"
    guarded_arm_ids = guard.get("guarded_arm_ids")
    if not isinstance(guarded_arm_ids, list) or not guarded_arm_ids:
        guarded_arm_ids = ["B"]
    guarded_arm_ids = [str(x).strip() for x in guarded_arm_ids if str(x).strip()]

    selected_id = str(selected.get("id", "")).strip()
    if selected_id not in guarded_arm_ids:
        return selected, None

    window_runs = int(guard.get("window_runs", 50) or 50)
    min_samples = int(guard.get("min_samples", 8) or 8)
    max_fail_rate = float(guard.get("max_fail_rate", 0.15) or 0.15)
    max_avg_elapsed_ms = int(guard.get("max_avg_elapsed_ms", 20000) or 20000)

    rows = read_ab_runs(AB_RUN_LOG_PATH, max_rows=max(window_runs * 3, 120))
    samples = [x for x in rows if str(x.get("arm_id", "")).strip() == selected_id]
    samples = samples[-window_runs:]
    if len(samples) < min_samples:
        return selected, None

    failures = sum(1 for x in samples if not bool(x.get("ok", False)))
    fail_rate = failures / max(len(samples), 1)
    elapsed_vals: List[int] = []
    for row in samples:
        try:
            val = int(row.get("elapsed_ms", 0) or 0)
        except Exception:
            val = 0
        if val > 0:
            elapsed_vals.append(val)
    avg_elapsed = int(sum(elapsed_vals) / len(elapsed_vals)) if elapsed_vals else 0

    fail_degraded = fail_rate > max_fail_rate
    latency_degraded = bool(avg_elapsed and avg_elapsed > max_avg_elapsed_ms)
    if not (fail_degraded or latency_degraded):
        return selected, None

    fallback_arm = find_arm_by_id(arms, fallback_arm_id)
    if not fallback_arm:
        return selected, None

    reasons: List[str] = []
    if fail_degraded:
        reasons.append(f"fail_rate={fail_rate:.3f}>{max_fail_rate:.3f}")
    if latency_degraded:
        reasons.append(f"avg_elapsed_ms={avg_elapsed}>{max_avg_elapsed_ms}")
    guard_meta = {
        "applied": True,
        "requested_arm_id": selected_id,
        "fallback_arm_id": str(fallback_arm.get("id", fallback_arm_id)),
        "samples": len(samples),
        "fail_rate": round(fail_rate, 4),
        "avg_elapsed_ms": avg_elapsed,
        "reason": " / ".join(reasons),
        "window_runs": window_runs,
    }
    return fallback_arm, guard_meta


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

    guarded_selected, guard_meta = apply_ab_health_guard(selected, arms, qa_conf, env)
    overrides = build_qa_overrides_from_arm(guarded_selected, env)

    decision = {
        "enabled": True,
        "route": ROUTE_QA,
        "arm_id": str(guarded_selected.get("id", "unknown")),
        "label": str(guarded_selected.get("label", "")),
        "bucket": bucket,
        "total_weight": total,
        "seed": seed,
        "rotate_daily": rotate_daily,
        "overrides": sorted(list(overrides.keys())),
        "ts": datetime.now(JST).isoformat(),
    }
    if guard_meta:
        decision["guard"] = guard_meta
    return overrides, decision


def extract_latest_user_request(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return ""
    patterns = [
        r"\[ユーザーの最新依頼\]\s*(.*?)\s*(?:上記コンテキストを前提に回答してください。不要な文脈は無視して構いません。)?\s*$",
        r"\[latest user request\]\s*(.*?)\s*$",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        latest = (m.group(1) or "").strip()
        if latest:
            return latest
    return text


def is_direct_neuronic_register_request(message: str) -> bool:
    text = extract_latest_user_request(message)
    lower = text.lower()
    if any(h in text or h in lower for h in TASK_ADD_HINTS):
        return True
    if ("neuronic" not in lower) and ("ニューロニック" not in text):
        return False
    return any(h in text or h in lower for h in DIRECT_NEURONIC_REGISTER_HINTS)


def is_explicit_local_qa_request(message: str) -> bool:
    text = extract_latest_user_request(message).strip()
    lower = text.lower()
    if not text:
        return False
    has_engine = ("ollama" in lower) or ("ローカルllm" in lower) or ("local llm" in lower)
    has_local_intent = any(
        key in text or key in lower
        for key in ["ローカルで回答", "ローカル回答", "ローカルで返答", "ollamaで回答", "ollamaで返答", "localで回答"]
    )
    return has_engine and has_local_intent


def classify_intent_heuristic(message: str) -> str:
    intent_text = extract_latest_user_request(message)
    lower = intent_text.lower()
    if is_direct_neuronic_register_request(intent_text):
        return ROUTE_MINUTES
    if is_self_status_request(intent_text):
        return ROUTE_QA
    if is_explicit_local_qa_request(intent_text) or any(k in lower for k in OLLAMA_HINTS):
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
    if is_direct_neuronic_register_request(intent_text) and not has_consult:
        return ROUTE_MINUTES
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
    intent_text = extract_latest_user_request(message)
    prompt = (
        "Classify the user request for orchestration. Return ONLY JSON object with keys: route, reason, confidence. "
        f"route must be one of: {ROUTE_QA}, {ROUTE_QA_LOCAL}, {ROUTE_CODING}, {ROUTE_MINUTES}, {ROUTE_SELF_GROWTH}, {ROUTE_GMAIL}, {ROUTE_NOTION_SYNC}, {ROUTE_EVAL}, {ROUTE_DRILL}, {ROUTE_WEEKLY_REPORT}."
    )
    parsed, raw = run_summarize_json(prompt, intent_text, env, max_tokens="300", timeout_sec=45)
    if isinstance(parsed, dict) and parsed.get("route") in {ROUTE_QA, ROUTE_QA_LOCAL, ROUTE_CODING, ROUTE_MINUTES, ROUTE_SELF_GROWTH, ROUTE_GMAIL, ROUTE_NOTION_SYNC, ROUTE_EVAL, ROUTE_DRILL, ROUTE_WEEKLY_REPORT}:
        parsed["raw"] = raw
        return parsed
    return None


def is_feature_list_request(message: str) -> bool:
    text = extract_latest_user_request(message)
    lower = text.lower()
    if "機能" in text and ("一覧" in text or "リスト" in text):
        return True
    if "何ができる" in text or "実装済み" in text or "現状" in text:
        return True
    return sum(1 for k in FEATURE_LIST_HINTS if k in lower or k in text) >= 2


def is_self_status_request(message: str) -> bool:
    text = extract_latest_user_request(message).strip()
    lower = text.lower()
    if not text:
        return False
    if any(k in text for k in SELF_STATUS_HINTS):
        return True
    if "ollama" in lower and any(k in text for k in ["導入", "使える", "確認", "状況"]):
        return True
    if "neuronic" in lower and any(k in text for k in ["連携", "状況", "確認", "使える"]):
        return True
    return False


def is_greeting_request(message: str) -> bool:
    normalized = extract_latest_user_request(message).strip().lower()
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


def should_force_detailed_retry(text: str, message: str) -> bool:
    if prefers_short_answer(message):
        return False
    if is_greeting_request(message) or is_self_status_request(message):
        return False
    normalized = (text or "").strip()
    if not normalized:
        return True
    if len(normalized) < 220:
        return True
    required_sections = [
        "## 目的",
        "## 実行可能な提案",
        "## 判断基準",
        "## 推奨案",
        "## 次のアクション",
    ]
    present = sum(1 for sec in required_sections if sec in normalized)
    return present < 3


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


def build_local_capability_summary(env: Optional[Dict[str, str]] = None) -> str:
    runtime_env = env or {}
    has_ollama = shutil.which("ollama") is not None
    checks = [
        ("UIチャット + オーケストレーター表示", OPENCLAW_REPO / "ui" / "src" / "ui" / "controllers" / "chat.ts"),
        ("オーケストレーター本体", OPENCLAW_REPO / "scripts" / "roby-orchestrator.py"),
        ("議事録処理（Notion/GDocs）", MINUTES_SCRIPT),
        ("Gmail仕分け", GMAIL_TRIAGE_SCRIPT),
        ("自己成長ジョブ", SELF_GROWTH_SCRIPT),
        ("GitHub→Notion同期", NOTION_SYNC_SCRIPT),
    ]
    neuronic_url = (
        runtime_env.get("NEURONIC_URL", "").strip()
        or runtime_env.get("ROBY_NEURONIC_URL", "").strip()
        or "http://127.0.0.1:5174/api/v1/tasks/import"
    )
    lines = [
        "## 目的",
        "現在の実装済み機能をローカル実体ベースで一覧化し、運用可否を即確認できる状態にする。",
        "",
        "## 実行可能な提案（優先順）",
        "1. 主要機能の有効/未検出を確認",
        "2. 直近パイプライン実行結果を確認",
        "3. 次アクションを明示して運用判断を即実行",
        "",
        "## 現在の主要機能一覧（ローカル検出）",
    ]
    for label, path in checks:
        status = "有効" if path.exists() else "未検出"
        lines.append(f"- {label}: {status}")

    minutes_last = read_last_jsonl(STATE_DIR / "minutes_runs.jsonl")
    gmail_last = read_last_jsonl(STATE_DIR / "gmail_triage_runs.jsonl")
    self_growth_last = read_last_jsonl(STATE_DIR / "self_growth_runs.jsonl")
    eval_last = read_last_json(STATE_DIR / "evals" / "latest.json")
    drill_last = read_last_json(STATE_DIR / "drills" / "latest.json")

    lines.extend(
        [
            "",
            "## 実行パイプラインの最新状態",
            (
                f"- minutes_sync: tasks={int(((minutes_last or {}).get('summary') or {}).get('tasks', 0))} "
                f"neuronic_errors={int(((minutes_last or {}).get('summary') or {}).get('neuronic_errors', 0))}"
            ),
            (
                f"- gmail_triage: tasks={int(((gmail_last or {}).get('summary') or {}).get('tasks', 0))} "
                f"archived={int(((gmail_last or {}).get('summary') or {}).get('archived', 0))} "
                f"notified={int(((gmail_last or {}).get('summary') or {}).get('notified', 0))}"
            ),
            f"- self_growth: patch_status={str((self_growth_last or {}).get('patch_status', 'unknown'))}",
            "",
            "## 運用品質の最新状態",
            summarize_health_snapshot("evaluation_harness", eval_last),
            summarize_health_snapshot("runbook_drill", drill_last),
            "",
            "## 連携先ステータス",
            f"- Neuronic endpoint: {neuronic_url}",
            f"- Neuronic token: {'設定済み' if bool(runtime_env.get('NEURONIC_TOKEN', '').strip()) else '未設定'}",
        ]
    )

    lines.extend(
        [
            "",
            "## 利用可能ルート",
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
            "",
            "## 次のアクション",
            "1. 実行確認したい機能名を指定してください（例: minutes_pipeline）。",
            "2. 品質確認が必要なら「evaluation_harnessを実行して」と指示してください。",
            "3. 実行が必要なら「実行して」と指示してください。",
        ]
    )
    return "\n".join(lines)


def read_last_jsonl(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return None
        return json.loads(lines[-1])
    except Exception:
        return None


def read_last_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def summarize_health_snapshot(name: str, data: Optional[Dict[str, Any]]) -> str:
    if not data:
        return f"- {name}: 未実行"
    if name == "evaluation_harness":
        gates = data.get("gates") if isinstance(data.get("gates"), dict) else {}
        latency = data.get("latency") if isinstance(data.get("latency"), dict) else {}
        return (
            f"- {name}: gate={'PASS' if gates.get('ok', False) else 'FAIL'} "
            f"failed={int(data.get('failed', 0))}/{int(data.get('total', 0))} "
            f"p95={int(latency.get('p95_ms', 0))}ms"
        )
    if name == "runbook_drill":
        return (
            f"- {name}: all_ok={'YES' if data.get('all_ok', False) else 'NO'} "
            f"failed={int(data.get('failed', 0))}/{int(data.get('total', 0))} "
            f"skipped={int(data.get('skipped', 0))}"
        )
    return f"- {name}: 状態不明"


def build_runtime_status_summary(env: Dict[str, str]) -> str:
    lines: List[str] = ["現在の自己把握ステータス（ローカル検出）"]

    # Ollama status
    ollama_bin = shutil.which("ollama") is not None
    ollama_api_ok = False
    ollama_models: List[str] = []
    base_url = env.get("ROBY_ORCH_OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
    if ollama_bin:
        try:
            req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                body = resp.read().decode("utf-8", "ignore")
                data = json.loads(body) if body else {}
                for m in data.get("models", [])[:5]:
                    name = str(m.get("name", "")).strip()
                    if name:
                        ollama_models.append(name)
                ollama_api_ok = True
        except Exception:
            ollama_api_ok = False

    lines.append(f"- Ollama CLI: {'有効' if ollama_bin else '未導入'}")
    lines.append(f"- Ollama API: {'接続OK' if ollama_api_ok else '未接続'} ({base_url})")
    if ollama_models:
        lines.append(f"- Ollama models: {', '.join(ollama_models)}")

    # Neuronic status (from env + latest run logs)
    neuronic_url = (
        env.get("NEURONIC_URL", "").strip()
        or env.get("ROBY_NEURONIC_URL", "").strip()
        or "http://127.0.0.1:5174/api/v1/tasks/import"
    )
    neuronic_token = bool(env.get("NEURONIC_TOKEN", "").strip())
    lines.append(f"- Neuronic endpoint: {neuronic_url}")
    lines.append(f"- Neuronic token: {'設定済み' if neuronic_token else '未設定'}")

    minutes_last = read_last_jsonl(STATE_DIR / "minutes_runs.jsonl")
    if minutes_last and isinstance(minutes_last.get("summary"), dict):
        s = minutes_last["summary"]
        lines.append(
            "- 直近 minutes_sync: "
            f"tasks={int(s.get('tasks', 0))}, neuronic_errors={int(s.get('neuronic_errors', 0))}, run_id={s.get('run_id', '-')}"
        )
    gmail_last = read_last_jsonl(STATE_DIR / "gmail_triage_runs.jsonl")
    if gmail_last and isinstance(gmail_last.get("summary"), dict):
        s = gmail_last["summary"]
        lines.append(
            "- 直近 gmail_triage: "
            f"tasks={int(s.get('tasks', 0))}, archived={int(s.get('archived', 0))}, notified={int(s.get('notified', 0))}, run_id={s.get('run_id', '-')}"
        )
    eval_last = read_last_json(STATE_DIR / "evals" / "latest.json")
    drill_last = read_last_json(STATE_DIR / "drills" / "latest.json")
    lines.extend(
        [
            summarize_health_snapshot("evaluation_harness", eval_last),
            summarize_health_snapshot("runbook_drill", drill_last),
        ]
    )

    lines.extend(
        [
            "",
            "利用可能ルート",
            f"- {ROUTE_QA}",
            f"- {ROUTE_QA_LOCAL}",
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


def _extract_root_task_title(message: str) -> str:
    text = extract_latest_user_request(message)
    if not text:
        return ""
    patterns = [
        r"「([^」]+)」\s*という?\s*大タスク",
        r"\"([^\"]+)\"\s*という?\s*大タスク",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


def _normalize_hierarchy_title(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(?:タスクカテゴリー|大タスク|小タスク|小小タスク)\s*[：:]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_hierarchical_nodes_from_message(message: str) -> List[Dict[str, Any]]:
    text = extract_latest_user_request(message)
    nodes: List[Dict[str, Any]] = []
    marker_levels = [
        ("■", 1),
        ("◆", 2),
        ("・", 3),
        ("-", 4),
        ("－", 4),
        ("*", 5),
        ("＊", 5),
    ]
    for line_no, raw_line in enumerate(text.splitlines(), 1):
        stripped = raw_line.lstrip(" \t　")
        if not stripped:
            continue
        level = None
        content = ""
        for marker, lv in marker_levels:
            if stripped.startswith(marker):
                level = lv
                content = stripped[len(marker):].strip()
                break
        if level is None:
            continue
        title = _normalize_hierarchy_title(content)
        if not title:
            continue
        nodes.append({"level": level, "title": title, "line_no": line_no})
    return nodes


def _parse_simple_task_entries(message: str) -> List[str]:
    text = extract_latest_user_request(message).strip()
    if not text:
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    cmd_prefix = re.compile(r"^(?:タスク追加|todo追加|todo add|task add|add task)\s*[：:]?\s*", re.IGNORECASE)
    bullet_prefix = re.compile(r"^(?:[-*・●◦▪︎■◆]+|\d+[.)])\s*")
    entries: List[str] = []

    for idx, line in enumerate(lines):
        current = line
        if idx == 0:
            current = cmd_prefix.sub("", current).strip()
            if not current:
                continue
        current = bullet_prefix.sub("", current).strip()
        if current:
            entries.append(current)

    if len(entries) == 1:
        single = entries[0]
        if " / " in single:
            split_vals = [p.strip() for p in single.split(" / ") if p.strip()]
            if len(split_vals) > 1:
                entries = split_vals
        elif "、" in single:
            split_vals = [p.strip() for p in single.split("、") if p.strip()]
            if len(split_vals) > 1:
                entries = split_vals

    deduped: List[str] = []
    seen: set[str] = set()
    for item in entries:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped[:50]


def _build_direct_neuronic_tasks(message: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    root_title = _extract_root_task_title(message).strip()
    if not root_title:
        root_title = "Slack追加タスク" if is_direct_neuronic_register_request(message) else "Robyチャット登録タスク"
    nodes = _parse_hierarchical_nodes_from_message(message)
    if not nodes:
        simple_entries = _parse_simple_task_entries(message)
        if simple_entries:
            nodes = [{"level": 1, "title": item, "line_no": idx + 1} for idx, item in enumerate(simple_entries)]
    if not nodes:
        return [], {"root_title": root_title, "node_count": 0}

    run_id = f"roby:chat:{datetime.now(JST).strftime('%Y%m%d%H%M%S')}"
    source = "roby"
    tasks: List[Dict[str, Any]] = []
    source_doc_title = root_title

    root_path = "0"
    root_seed = f"{root_title}|{root_path}|root"
    root_origin = f"roby:chat:{hashlib.sha1(root_seed.encode('utf-8')).hexdigest()[:16]}"
    root_task = {
        "title": root_title,
        "source": source,
        "origin_id": root_origin,
        "parent_origin_id": None,
        "sibling_order": 0,
        "outline_path": root_path,
        "status": "inbox",
        "priority": 1,
        "run_id": run_id,
        "source_doc_title": source_doc_title,
        "external_ref": "roby:chat",
    }
    tasks.append(root_task)

    stack: List[Dict[str, Any]] = [{"level": 0, "origin_id": root_origin, "path": root_path}]
    sibling_counts: Dict[str, int] = {root_origin: 0}

    for node in nodes:
        level = int(node["level"])
        title = str(node["title"])
        while stack and int(stack[-1]["level"]) >= level:
            stack.pop()
        parent = stack[-1] if stack else {"level": 0, "origin_id": root_origin, "path": root_path}
        parent_origin = str(parent["origin_id"])
        order = int(sibling_counts.get(parent_origin, 0))
        sibling_counts[parent_origin] = order + 1
        path = f"{parent['path']}/{order}"
        seed = f"{root_title}|{path}|{title}"
        origin_id = f"roby:chat:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"
        task = {
            "title": title,
            "source": source,
            "origin_id": origin_id,
            "parent_origin_id": parent_origin,
            "sibling_order": order,
            "outline_path": path,
            "status": "inbox",
            "priority": 1,
            "run_id": run_id,
            "source_doc_title": source_doc_title,
            "external_ref": "roby:chat",
        }
        tasks.append(task)
        stack.append({"level": level, "origin_id": origin_id, "path": path})

    return tasks, {"root_title": root_title, "node_count": len(nodes), "run_id": run_id}


def _send_neuronic_direct_import(tasks: List[Dict[str, Any]], env: Dict[str, str]) -> Dict[str, Any]:
    url = env.get("NEURONIC_URL", "http://127.0.0.1:5174/api/v1/tasks/import")
    fallback_url = env.get("NEURONIC_FALLBACK_URL", "http://127.0.0.1:5174/api/v1/tasks/bulk")
    headers = _neuronic_api_headers(env)

    def _post(items: List[Dict[str, Any]], target_url: str) -> Dict[str, Any]:
        payload = json.dumps({"items": items}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(target_url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8", "ignore")
                body = json.loads(raw) if raw else {}
                return {
                    "ok": 200 <= resp.status < 300,
                    "status_code": int(resp.status),
                    "body": body,
                    "raw": raw,
                    "endpoint": target_url,
                }
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            parsed = {}
            try:
                parsed = json.loads(detail) if detail else {}
            except Exception:
                parsed = {}
            return {
                "ok": False,
                "status_code": int(e.code),
                "body": parsed,
                "raw": detail,
                "endpoint": target_url,
            }
        except Exception as exc:
            return {
                "ok": False,
                "status_code": None,
                "body": {},
                "raw": str(exc),
                "endpoint": target_url,
            }

    batch_size = int_from_env(env.get("NEURONIC_BATCH_SIZE", "25"), 25)
    batch_size = max(1, batch_size)
    queue: List[List[Dict[str, Any]]] = [tasks[i:i + batch_size] for i in range(0, len(tasks), batch_size)]
    aggregate = {
        "ok": True,
        "status_code": 200,
        "endpoint_used": "/api/v1/tasks/import",
        "fallback_used": False,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "error_count": 0,
        "errors": [],
        "hierarchy_applied": None,
        "order_applied": None,
        "detail": "",
    }
    hierarchy_flags: List[bool] = []
    order_flags: List[bool] = []

    while queue:
        current = queue.pop(0)
        first = _post(current, url)
        used_fallback = False
        result = first
        if (not first.get("ok")) and first.get("status_code") == 404 and url.endswith("/tasks/import"):
            result = _post(current, fallback_url)
            used_fallback = True

        if (not result.get("ok")) and result.get("status_code") == 413 and len(current) > 1:
            mid = len(current) // 2
            queue.insert(0, current[mid:])
            queue.insert(0, current[:mid])
            continue

        if not result.get("ok"):
            aggregate["ok"] = False
            aggregate["status_code"] = result.get("status_code")
            aggregate["detail"] = str(result.get("raw") or "")
            break

        body = result.get("body") if isinstance(result.get("body"), dict) else {}
        errors = body.get("errors") if isinstance(body.get("errors"), list) else []
        aggregate["created"] += int(body.get("created", 0) or 0)
        aggregate["updated"] += int(body.get("updated", 0) or 0)
        aggregate["skipped"] += int(body.get("skipped", 0) or 0)
        aggregate["error_count"] += len(errors)
        aggregate["errors"].extend(errors)
        if used_fallback:
            aggregate["fallback_used"] = True
            aggregate["endpoint_used"] = "/api/v1/tasks/bulk"
        if body.get("hierarchy_applied") is not None:
            hierarchy_flags.append(bool(body.get("hierarchy_applied")))
        if body.get("order_applied") is not None:
            order_flags.append(bool(body.get("order_applied")))

    if hierarchy_flags:
        aggregate["hierarchy_applied"] = all(hierarchy_flags)
    if order_flags:
        aggregate["order_applied"] = all(order_flags)
    if aggregate["error_count"] > 0:
        aggregate["ok"] = False
        if not aggregate["detail"]:
            aggregate["detail"] = "neuronic returned item errors"

    return {
        "ok": bool(aggregate.get("ok")),
        "status_code": aggregate.get("status_code"),
        "endpoint_used": aggregate.get("endpoint_used"),
        "fallback_used": bool(aggregate.get("fallback_used")),
        "created": int(aggregate.get("created", 0) or 0),
        "updated": int(aggregate.get("updated", 0) or 0),
        "skipped": int(aggregate.get("skipped", 0) or 0),
        "error_count": int(aggregate.get("error_count", 0) or 0),
        "errors": aggregate.get("errors", []),
        "hierarchy_applied": aggregate.get("hierarchy_applied"),
        "order_applied": aggregate.get("order_applied"),
        "detail": str(aggregate.get("detail") or ""),
    }


def _neuronic_api_headers(env: Dict[str, str]) -> Dict[str, str]:
    token = env.get("NEURONIC_TOKEN", "")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _is_direct_register_managed_task(task: Dict[str, Any], root_title: str) -> bool:
    if not isinstance(task, dict):
        return False
    if str(task.get("source") or "") != "roby":
        return False
    if str(task.get("external_ref") or "") != "roby:chat":
        return False
    return str(task.get("source_doc_title") or "") == str(root_title or "")


def _cleanup_existing_direct_register_tasks(root_title: str, env: Dict[str, str]) -> Dict[str, Any]:
    base = env.get("NEURONIC_API_BASE_URL", "http://127.0.0.1:5174/api/v1").strip().rstrip("/")
    headers = _neuronic_api_headers(env)

    def _list_tasks(params: Dict[str, Any]) -> Dict[str, Any]:
        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{base}/tasks?{query}", headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", "ignore")
                body = json.loads(raw) if raw else {}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "items": []}
        items = body.get("items") if isinstance(body, dict) else []
        if not isinstance(items, list):
            items = []
        return {"ok": True, "items": items}

    limit = 200
    root_ids: List[str] = []
    directly_found_ids: List[str] = []
    offset = 0
    while True:
        listed = _list_tasks({"q": root_title, "limit": limit, "offset": offset})
        if not listed.get("ok"):
            return {"ok": False, "deleted": 0, "error": f"cleanup_list_failed: {listed.get('error')}"}
        items = listed.get("items", [])
        if not items:
            break
        for item in items:
            if _is_direct_register_managed_task(item, root_title):
                tid = str(item.get("id") or "").strip()
                if tid:
                    directly_found_ids.append(tid)
            if (
                str(item.get("source") or "") == "roby"
                and str(item.get("external_ref") or "") == "roby:chat"
                and str(item.get("title") or "") == root_title
            ):
                rid = str(item.get("id") or "").strip()
                if rid:
                    root_ids.append(rid)
        if len(items) < limit:
            break
        offset += limit
        if offset > 10000:
            break

    to_delete: set[str] = set(directly_found_ids)
    queue: List[str] = sorted(set(root_ids))
    seen: set[str] = set()
    while queue:
        parent_id = queue.pop(0)
        if parent_id in seen:
            continue
        seen.add(parent_id)
        to_delete.add(parent_id)
        child_offset = 0
        while True:
            listed = _list_tasks({"parent_id": parent_id, "limit": limit, "offset": child_offset})
            if not listed.get("ok"):
                break
            children = listed.get("items", [])
            if not children:
                break
            for child in children:
                if not _is_direct_register_managed_task(child, root_title):
                    continue
                cid = str(child.get("id") or "").strip()
                if cid:
                    to_delete.add(cid)
                    queue.append(cid)
            if len(children) < limit:
                break
            child_offset += limit
            if child_offset > 10000:
                break

    deleted = 0
    for tid in sorted(to_delete):
        del_req = urllib.request.Request(f"{base}/tasks/{tid}", headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(del_req, timeout=20):
                pass
            deleted += 1
        except Exception:
            # Continue best-effort cleanup to avoid blocking the registration.
            continue
    return {"ok": True, "deleted": deleted}


def handle_neuronic_direct_register(message: str, env: Dict[str, str], execute: bool) -> Dict[str, Any]:
    tasks, meta = _build_direct_neuronic_tasks(message)
    result: Dict[str, Any] = {
        "route": ROUTE_MINUTES,
        "mode": "direct_register",
        "executed": False,
        "root_title": meta.get("root_title", ""),
        "task_count": len(tasks),
        "node_count": int(meta.get("node_count", 0)),
    }
    if not tasks:
        result["ok"] = False
        result["error"] = "no_hierarchical_tasks_detected"
        result["note"] = (
            "タスクを抽出できませんでした。"
            "『タスク追加: タイトル』または箇条書き（■◆・- *）形式で指定してください。"
        )
        return result
    if not execute:
        result["ok"] = True
        result["note"] = "Neuronicへ階層タスク登録を実行する準備ができています。"
        return result

    replace_mode = env.get("ROBY_ORCH_DIRECT_REGISTER_REPLACE", "1") == "1"
    if replace_mode:
        cleanup = _cleanup_existing_direct_register_tasks(str(meta.get("root_title") or ""), env)
        result["replace_mode"] = True
        result["cleanup_deleted"] = int(cleanup.get("deleted", 0) or 0)
        if not cleanup.get("ok"):
            result["cleanup_warning"] = cleanup.get("error", "cleanup_failed")

    sent = _send_neuronic_direct_import(tasks, env)
    result.update(sent)
    result["executed"] = True
    if sent.get("ok"):
        result["output"] = (
            f"Neuronicへ階層タスクを登録しました。"
            f" created={sent.get('created', 0)} updated={sent.get('updated', 0)}"
            f" skipped={sent.get('skipped', 0)}"
        )
    else:
        result["output"] = (
            f"Neuronic登録に失敗しました。 status={sent.get('status_code')} "
            f"endpoint={sent.get('endpoint_used')}"
        )
    return result


def handle_minutes_pipeline(message: str, env: Dict[str, str], execute: bool, verbose: bool) -> Dict[str, Any]:
    intent_text = extract_latest_user_request(message)
    if is_direct_neuronic_register_request(intent_text):
        return handle_neuronic_direct_register(intent_text, env, execute)

    select_match = re.search(r"--select\s+\"([^\"]+)\"|--select\s+'([^']+)'|--select\s+(\S+)", intent_text)
    select_val = None
    if select_match:
        select_val = next((g for g in select_match.groups() if g), None)

    run_mode = "list"
    if any(k in intent_text for k in ["実行", "取り込み", "連携", "Neuronic", "タスク化", "登録", "タスク登録"]):
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

    profile, profile_env = apply_minutes_llm_profile(env)
    child_env = dict(env)
    child_env.update(profile_env)

    result = {
        "route": ROUTE_MINUTES,
        "mode": run_mode,
        "llm_profile": profile,
        "llm_overrides": profile_env,
        "command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
    }
    if execute:
        proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), env=child_env, capture_output=True, text=True)
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

    profile, profile_env = apply_gmail_profile(env)
    child_env = dict(env)
    child_env.update(profile_env)

    result = {
        "route": ROUTE_GMAIL,
        "command": " ".join(shlex.quote(x) for x in cmd),
        "executed": False,
        "llm_profile": profile,
        "llm_overrides": profile_env,
        "account": account,
        "query": query,
        "max": int(max_items),
    }
    if execute:
        proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), env=child_env, capture_output=True, text=True)
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
    if is_self_status_request(message):
        return {
            "route": ROUTE_QA,
            "executed": True,
            "ok": True,
            "mode": "local_status",
            "output": build_runtime_status_summary(qa_env),
        }
    if is_feature_list_request(message) and bool_from_env(
        qa_env.get("ROBY_ORCH_FEATURE_LIST_LOCAL_FIRST", "1"),
        default=True,
    ):
        payload = {
            "route": ROUTE_QA,
            "executed": True,
            "ok": True,
            "mode": "local_capabilities",
            "output": build_local_capability_summary(qa_env),
        }
        if ab_decision:
            payload["ab_router"] = ab_decision
        return payload
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
        if should_force_detailed_retry(text, message):
            detailed_prompt = (
                qa_prompt
                + "\n回答要件: 実務でそのまま使える具体性で、"
                  "『## 目的』『## 実行可能な提案（優先順）』『## 判断基準』『## 推奨案』『## 次のアクション』"
                  "の5見出しを必ず含めてください。提案は最低3件、次のアクションは実行可能な単位で記載してください。"
            )
            d_parsed, d_raw = run_qa_generation(detailed_prompt, qa_input, qa_env)
            d_text = d_raw
            if isinstance(d_parsed, (dict, list)):
                d_text = json.dumps(d_parsed, ensure_ascii=False)
            if d_text and (len(d_text) > len(text) or not should_force_detailed_retry(d_text, message)):
                text = d_text
        if is_feature_list_request(message) and is_low_detail_output(text):
            text = build_local_capability_summary(qa_env)
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

    if is_greeting_request(message):
        result.update(
            {
                "executed": True,
                "ok": True,
                "mode": "local_greeting",
                "output": build_greeting_response(),
            }
        )
        return result

    if is_self_status_request(message):
        result.update(
            {
                "executed": True,
                "ok": True,
                "mode": "local_status",
                "output": build_runtime_status_summary(env),
            }
        )
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
        if env.get("ROBY_ORCH_GEMINI_CLASSIFIER", "0") == "1" and not is_direct_neuronic_register_request(args.message):
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
        guard = ab_decision.get("guard") if isinstance(ab_decision.get("guard"), dict) else {}
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
                "guard_applied": bool(guard.get("applied", False)),
                "requested_arm_id": guard.get("requested_arm_id"),
                "guard_reason": guard.get("reason", ""),
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

#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENV_PATH = Path.home() / ".openclaw" / ".env"
STATE_DIR = Path.home() / ".openclaw" / "roby"
RUN_LOG_PATH = STATE_DIR / "orchestrator_runs.jsonl"
JST = timezone(timedelta(hours=9))
OPENCLAW_REPO = Path("/Users/<user>/OpenClaw")
MINUTES_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-minutes.py"

ROUTE_QA = "qa_gemini"
ROUTE_CODING = "coding_codex"
ROUTE_MINUTES = "minutes_pipeline"

CODING_HINTS = [
    "実装", "修正", "バグ", "テスト", "リファクタ", "コーディング", "コード", "ui", "ux", "画面", "api", "連携", "デプロイ", "再起動", "改善", "追加", "変更"
]
MINUTES_HINTS = [
    "議事録", "notion", "gdocs", "google docs", "googlemeet", "google meet", "タスク抽出", "細分化", "neuronic", "tokiwagi"
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


def classify_intent_heuristic(message: str) -> str:
    lower = message.lower()
    if any(k in lower for k in MINUTES_HINTS):
        return ROUTE_MINUTES
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


def run_summarize_json(prompt: str, text: str, env: Dict[str, str], max_tokens: str = "1800", timeout_sec: int = 120) -> Tuple[Any, str]:
    cmd = [
        "summarize", "-",
        "--json", "--plain",
        "--metrics", "off",
        "--model", env.get("ROBY_ORCH_GEMINI_MODEL", env.get("MINUTES_GEMINI_MODEL", "google/gemini-3-flash-preview")),
        "--length", env.get("ROBY_ORCH_GEMINI_LENGTH", "xl"),
        "--force-summary",
        "--prompt", prompt,
        "--max-output-tokens", max_tokens,
    ]
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
        f"route must be one of: {ROUTE_QA}, {ROUTE_CODING}, {ROUTE_MINUTES}."
    )
    parsed, raw = run_summarize_json(prompt, message, env, max_tokens="300", timeout_sec=45)
    if isinstance(parsed, dict) and parsed.get("route") in {ROUTE_QA, ROUTE_CODING, ROUTE_MINUTES}:
        parsed["raw"] = raw
        return parsed
    return None


def build_coding_requirements(message: str, env: Dict[str, str]) -> Dict[str, Any]:
    prompt = (
        "You are a product/engineering requirements organizer. "
        "Convert the user's request into implementation-ready requirements. "
        "Return ONLY JSON object with keys: objective, scope, constraints, acceptance_criteria, implementation_notes, open_questions. "
        "Keep acceptance_criteria concrete and testable. If no open questions, return an empty array."
    )
    parsed, raw = run_summarize_json(prompt, message, env, max_tokens="1400", timeout_sec=90)
    if isinstance(parsed, dict):
        parsed["_raw"] = raw
        return parsed
    return {
        "objective": message.strip(),
        "scope": [],
        "constraints": [],
        "acceptance_criteria": [],
        "implementation_notes": [],
        "open_questions": [],
        "_raw": raw,
    }


def shell_run(cmd: str, env: Dict[str, str], cwd: Optional[Path] = None, timeout: int = 1800) -> Dict[str, Any]:
    try:
        out = subprocess.check_output(["bash", "-lc", cmd], cwd=str(cwd) if cwd else None, env=env, stderr=subprocess.STDOUT, timeout=timeout)
        return {"ok": True, "output": out.decode("utf-8", "ignore")}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "status": e.returncode, "output": (e.output or b"").decode("utf-8", "ignore")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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


def handle_qa_gemini(message: str, env: Dict[str, str], execute: bool) -> Dict[str, Any]:
    qa_cmd = env.get("ROBY_ORCH_GEMINI_QA_CMD", "").strip()
    result = {"route": ROUTE_QA, "executed": False}
    if not qa_cmd:
        result["mode"] = "unconfigured"
        result["note"] = "Set ROBY_ORCH_GEMINI_QA_CMD to enable direct Gemini QA execution."
        return result
    child_env = dict(env)
    child_env["ROBY_ORCH_MESSAGE"] = message
    run = shell_run(qa_cmd, child_env, cwd=OPENCLAW_REPO, timeout=int(env.get("ROBY_ORCH_QA_TIMEOUT_SEC", "600")))
    result.update({"executed": True, **run, "command": qa_cmd})
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

    child_env = dict(env)
    child_env["ROBY_ORCH_MESSAGE"] = message
    child_env["ROBY_ORCH_REQUIREMENTS_JSON"] = json.dumps(result["requirements"], ensure_ascii=False)
    run = shell_run(codex_cmd, child_env, cwd=OPENCLAW_REPO, timeout=int(env.get("ROBY_ORCH_CODEX_TIMEOUT_SEC", "3600")))
    result.update({"executed": True, **run, "command": codex_cmd})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", required=True)
    parser.add_argument("--route", choices=["auto", ROUTE_QA, ROUTE_CODING, ROUTE_MINUTES], default="auto")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    env = load_env()
    started = time.time()
    route = args.route
    classify_meta: Dict[str, Any] = {"method": "manual" if route != "auto" else "heuristic"}

    if route == "auto":
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

    if route == ROUTE_MINUTES:
        action = handle_minutes_pipeline(args.message, env, args.execute, args.verbose)
    elif route == ROUTE_CODING:
        action = handle_coding_codex(args.message, env, args.execute)
    else:
        action = handle_qa_gemini(args.message, env, args.execute)

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

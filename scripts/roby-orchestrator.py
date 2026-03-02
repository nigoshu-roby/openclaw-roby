#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
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
SELF_GROWTH_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-self-growth.py"
GMAIL_TRIAGE_SCRIPT = OPENCLAW_REPO / "skills" / "roby-mail" / "scripts" / "gmail_triage.py"
NOTION_SYNC_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-notion-sync.py"

ROUTE_QA = "qa_gemini"
ROUTE_CODING = "coding_codex"
ROUTE_MINUTES = "minutes_pipeline"
ROUTE_SELF_GROWTH = "self_growth"
ROUTE_GMAIL = "gmail_pipeline"
ROUTE_NOTION_SYNC = "notion_sync"

CODING_HINTS = [
    "実装", "修正", "バグ", "テスト", "リファクタ", "コーディング", "コード", "ui", "ux", "画面", "api", "連携", "デプロイ", "再起動", "改善", "追加", "変更"
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


def classify_intent_heuristic(message: str) -> str:
    lower = message.lower()
    if any(k in lower for k in SELF_GROWTH_HINTS):
        return ROUTE_SELF_GROWTH
    if any(k in lower for k in NOTION_SYNC_HINTS):
        return ROUTE_NOTION_SYNC
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
        f"route must be one of: {ROUTE_QA}, {ROUTE_CODING}, {ROUTE_MINUTES}, {ROUTE_SELF_GROWTH}, {ROUTE_GMAIL}, {ROUTE_NOTION_SYNC}."
    )
    parsed, raw = run_summarize_json(prompt, message, env, max_tokens="300", timeout_sec=45)
    if isinstance(parsed, dict) and parsed.get("route") in {ROUTE_QA, ROUTE_CODING, ROUTE_MINUTES, ROUTE_SELF_GROWTH, ROUTE_GMAIL, ROUTE_NOTION_SYNC}:
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
            f"- {ROUTE_CODING}",
            f"- {ROUTE_MINUTES}",
            f"- {ROUTE_GMAIL}",
            f"- {ROUTE_SELF_GROWTH}",
            f"- {ROUTE_NOTION_SYNC}",
        ]
    )
    return "\n".join(lines)


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


def run_macos_ocr(image_path: str, timeout_sec: int = 45) -> Dict[str, Any]:
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
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
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
    try:
        proc = subprocess.run(
            ["swift", "-", image_path],
            input=swift_script.encode("utf-8"),
            capture_output=True,
            timeout=timeout_sec,
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


def read_attachment_texts(env: Dict[str, str]) -> List[Dict[str, Any]]:
    files = parse_attachment_files(env)
    outputs: List[Dict[str, Any]] = []
    for item in files:
        path = item.get("path")
        mime = (item.get("mimeType") or "").lower()
        if not path or (mime and not mime.startswith("image/")):
            continue
        ocr = run_macos_ocr(str(path), timeout_sec=int(env.get("ROBY_OCR_TIMEOUT_SEC", "45")))
        text = str(ocr.get("text") or "").strip() if isinstance(ocr, dict) else ""
        outputs.append(
            {
                "index": item.get("index"),
                "path": path,
                "mimeType": item.get("mimeType"),
                "bytes": item.get("bytes"),
                "ok": bool(isinstance(ocr, dict) and ocr.get("ok")),
                "text": text,
                "error": str(ocr.get("error") or "").strip() if isinstance(ocr, dict) else "",
                "line_count": int(ocr.get("line_count") or 0) if isinstance(ocr, dict) else 0,
            }
        )
    return outputs


def format_attachment_text_result(ocr_items: List[Dict[str, Any]]) -> str:
    lines = ["添付画像から抽出したテキストです。"]
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


def handle_qa_gemini(message: str, env: Dict[str, str], execute: bool) -> Dict[str, Any]:
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
    if env.get("ROBY_ORCH_GEMINI_QA_NATIVE", "1") == "1":
        qa_prompt = env.get(
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
        parsed, raw = run_summarize_json(
            qa_prompt,
            qa_input,
            env,
            max_tokens=env.get("ROBY_ORCH_QA_MAX_TOKENS", "2200"),
            timeout_sec=int(env.get("ROBY_ORCH_QA_TIMEOUT_SEC", "600")),
        )
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
        return {
            "route": ROUTE_QA,
            "executed": True,
            "ok": True,
            "mode": "native_gemini",
            "output": text,
        }

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
    parser.add_argument("--route", choices=["auto", ROUTE_QA, ROUTE_CODING, ROUTE_MINUTES, ROUTE_SELF_GROWTH, ROUTE_GMAIL, ROUTE_NOTION_SYNC], default="auto")
    parser.add_argument("--cron-task", choices=["self_growth", "minutes_sync", "gmail_triage", "notion_sync", "none"], default="none")
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

    if route == ROUTE_MINUTES:
        action = handle_minutes_pipeline(args.message, env, args.execute, args.verbose)
    elif route == ROUTE_GMAIL:
        action = handle_gmail_pipeline(args.message, env, args.execute, args.verbose)
    elif route == ROUTE_NOTION_SYNC:
        msg_low = args.message.lower()
        dry = ("--dry-run" in msg_low) or ("dry-run" in msg_low) or ("ドライラン" in args.message)
        action = handle_notion_sync(env, args.execute, dry_run=dry)
    elif route == ROUTE_SELF_GROWTH:
        action = handle_self_growth(env, args.execute)
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
    if route == ROUTE_SELF_GROWTH:
        print("[orchestrator] self-growth route selected")
    if route == ROUTE_GMAIL:
        print("[orchestrator] gmail triage route selected")
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

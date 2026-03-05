#!/usr/bin/env python3
"""PBS Runbook Drill executor.

Runs operational smoke drills and outputs a structured report.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from roby_audit import append_audit_event

JST = timezone(timedelta(hours=9))
OPENCLAW_REPO = Path("/Users/<user>/OpenClaw")
STATE_DIR = Path.home() / ".openclaw" / "roby" / "drills"
LATEST_PATH = STATE_DIR / "latest.json"
HISTORY_PATH = STATE_DIR / "history.jsonl"
LATEST_MD_PATH = STATE_DIR / "latest.md"
ENV_PATH = Path.home() / ".openclaw" / ".env"


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


def send_slack(webhook_url: str, text: str) -> None:
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def run_cmd(cmd: List[str], env: Dict[str, str], timeout: int = 120) -> Dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), env=env, capture_output=True, text=True, timeout=timeout)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "command": " ".join(cmd),
        "returncode": int(proc.returncode),
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
        "elapsed_ms": elapsed_ms,
    }


def _parse_json(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return {}
    return {}


def check_gateway_status(env: Dict[str, str]) -> Dict[str, Any]:
    run = run_cmd(["node", str(OPENCLAW_REPO / "openclaw.mjs"), "gateway", "status"], env, timeout=90)
    ok = run["returncode"] == 0
    return {
        "id": "gateway_status",
        "kind": "required",
        "ok": ok,
        "elapsed_ms": run["elapsed_ms"],
        "detail": "" if ok else (run["stderr"] or run["stdout"] or "gateway status failed"),
        "command": run["command"],
    }


def check_orchestrator_qa(env: Dict[str, str]) -> Dict[str, Any]:
    run = run_cmd(
        [
            "python3",
            str(OPENCLAW_REPO / "scripts" / "roby-orchestrator.py"),
            "--route",
            "qa_gemini",
            "--message",
            "こんにちは",
            "--execute",
            "--json",
        ],
        env,
        timeout=180,
    )
    parsed = _parse_json(run["stdout"])
    ok = run["returncode"] == 0 and bool(parsed.get("action", {}).get("ok", False))
    return {
        "id": "orchestrator_qa_smoke",
        "kind": "required",
        "ok": ok,
        "elapsed_ms": run["elapsed_ms"],
        "detail": "" if ok else (run["stderr"] or run["stdout"] or "orchestrator qa failed"),
        "command": run["command"],
    }


def check_eval_harness(env: Dict[str, str]) -> Dict[str, Any]:
    run = run_cmd(
        [
            "python3",
            str(OPENCLAW_REPO / "scripts" / "roby-eval-harness.py"),
            "--json",
        ],
        env,
        timeout=240,
    )
    parsed = _parse_json(run["stdout"])
    ok = run["returncode"] == 0 and int(parsed.get("total", 0)) > 0
    return {
        "id": "eval_harness_smoke",
        "kind": "required",
        "ok": ok,
        "elapsed_ms": run["elapsed_ms"],
        "detail": "" if ok else (run["stderr"] or run["stdout"] or "eval harness failed"),
        "command": run["command"],
    }


def check_eval_self_awareness_cases(env: Dict[str, str]) -> Dict[str, Any]:
    run = run_cmd(
        [
            "python3",
            str(OPENCLAW_REPO / "scripts" / "roby-eval-harness.py"),
            "--case",
            "qa_local_status_ollama",
            "--case",
            "qa_local_status_neuronic",
            "--case",
            "qa_feature_list_quality",
            "--case",
            "qa_no_prompt_leak_for_detailed_question",
            "--json",
        ],
        env,
        timeout=360,
    )
    parsed = _parse_json(run["stdout"])
    ok = (
        run["returncode"] == 0
        and int(parsed.get("total", 0)) >= 4
        and int(parsed.get("failed", 0)) == 0
        and bool(parsed.get("gates", {}).get("ok", False))
    )
    return {
        "id": "eval_self_awareness_cases",
        "kind": "required",
        "ok": ok,
        "elapsed_ms": run["elapsed_ms"],
        "detail": "" if ok else (run["stderr"] or run["stdout"] or "self-awareness eval cases failed"),
        "command": run["command"],
    }


def check_audit_verify(env: Dict[str, str]) -> Dict[str, Any]:
    run = run_cmd(
        ["python3", str(OPENCLAW_REPO / "scripts" / "roby_audit.py"), "verify", "--json"],
        env,
        timeout=90,
    )
    parsed = _parse_json(run["stdout"])
    ok = run["returncode"] == 0 and bool(parsed.get("ok", False))
    return {
        "id": "audit_verify",
        "kind": "required",
        "ok": ok,
        "elapsed_ms": run["elapsed_ms"],
        "detail": "" if ok else (run["stderr"] or run["stdout"] or "audit verify failed"),
        "command": run["command"],
    }


def check_gmail_dry_run(env: Dict[str, str]) -> Dict[str, Any]:
    account = env.get("GOG_ACCOUNT", "").strip()
    if not account:
        return {
            "id": "gmail_triage_dry_run",
            "kind": "optional",
            "ok": True,
            "skipped": True,
            "elapsed_ms": 0,
            "detail": "GOG_ACCOUNT is not set",
            "command": "",
        }
    run = run_cmd(
        [
            "python3",
            str(OPENCLAW_REPO / "skills" / "roby-mail" / "scripts" / "gmail_triage.py"),
            "--account",
            account,
            "--query",
            "newer_than:1d in:inbox",
            "--max",
            "5",
            "--dry-run",
        ],
        env,
        timeout=240,
    )
    ok = run["returncode"] == 0
    return {
        "id": "gmail_triage_dry_run",
        "kind": "optional",
        "ok": ok,
        "elapsed_ms": run["elapsed_ms"],
        "detail": "" if ok else (run["stderr"] or run["stdout"] or "gmail dry-run failed"),
        "command": run["command"],
    }


def check_notion_sync_dry_run(env: Dict[str, str]) -> Dict[str, Any]:
    notion_token = (
        env.get("NOTION_API_KEY", "").strip()
        or env.get("NOTION_TOKEN", "").strip()
        or env.get("NOTION_KEY", "").strip()
    )
    notion_key_file = Path.home() / ".config" / "notion" / "api_key"
    if not notion_token and not notion_key_file.exists():
        return {
            "id": "notion_sync_dry_run",
            "kind": "optional",
            "ok": True,
            "skipped": True,
            "elapsed_ms": 0,
            "detail": "Notion token is not set",
            "command": "",
        }
    run = run_cmd(
        [
            "python3",
            str(OPENCLAW_REPO / "scripts" / "roby-notion-sync.py"),
            "--dry-run",
        ],
        env,
        timeout=180,
    )
    parsed = _parse_json(run["stdout"])
    ok = (
        run["returncode"] == 0
        and bool(parsed.get("dry_run", False))
        and "phase_counts" in parsed
    )
    return {
        "id": "notion_sync_dry_run",
        "kind": "optional",
        "ok": ok,
        "elapsed_ms": run["elapsed_ms"],
        "detail": "" if ok else (run["stderr"] or run["stdout"] or "notion sync dry-run failed"),
        "command": run["command"],
    }


def check_weekly_report_smoke(env: Dict[str, str]) -> Dict[str, Any]:
    run = run_cmd(
        [
            "python3",
            str(OPENCLAW_REPO / "scripts" / "roby-weekly-report.py"),
            "--json",
        ],
        env,
        timeout=180,
    )
    parsed = _parse_json(run["stdout"])
    ok = (
        run["returncode"] == 0
        and isinstance(parsed.get("eval"), dict)
        and isinstance(parsed.get("drill"), dict)
        and isinstance(parsed.get("audit"), dict)
        and isinstance(parsed.get("ops"), dict)
    )
    return {
        "id": "weekly_report_smoke",
        "kind": "optional",
        "ok": ok,
        "elapsed_ms": run["elapsed_ms"],
        "detail": "" if ok else (run["stderr"] or run["stdout"] or "weekly report smoke failed"),
        "command": run["command"],
    }


def check_orchestrator_cron_status(env: Dict[str, str]) -> Dict[str, Any]:
    require_cron = str(env.get("ROBY_DRILL_REQUIRE_CRON", "0")).strip() == "1"
    kind = "required" if require_cron else "optional"
    started = time.perf_counter()
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "crontab command failed"
        if require_cron:
            return {
                "id": "orchestrator_cron_status",
                "kind": kind,
                "ok": False,
                "elapsed_ms": elapsed_ms,
                "detail": detail,
                "command": "crontab -l",
            }
        return {
            "id": "orchestrator_cron_status",
            "kind": kind,
            "ok": True,
            "skipped": True,
            "elapsed_ms": elapsed_ms,
            "detail": f"cron未設定のためスキップ: {detail}",
            "command": "crontab -l",
        }

    text = proc.stdout or ""
    required_tags = [
        "ROBY_ORCH_CRON_SELF_GROWTH",
        "ROBY_ORCH_CRON_MINUTES_SYNC",
        "ROBY_ORCH_CRON_GMAIL_TRIAGE",
    ]
    optional_tags = [
        "ROBY_ORCH_CRON_EVAL_HARNESS",
        "ROBY_ORCH_CRON_RUNBOOK_DRILL",
        "ROBY_ORCH_CRON_NOTION_SYNC",
        "ROBY_ORCH_CRON_WEEKLY_REPORT",
    ]
    missing_required = [tag for tag in required_tags if tag not in text]
    present_optional = [tag for tag in optional_tags if tag in text]

    ok = len(missing_required) == 0
    detail = ""
    if missing_required:
        detail = "missing required cron tags: " + ", ".join(missing_required)
    else:
        detail = "required cron tags present"
    if present_optional:
        detail += " / optional enabled: " + ", ".join(present_optional)

    return {
        "id": "orchestrator_cron_status",
        "kind": kind,
        "ok": ok,
        "elapsed_ms": elapsed_ms,
        "detail": detail,
        "command": "crontab -l",
    }


def check_minutes_neuronic_regression(env: Dict[str, str]) -> Dict[str, Any]:
    run = run_cmd(
        [
            "python3",
            str(OPENCLAW_REPO / "scripts" / "tests" / "test_roby_minutes_neuronic.py"),
        ],
        env,
        timeout=240,
    )
    ok = run["returncode"] == 0
    return {
        "id": "minutes_neuronic_regression",
        "kind": "required",
        "ok": ok,
        "elapsed_ms": run["elapsed_ms"],
        "detail": "" if ok else (run["stderr"] or run["stdout"] or "minutes neuronic regression failed"),
        "command": run["command"],
    }


def check_gmail_neuronic_regression(env: Dict[str, str]) -> Dict[str, Any]:
    run = run_cmd(
        [
            "python3",
            str(OPENCLAW_REPO / "skills" / "roby-mail" / "scripts" / "test_gmail_triage_neuronic.py"),
        ],
        env,
        timeout=240,
    )
    ok = run["returncode"] == 0
    return {
        "id": "gmail_neuronic_regression",
        "kind": "required",
        "ok": ok,
        "elapsed_ms": run["elapsed_ms"],
        "detail": "" if ok else (run["stderr"] or run["stdout"] or "gmail neuronic regression failed"),
        "command": run["command"],
    }


def check_ollama_health(env: Dict[str, str]) -> Dict[str, Any]:
    require_ollama = str(env.get("ROBY_DRILL_REQUIRE_OLLAMA", "0")).strip() == "1"
    configured_model = env.get("ROBY_ORCH_OLLAMA_MODEL", "qwen2.5:7b").strip()
    base_url = env.get("ROBY_ORCH_OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
    has_cli = shutil.which("ollama") is not None
    kind = "required" if require_ollama else "optional"

    if not has_cli:
        if require_ollama:
            return {
                "id": "ollama_health",
                "kind": kind,
                "ok": False,
                "elapsed_ms": 0,
                "detail": "ollama CLI が見つかりません（ROBY_DRILL_REQUIRE_OLLAMA=1）",
                "command": "ollama --version",
            }
        return {
            "id": "ollama_health",
            "kind": kind,
            "ok": True,
            "skipped": True,
            "elapsed_ms": 0,
            "detail": "ollama CLI 未導入のためスキップ",
            "command": "ollama --version",
        }

    started = time.perf_counter()
    models: List[str] = []
    detail = ""
    ok = False
    try:
        req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8", "ignore")
            parsed = json.loads(body) if body else {}
            for item in parsed.get("models", []):
                name = str(item.get("name", "")).strip()
                if name:
                    models.append(name)
        ok = True
        if configured_model:
            if configured_model in models:
                detail = f"Ollama API接続OK / model={configured_model} 利用可"
            else:
                if require_ollama:
                    ok = False
                    detail = (
                        f"Ollama API接続OKだが configured model 未検出: {configured_model} "
                        f"(available={', '.join(models[:8]) or 'none'})"
                    )
                else:
                    detail = (
                        f"Ollama API接続OKだが configured model 未検出: {configured_model} "
                        f"(available={', '.join(models[:8]) or 'none'})"
                    )
    except Exception as exc:
        ok = False
        detail = f"Ollama API接続失敗: {exc}"
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "id": "ollama_health",
        "kind": kind,
        "ok": ok,
        "elapsed_ms": elapsed_ms,
        "detail": detail,
        "command": f"GET {base_url}/api/tags",
    }


CHECKS = {
    "gateway_status": check_gateway_status,
    "ollama_health": check_ollama_health,
    "orchestrator_qa_smoke": check_orchestrator_qa,
    "eval_harness_smoke": check_eval_harness,
    "eval_self_awareness_cases": check_eval_self_awareness_cases,
    "audit_verify": check_audit_verify,
    "minutes_neuronic_regression": check_minutes_neuronic_regression,
    "gmail_neuronic_regression": check_gmail_neuronic_regression,
    "notion_sync_dry_run": check_notion_sync_dry_run,
    "weekly_report_smoke": check_weekly_report_smoke,
    "orchestrator_cron_status": check_orchestrator_cron_status,
    "gmail_triage_dry_run": check_gmail_dry_run,
}


def build_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# PBS Runbook Drill Report",
        "",
        f"- timestamp: {report['ts']}",
        f"- total: {report['total']}",
        f"- passed: {report['passed']}",
        f"- failed: {report['failed']}",
        f"- skipped: {report['skipped']}",
        f"- all_ok: {report['all_ok']}",
        "",
        "## Checks",
    ]
    for row in report["checks"]:
        if row.get("skipped"):
            status = "SKIP"
        else:
            status = "PASS" if row.get("ok") else "FAIL"
        lines.append(f"- [{status}] {row['id']} ({row.get('kind','')}, {row.get('elapsed_ms',0)}ms)")
        if row.get("detail"):
            lines.append(f"  - {row['detail']}")
    return "\n".join(lines) + "\n"


def write_outputs(report: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    LATEST_MD_PATH.write_text(build_markdown(report), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="append", default=[], help="Run specific check id only")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--soft-fail", action="store_true", help="Always exit 0")
    parser.add_argument("--notify", action="store_true", help="Notify Slack regardless of pass/fail")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = load_env()
    selected = set(args.check or [])
    if selected:
        unknown = sorted(selected - set(CHECKS.keys()))
        if unknown:
            print(json.dumps({"error": f"unknown checks: {','.join(unknown)}"}, ensure_ascii=False))
            return 2
        order = [k for k in CHECKS.keys() if k in selected]
    else:
        order = list(CHECKS.keys())

    rows: List[Dict[str, Any]] = []
    for cid in order:
        row = CHECKS[cid](env)
        rows.append(row)

    passed = sum(1 for x in rows if x.get("ok") and not x.get("skipped"))
    failed = sum(1 for x in rows if not x.get("ok") and not x.get("skipped"))
    skipped = sum(1 for x in rows if x.get("skipped"))
    report = {
        "ts": datetime.now(JST).isoformat(),
        "total": len(rows),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "all_ok": failed == 0,
        "checks": rows,
    }

    slack_error = ""
    webhook = env.get("SLACK_WEBHOOK_URL", "").strip()
    notify_on_pass = str(env.get("ROBY_DRILL_NOTIFY_ON_PASS", "0")).strip() == "1"
    should_notify = bool(webhook) and (args.notify or failed > 0 or notify_on_pass)
    if should_notify:
        failed_checks = [x["id"] for x in rows if (not x.get("ok") and not x.get("skipped"))]
        status = "FAIL" if failed > 0 else "PASS"
        text = (
            f"[PBS Drill] {status}\n"
            f"- ts: {report['ts']}\n"
            f"- total: {report['total']} passed: {report['passed']} failed: {report['failed']} skipped: {report['skipped']}\n"
        )
        if failed_checks:
            text += f"- failed_checks: {', '.join(failed_checks)}\n"
        try:
            send_slack(webhook, text[:3500])
        except Exception as exc:
            slack_error = str(exc)
            report["slack_error"] = slack_error

    write_outputs(report)

    if env.get("ROBY_IMMUTABLE_AUDIT", "1") == "1":
        try:
            append_audit_event(
                "runbook_drill.run",
                {
                    "total": report["total"],
                    "passed": report["passed"],
                    "failed": report["failed"],
                    "skipped": report["skipped"],
                    "all_ok": report["all_ok"],
                    "failed_checks": [x["id"] for x in rows if (not x.get("ok") and not x.get("skipped"))],
                    "notified": bool(should_notify),
                    "slack_error": slack_error,
                },
                source="roby-drill",
                run_id=report["ts"],
                severity="error" if failed > 0 else "info",
            )
        except Exception:
            pass

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(
            f"[drill] total={report['total']} passed={passed} failed={failed} skipped={skipped} all_ok={report['all_ok']}"
        )
        for row in rows:
            if row.get("skipped"):
                status = "SKIP"
            else:
                status = "PASS" if row.get("ok") else "FAIL"
            print(f"- {status} {row['id']} ({row.get('elapsed_ms',0)}ms)")
            if row.get("detail"):
                print(f"  - {row['detail']}")
        print(f"[drill] latest={LATEST_PATH}")
        print(f"[drill] markdown={LATEST_MD_PATH}")

    if args.soft_fail:
        return 0
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

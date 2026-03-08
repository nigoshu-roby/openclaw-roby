#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict
from roby_audit import append_audit_event

REPO_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = Path.home() / ".openclaw" / "roby"
RUNS_LOG = STATE_DIR / "self_growth_runs.jsonl"
ENV_PATH = Path.home() / ".openclaw" / ".env"
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
FAILURE_STATES = {"failed", "invalid", "apply_failed", "agent_failed", "invalid_response"}


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
            if (val.startswith("\"") and val.endswith("\"")) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
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


def run_cmd(cmd, env: Dict[str, str], timeout: int = 60) -> str:
    try:
        out = subprocess.check_output(cmd, env=env, timeout=timeout, stderr=subprocess.STDOUT)
        return out.decode("utf-8", "ignore").strip()
    except subprocess.CalledProcessError as e:
        output = e.output.decode("utf-8", "ignore") if e.output else ""
        return f"[error] exit={e.returncode}\n{output}".strip()
    except Exception as e:
        return f"[error] {e}"


def extract_patch(text: str) -> str:
    if not text:
        return ""
    normalized = text.strip()
    if normalized == "NO_CHANGE" or "```NO_CHANGE```" in normalized or "NO_CHANGE" in normalized:
        return "NO_CHANGE"
    # Unified diff output in fenced block (```diff ... ```)
    fenced = re.search(r"```(?:diff|patch)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
        if "diff --git " in candidate or ("\n--- " in f"\n{candidate}" and "\n+++ " in f"\n{candidate}" and "\n@@ " in f"\n{candidate}"):
            return candidate
    m = re.search(r"(diff --git .*?)$", text, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    # Unified diff without diff header.
    if "\n--- " in f"\n{text}" and "\n+++ " in f"\n{text}" and "\n@@ " in f"\n{text}":
        start = min(idx for idx in [text.find("\n--- "), text.find("--- ")] if idx >= 0)
        return text[start:].strip()
    return ""


def build_agent_cmd(agent_name: str, prompt: str) -> list[str]:
    return [
        "node",
        str(REPO_DIR / "openclaw.mjs"),
        "agent",
        "--local",
        "--agent",
        agent_name.strip() or "main",
        "--message",
        prompt,
        "--timeout",
        "900",
    ]


def tail_file(path: Path, max_lines: int = 50) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


def send_slack(webhook_url: str, text: str) -> None:
    import urllib.request

    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def format_self_growth_slack(
    timestamp: str,
    patch_status: str,
    test_status: str,
    rollback_status: str,
    commit_status: str,
    restart_status: str,
    report: str,
) -> str:
    status = "失敗あり" if has_failures(
        patch_status, test_status, rollback_status, commit_status, restart_status
    ) else "正常"
    lines = [
        "【Roby 自己成長レポート】",
        f"・実行時刻: {timestamp}",
        f"・実行結果: {status}",
        "",
        "■処理ステータス",
        f"・パッチ: {patch_status}",
        f"・テスト: {test_status}",
        f"・ロールバック: {rollback_status}",
        f"・コミット: {commit_status}",
        f"・再起動: {restart_status}",
    ]
    cleaned = [ln.strip() for ln in (report or "").splitlines() if ln.strip()]
    if cleaned:
        lines.extend(["", "■実行ログ（抜粋）"])
        lines.extend(f"・{ln}" for ln in cleaned[:12])
    return "\n".join(lines)


def has_failures(*states: str) -> bool:
    return any(state in FAILURE_STATES for state in states)


def main() -> int:
    env = load_env()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    allow_dirty = env.get("SELF_GROWTH_ALLOW_DIRTY", "0") == "1"
    auto_commit = env.get("SELF_GROWTH_AUTO_COMMIT", "1") == "1"
    test_cmd = env.get("SELF_GROWTH_TEST_CMD", "pnpm -s test:fast")
    test_timeout = int(env.get("SELF_GROWTH_TEST_TIMEOUT", "1200"))
    restart_cmd = env.get(
        "SELF_GROWTH_RESTART_CMD",
        f"node {REPO_DIR / 'openclaw.mjs'} gateway restart",
    )
    agent_name = env.get("SELF_GROWTH_AGENT", "main").strip() or "main"

    git_status = run_cmd(["git", "-C", str(REPO_DIR), "status", "-sb"], env, timeout=30)
    git_dirty = run_cmd(["git", "-C", str(REPO_DIR), "status", "--porcelain"], env, timeout=30)
    git_log = run_cmd(["git", "-C", str(REPO_DIR), "log", "-5", "--oneline"], env, timeout=30)
    gateway_log = tail_file(REPO_DIR / ".openclaw-gateway.log", 80)
    triage_runs = tail_file(Path.home() / ".openclaw" / "roby" / "gmail_triage_runs.jsonl", 10)

    context_parts = [
        f"REPO: {REPO_DIR}",
        f"GIT STATUS:\n{git_status or 'N/A'}",
        f"RECENT COMMITS:\n{git_log or 'N/A'}",
    ]
    if gateway_log:
        context_parts.append(f"GATEWAY LOG TAIL:\n{gateway_log}")
    if triage_runs:
        context_parts.append(f"GMAIL TRIAGE RUNS (tail):\n{triage_runs}")

    context = "\n\n".join(context_parts)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    steps = []
    patch_status = "skipped"
    test_status = "skipped"
    restart_status = "skipped"
    commit_status = "skipped"
    rollback_status = "skipped"

    if git_dirty and not allow_dirty:
        steps.append("SKIP: working tree is dirty (set SELF_GROWTH_ALLOW_DIRTY=1 to override).")
        report = "\n".join(steps)
    else:
        prompt = (
            "You are roby, performing self-growth on the OpenClaw repo. "
            "Return a unified diff patch only. If no changes are needed, return exactly NO_CHANGE.\n\n"
            "Constraints:\n"
            "- Only touch files in this repo.\n"
            "- Keep changes minimal and safe.\n"
            "- Do not modify secrets or auth tokens.\n\n"
            "Context:\n"
            f"{context}"
        )
        agent_cmd = build_agent_cmd(agent_name, prompt)
        raw = run_cmd(agent_cmd, env, timeout=920)
        if raw.startswith("[error]"):
            patch_status = "agent_failed"
            steps.append(f"AGENT: failed\n{raw}")
        else:
            patch = extract_patch(raw)
        if patch_status == "agent_failed":
            pass
        elif patch == "NO_CHANGE":
            patch_status = "no_change"
            steps.append("PATCH: no_change")
        elif not patch:
            patch_status = "invalid_response"
            preview = raw[:800].replace("\r", "")
            steps.append(f"PATCH: invalid_response\n{preview}")
        else:
            patch_path = STATE_DIR / "self_growth.patch"
            patch_path.write_text(patch, encoding="utf-8")
            check = run_cmd(["git", "-C", str(REPO_DIR), "apply", "--check", str(patch_path)], env, timeout=60)
            if check.startswith("[error]"):
                patch_status = "invalid"
                steps.append(f"PATCH: invalid\n{check}")
            else:
                apply_out = run_cmd(["git", "-C", str(REPO_DIR), "apply", str(patch_path)], env, timeout=60)
                if apply_out.startswith("[error]"):
                    patch_status = "apply_failed"
                    steps.append(f"PATCH: apply_failed\n{apply_out}")
                else:
                    patch_status = "applied"
                    steps.append("PATCH: applied")

                    # Tests
                    test_out = run_cmd(["bash", "-lc", test_cmd], env, timeout=test_timeout)
                    if test_out.startswith("[error]") or "FAIL" in test_out:
                        test_status = "failed"
                        steps.append(f"TEST: failed\n{test_out}")
                        # rollback
                        rollback_out = run_cmd(["git", "-C", str(REPO_DIR), "apply", "-R", str(patch_path)], env, timeout=60)
                        if rollback_out.startswith("[error]"):
                            rollback_status = "failed"
                            steps.append(f"ROLLBACK: failed\n{rollback_out}")
                        else:
                            rollback_status = "ok"
                            steps.append("ROLLBACK: ok")
                    else:
                        test_status = "passed"
                        steps.append("TEST: passed")

                        if auto_commit:
                            run_cmd(["git", "-C", str(REPO_DIR), "add", "-A"], env, timeout=60)
                            commit_msg = f"roby self-growth {timestamp}"
                            commit_out = run_cmd(["git", "-C", str(REPO_DIR), "commit", "-m", commit_msg], env, timeout=60)
                            if commit_out.startswith("[error]"):
                                commit_status = "failed"
                                steps.append(f"COMMIT: failed\n{commit_out}")
                            else:
                                commit_status = "ok"
                                steps.append("COMMIT: ok")

                        # Restart
                        restart_out = run_cmd(["bash", "-lc", restart_cmd], env, timeout=90)
                        if restart_out.startswith("[error]"):
                            restart_status = "failed"
                            steps.append(f"RESTART: failed\n{restart_out}")
                        else:
                            restart_status = "ok"
                            steps.append("RESTART: ok")

        report = "\n".join(steps) if steps else "[error] empty report"

    slack_url = env.get("SLACK_WEBHOOK_URL", "").strip()
    slack_text = format_self_growth_slack(
        timestamp=timestamp,
        patch_status=patch_status,
        test_status=test_status,
        rollback_status=rollback_status,
        commit_status=commit_status,
        restart_status=restart_status,
        report=report,
    )
    if slack_url:
        try:
            send_slack(slack_url, slack_text[:3800])
        except Exception as e:
            report = f"{report}\n\n[slack_error] {e}"

    entry = {
        "ts": int(time.time()),
        "timestamp": timestamp,
        "git_status": git_status,
        "patch_status": patch_status,
        "test_status": test_status,
        "rollback_status": rollback_status,
        "commit_status": commit_status,
        "restart_status": restart_status,
        "report": report,
    }
    with RUNS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if env.get("ROBY_IMMUTABLE_AUDIT", "1") == "1":
        try:
            append_audit_event(
                "self_growth.run",
                {
                    "patch_status": patch_status,
                    "test_status": test_status,
                    "rollback_status": rollback_status,
                    "commit_status": commit_status,
                    "restart_status": restart_status,
                    "report_preview": report[:300],
                },
                source="roby-self-growth",
                run_id=timestamp,
                severity="error" if has_failures(
                    patch_status, test_status, rollback_status, commit_status, restart_status
                ) else "info",
            )
        except Exception:
            pass

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

DEFAULT_ENV_FILE = str(Path.home() / ".openclaw" / ".env")
DEFAULT_ORCH = "/Users/shu/OpenClaw/scripts/roby-orchestrator.py"


def load_env_file(path: str = DEFAULT_ENV_FILE) -> None:
    p = Path(path).expanduser()
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = k.strip()
        val = v.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        os.environ.setdefault(key, val)


def run_shell(cmd: str, extra_env: dict[str, str], timeout: int) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.update(extra_env)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env, timeout=timeout)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def run_exec(argv: list[str], extra_env: dict[str, str], timeout: int) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.update(extra_env)
    p = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def summarize_orchestrator_json(raw: str) -> str:
    try:
        data = json.loads(raw)
    except Exception:
        return raw[:3000] if raw else ""

    action = data.get("action") or {}
    for key in ("output", "stdout"):
        value = action.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:3000]
    reason = action.get("reason") or data.get("route") or "処理結果を取得できませんでした。"
    return str(reason)[:3000]


def run_ask(text: str, shared_env: dict[str, str]) -> str:
    ask_cmd = os.getenv("ROBY_ORCH_GEMINI_QA_CMD", "").strip()
    if ask_cmd:
        code, out, err = run_shell(ask_cmd, shared_env, timeout=180)
        if code == 0 and out:
            return out[:3000]
        return f"相談処理でエラーが発生しました: {err or out or f'exit={code}'}"

    orch = os.getenv("ROBY_ORCH_FALLBACK", DEFAULT_ORCH).strip() or DEFAULT_ORCH
    if Path(orch).exists():
        code, out, err = run_exec(["python3", orch, "--message", text, "--execute", "--json"], shared_env, timeout=240)
        if code == 0 and out:
            summary = summarize_orchestrator_json(out)
            if summary:
                return summary
        return f"相談処理でエラーが発生しました: {err or out or f'exit={code}'}"

    return (
        "相談ありがとう。現在 ask の実行コマンドが未設定なので、まずは方針だけ返すね。\n"
        "- 目的を1行で定義\n"
        "- 成果物を明確化\n"
        "- 優先順位を3段階で決める\n"
        "必要なら ROBY_ORCH_GEMINI_QA_CMD を設定して自動回答を有効化できます。"
    )


def run_dev(text: str, shared_env: dict[str, str]) -> str:
    dev_cmd = os.getenv("ROBY_ORCH_CODEX_CMD", "").strip()
    if dev_cmd:
        code, out, err = run_shell(dev_cmd, shared_env, timeout=1800)
        if code == 0 and out:
            return out[:3000]
        return f"dev実行でエラーが発生しました: {err or out or f'exit={code}'}"

    orch = os.getenv("ROBY_ORCH_FALLBACK", DEFAULT_ORCH).strip() or DEFAULT_ORCH
    if Path(orch).exists():
        code, out, err = run_exec(["python3", orch, "--message", text, "--execute", "--json"], shared_env, timeout=1800)
        if code == 0 and out:
            summary = summarize_orchestrator_json(out)
            if summary:
                return summary
        return f"dev実行でエラーが発生しました: {err or out or f'exit={code}'}"

    return (
        "dev指示を受領しました。現在 ROBY_ORCH_CODEX_CMD が未設定のため実行できません。\n"
        "環境変数を設定すると、ここから実装フローに接続できます。"
    )


def main() -> int:
    load_env_file()

    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["ask", "dev"], required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--channel", default="")
    ap.add_argument("--thread", default="")
    ap.add_argument("--user", default="")
    args = ap.parse_args()

    text = args.text.strip()
    if not text:
        print("内容が空です。")
        return 0

    shared_env = {
        "ROBY_ORCH_MESSAGE": text,
        "ROBY_ORCH_CHANNEL": args.channel,
        "ROBY_ORCH_THREAD": args.thread,
        "ROBY_ORCH_USER": args.user,
    }

    if args.mode == "ask":
        print(run_ask(text, shared_env)[:3000])
        return 0

    print(run_dev(text, shared_env)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

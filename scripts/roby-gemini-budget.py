#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable

DEFAULT_OUTPUT_TOKENS = 4000
DEFAULT_SOFT_LIMIT = 200_000
DEFAULT_HARD_LIMIT = 800_000


def env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def estimate_tokens(text: str) -> int:
    # Gemini exact tokenizer is not available locally; use a conservative heuristic.
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def iter_text_inputs(paths: Iterable[str]) -> list[dict]:
    records: list[dict] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        records.append(
            {
                "path": str(path),
                "chars": len(text),
                "estimated_tokens": estimate_tokens(text),
            }
        )
    return records


def build_summary(label: str, files: list[dict], output_tokens: int, soft_limit: int, hard_limit: int) -> dict:
    input_tokens = sum(int(item["estimated_tokens"]) for item in files)
    total_chars = sum(int(item["chars"]) for item in files)
    estimated_total = input_tokens + output_tokens
    if estimated_total >= hard_limit:
        decision = "blocked"
    elif estimated_total >= soft_limit:
        decision = "confirm_required"
    else:
        decision = "ok"
    return {
        "label": label,
        "file_count": len(files),
        "total_chars": total_chars,
        "estimated_input_tokens": input_tokens,
        "assumed_output_tokens": output_tokens,
        "estimated_total_tokens": estimated_total,
        "soft_limit_tokens": soft_limit,
        "hard_limit_tokens": hard_limit,
        "decision": decision,
        "files": files,
    }


def render_text(summary: dict) -> str:
    lines = [
        f"[gemini-budget] {summary['label']}",
        f"- files: {summary['file_count']}",
        f"- chars: {summary['total_chars']}",
        f"- estimated input tokens: {summary['estimated_input_tokens']}",
        f"- assumed output tokens: {summary['assumed_output_tokens']}",
        f"- estimated total tokens: {summary['estimated_total_tokens']}",
        f"- decision: {summary['decision']}",
    ]
    if summary["decision"] != "ok":
        lines.append("- note: Gemini本処理の前に確認が必要です。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate Gemini token budget before large PBS runs")
    parser.add_argument("--label", default="gemini-bulk-run")
    parser.add_argument("--input-file", action="append", default=[])
    parser.add_argument(
        "--output-tokens",
        type=int,
        default=env_int("ROBY_GEMINI_BUDGET_OUTPUT_TOKENS", DEFAULT_OUTPUT_TOKENS),
    )
    parser.add_argument(
        "--soft-limit",
        type=int,
        default=env_int("ROBY_GEMINI_BUDGET_SOFT_LIMIT", DEFAULT_SOFT_LIMIT),
    )
    parser.add_argument(
        "--hard-limit",
        type=int,
        default=env_int("ROBY_GEMINI_BUDGET_HARD_LIMIT", DEFAULT_HARD_LIMIT),
    )
    parser.add_argument("--approve", action="store_true", help="Acknowledge confirm_required and continue with exit code 0")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    files = iter_text_inputs(args.input_file)
    summary = build_summary(args.label, files, args.output_tokens, args.soft_limit, args.hard_limit)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(render_text(summary))

    if summary["decision"] == "ok":
        return 0
    if summary["decision"] == "confirm_required":
        return 0 if args.approve else 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())

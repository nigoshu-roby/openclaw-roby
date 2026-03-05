#!/usr/bin/env python3
"""PBS Evaluation Harness (production-hardened).

Runs predefined orchestrator test cases, applies expectation checks, and enforces
policy gates (failure-rate / latency / regression drift).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from roby_audit import append_audit_event

JST = timezone(timedelta(hours=9))
OPENCLAW_REPO = Path("/Users/<user>/OpenClaw")
ORCH_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-orchestrator.py"
DEFAULT_CASES_PATH = OPENCLAW_REPO / "config" / "pbs" / "eval_cases.json"
DEFAULT_POLICY_PATH = OPENCLAW_REPO / "config" / "pbs" / "eval_policy.json"
STATE_DIR = Path.home() / ".openclaw" / "roby" / "evals"
LATEST_PATH = STATE_DIR / "latest.json"
HISTORY_PATH = STATE_DIR / "history.jsonl"
LATEST_MD_PATH = STATE_DIR / "latest.md"


@dataclass
class EvalCase:
    id: str
    description: str
    message: str
    route: str = "auto"
    execute: bool = False
    expect: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalPolicy:
    max_failed_cases: int = 0
    max_failure_rate: float = 0.0
    allow_new_failures: int = 0
    max_p95_ms: int = 20000
    max_avg_ms: int = 12000
    max_retries: int = 1
    retry_delay_ms: int = 700
    transient_exit_codes: List[int] = field(default_factory=lambda: [2])
    transient_failure_markers: List[str] = field(
        default_factory=lambda: [
            "timed out",
            "timeout",
            "temporary",
            "service unavailable",
            "connection reset",
            "connection refused",
            "send failed",
            "送信に失敗",
            "rate limit",
            "too many requests",
            "429",
            "502",
            "503",
            "504",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--max-retries", type=int, default=None, help="Override policy max_retries")
    parser.add_argument("--retry-delay-ms", type=int, default=None, help="Override policy retry_delay_ms")
    parser.add_argument("--write-markdown", default=str(LATEST_MD_PATH))
    parser.add_argument("--skip-gates", action="store_true", help="Only evaluate checks without policy gating")
    parser.add_argument("--soft-fail", action="store_true", help="Always exit 0")
    return parser.parse_args()


def load_json_file(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"json file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(path: Path) -> List[EvalCase]:
    data = load_json_file(path)
    if not isinstance(data, list):
        raise ValueError("cases file must be a JSON array")
    out: List[EvalCase] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        case_id = str(raw.get("id") or "").strip()
        message = str(raw.get("message") or "").strip()
        if not case_id or not message:
            continue
        out.append(
            EvalCase(
                id=case_id,
                description=str(raw.get("description") or "").strip(),
                message=message,
                route=str(raw.get("route") or "auto").strip() or "auto",
                execute=bool(raw.get("execute", False)),
                expect=raw.get("expect") if isinstance(raw.get("expect"), dict) else {},
            )
        )
    return out


def load_policy(path: Path) -> EvalPolicy:
    if not path.exists():
        return EvalPolicy()
    raw = load_json_file(path)
    if not isinstance(raw, dict):
        raise ValueError("policy file must be a JSON object")
    policy = EvalPolicy()
    for key, value in raw.items():
        if not hasattr(policy, key):
            continue
        setattr(policy, key, value)
    policy.max_failed_cases = int(policy.max_failed_cases)
    policy.max_failure_rate = float(policy.max_failure_rate)
    policy.allow_new_failures = int(policy.allow_new_failures)
    policy.max_p95_ms = int(policy.max_p95_ms)
    policy.max_avg_ms = int(policy.max_avg_ms)
    policy.max_retries = int(policy.max_retries)
    policy.retry_delay_ms = int(policy.retry_delay_ms)
    policy.transient_exit_codes = [int(x) for x in policy.transient_exit_codes]
    policy.transient_failure_markers = [str(x).lower() for x in policy.transient_failure_markers]
    return policy


def get_by_path(data: Any, dotted: str) -> Any:
    cur = data
    if not dotted:
        return cur
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def run_orchestrator(case: EvalCase) -> Dict[str, Any]:
    cmd = [
        "python3",
        str(ORCH_SCRIPT),
        "--message",
        case.message,
        "--route",
        case.route,
        "--json",
    ]
    if case.execute:
        cmd.append("--execute")
    started = time.perf_counter()
    child_env = dict(os.environ)
    # Keep evaluation deterministic and low-variance:
    # disable AB routing during harness runs.
    child_env["ROBY_ORCH_AB_ROUTER"] = "0"
    proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), capture_output=True, text=True, env=child_env)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    parsed: Dict[str, Any] = {}
    parse_error = ""
    if stdout:
        try:
            parsed = json.loads(stdout)
        except Exception as exc:  # pragma: no cover - defensive guard
            parsed = {"_raw_stdout": stdout}
            parse_error = f"json_parse_error: {exc}"
    return {
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "parsed": parsed,
        "parse_error": parse_error,
        "elapsed_ms": elapsed_ms,
    }


def check_equals(parsed: Dict[str, Any], rules: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    for path, expected in rules.items():
        actual = get_by_path(parsed, path)
        if actual != expected:
            failures.append(f"equals: {path} expected={expected!r} actual={actual!r}")
    return failures


def check_contains(parsed: Dict[str, Any], rules: Dict[str, List[str]]) -> List[str]:
    failures: List[str] = []
    for path, needles in rules.items():
        actual = get_by_path(parsed, path)
        text = "" if actual is None else str(actual)
        for needle in needles:
            if needle not in text:
                failures.append(f"contains: {path} missing={needle!r}")
    return failures


def check_not_contains(parsed: Dict[str, Any], rules: Dict[str, List[str]]) -> List[str]:
    failures: List[str] = []
    for path, needles in rules.items():
        actual = get_by_path(parsed, path)
        text = "" if actual is None else str(actual)
        for needle in needles:
            if needle in text:
                failures.append(f"not_contains: {path} found_forbidden={needle!r}")
    return failures


def check_min_len(parsed: Dict[str, Any], rules: Dict[str, int]) -> List[str]:
    failures: List[str] = []
    for path, min_len in rules.items():
        actual = get_by_path(parsed, path)
        text = "" if actual is None else str(actual)
        if len(text) < int(min_len):
            failures.append(f"min_len: {path} min={min_len} actual={len(text)}")
    return failures


def evaluate_expectations(parsed: Dict[str, Any], expect: Dict[str, Any]) -> List[str]:
    equals_rules = expect.get("equals") if isinstance(expect.get("equals"), dict) else {}
    contains_rules = expect.get("contains") if isinstance(expect.get("contains"), dict) else {}
    not_contains_rules = expect.get("not_contains") if isinstance(expect.get("not_contains"), dict) else {}
    min_len_rules = expect.get("min_len") if isinstance(expect.get("min_len"), dict) else {}
    failures: List[str] = []
    failures.extend(check_equals(parsed, equals_rules))
    failures.extend(check_contains(parsed, contains_rules))
    failures.extend(check_not_contains(parsed, not_contains_rules))
    failures.extend(check_min_len(parsed, min_len_rules))
    return failures


def extract_retry_signal(attempt: Dict[str, Any], policy: EvalPolicy) -> bool:
    if int(attempt.get("returncode", 0)) in set(policy.transient_exit_codes):
        return True
    blob = " ".join(
        [
            str(attempt.get("stdout") or ""),
            str(attempt.get("stderr") or ""),
            str(attempt.get("parse_error") or ""),
        ]
    ).lower()
    if not blob.strip():
        return False
    return any(marker in blob for marker in policy.transient_failure_markers)


def evaluate_case(case: EvalCase, policy: EvalPolicy) -> Dict[str, Any]:
    max_attempts = max(int(policy.max_retries), 0) + 1
    attempts: List[Dict[str, Any]] = []
    final_failures: List[str] = []
    final_result: Dict[str, Any] = {}
    transient_retry_used = 0

    for idx in range(max_attempts):
        attempt = run_orchestrator(case)
        failure_reasons: List[str] = []
        if int(attempt["returncode"]) != 0:
            failure_reasons.append(f"orchestrator_exit: {attempt['returncode']}")
        if attempt.get("parse_error"):
            failure_reasons.append(str(attempt["parse_error"]))
        failure_reasons.extend(evaluate_expectations(attempt["parsed"], case.expect or {}))
        attempt["failures"] = failure_reasons
        attempts.append(
            {
                "attempt": idx + 1,
                "elapsed_ms": attempt["elapsed_ms"],
                "returncode": attempt["returncode"],
                "parse_error": attempt.get("parse_error", ""),
                "failures": failure_reasons,
            }
        )

        final_failures = failure_reasons
        final_result = attempt
        if not failure_reasons:
            break

        should_retry = idx < (max_attempts - 1) and (
            int(attempt["returncode"]) != 0 or bool(attempt.get("parse_error"))
        )
        if should_retry and extract_retry_signal(attempt, policy):
            transient_retry_used += 1
            time.sleep(max(policy.retry_delay_ms, 0) / 1000.0)
            continue
        break

    parsed = final_result.get("parsed", {})
    output_text = str(get_by_path(parsed, "action.output") or "")
    message_text = case.message or ""
    return {
        "id": case.id,
        "description": case.description,
        "route": case.route,
        "execute": case.execute,
        "ok": len(final_failures) == 0,
        "failures": final_failures,
        "attempts": attempts,
        "attempt_count": len(attempts),
        "transient_retry_used": transient_retry_used,
        "orchestrator_returncode": final_result.get("returncode", 1),
        "elapsed_ms": int(final_result.get("elapsed_ms", 0)),
        "result": parsed,
        "raw_stdout": final_result.get("stdout", "") if not parsed else "",
        "raw_stderr": final_result.get("stderr", ""),
        "output_len": len(output_text),
        "message_len": len(message_text),
    }


def p95_ms(values: List[int]) -> int:
    if not values:
        return 0
    ordered = sorted(int(v) for v in values)
    idx = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return ordered[idx]


def avg_ms(values: List[int]) -> int:
    if not values:
        return 0
    return int(sum(values) / len(values))


def load_previous_latest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def evaluate_gates(report: Dict[str, Any], previous: Dict[str, Any], policy: EvalPolicy, skip_gates: bool) -> Dict[str, Any]:
    failed_ids = [row["id"] for row in report["results"] if not row.get("ok")]
    prev_failed_ids = [row.get("id") for row in previous.get("results", []) if isinstance(row, dict) and not row.get("ok")]
    prev_failed_set = {x for x in prev_failed_ids if isinstance(x, str)}
    failed_set = set(failed_ids)
    new_failures = sorted(failed_set - prev_failed_set)
    resolved_failures = sorted(prev_failed_set - failed_set)

    failures: List[str] = []
    if not skip_gates:
        if report["failed"] > policy.max_failed_cases:
            failures.append(
                f"gate:max_failed_cases exceeded actual={report['failed']} limit={policy.max_failed_cases}"
            )
        if report["failure_rate"] > policy.max_failure_rate:
            failures.append(
                f"gate:max_failure_rate exceeded actual={report['failure_rate']:.3f} limit={policy.max_failure_rate:.3f}"
            )
        if policy.max_p95_ms > 0 and report["latency"]["p95_ms"] > policy.max_p95_ms:
            failures.append(
                f"gate:max_p95_ms exceeded actual={report['latency']['p95_ms']} limit={policy.max_p95_ms}"
            )
        if policy.max_avg_ms > 0 and report["latency"]["avg_ms"] > policy.max_avg_ms:
            failures.append(
                f"gate:max_avg_ms exceeded actual={report['latency']['avg_ms']} limit={policy.max_avg_ms}"
            )
        if len(new_failures) > policy.allow_new_failures:
            failures.append(
                f"gate:allow_new_failures exceeded actual={len(new_failures)} limit={policy.allow_new_failures}"
            )

    return {
        "ok": len(failures) == 0,
        "skip_gates": bool(skip_gates),
        "failures": failures,
        "new_failures": new_failures,
        "resolved_failures": resolved_failures,
        "previous_failed_count": len(prev_failed_set),
    }


def build_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# PBS Evaluation Harness Report")
    lines.append("")
    lines.append(f"- timestamp: {report['ts']}")
    lines.append(f"- cases: {report['total']} (passed={report['passed']}, failed={report['failed']})")
    lines.append(f"- failure_rate: {report['failure_rate']:.3f}")
    lines.append(
        f"- latency(ms): avg={report['latency']['avg_ms']} p95={report['latency']['p95_ms']}"
    )
    lines.append(
        f"- retries: total={report['retries']['total']} cases_with_retry={report['retries']['cases_with_retry']}"
    )
    lines.append(f"- gate: {'PASS' if report['gates']['ok'] else 'FAIL'}")
    if report["gates"]["new_failures"]:
        lines.append(f"- new_failures: {', '.join(report['gates']['new_failures'])}")
    if report["gates"]["resolved_failures"]:
        lines.append(f"- resolved_failures: {', '.join(report['gates']['resolved_failures'])}")
    lines.append("")
    lines.append("## Case Results")
    for row in report["results"]:
        status = "PASS" if row.get("ok") else "FAIL"
        lines.append(
            f"- [{status}] {row['id']} elapsed={row.get('elapsed_ms',0)}ms attempts={row.get('attempt_count',1)}"
        )
        if row.get("failures"):
            for reason in row["failures"]:
                lines.append(f"  - {reason}")
    if report["gates"]["failures"]:
        lines.append("")
        lines.append("## Gate Failures")
        for reason in report["gates"]["failures"]:
            lines.append(f"- {reason}")
    lines.append("")
    return "\n".join(lines)


def write_outputs(report: Dict[str, Any], markdown_path: Optional[Path]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    if markdown_path:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(build_markdown(report), encoding="utf-8")


def summarize_routes(results: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    stats: Dict[str, Dict[str, int]] = {}
    for row in results:
        route = str(get_by_path(row, "result.route") or row.get("route") or "unknown")
        bucket = stats.setdefault(route, {"total": 0, "passed": 0, "failed": 0})
        bucket["total"] += 1
        if row.get("ok"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
    return stats


def main() -> int:
    args = parse_args()
    cases_path = Path(args.cases)
    policy_path = Path(args.policy)
    all_cases = load_cases(cases_path)
    policy = load_policy(policy_path)

    if args.max_retries is not None:
        policy.max_retries = max(args.max_retries, 0)
    if args.retry_delay_ms is not None:
        policy.retry_delay_ms = max(args.retry_delay_ms, 0)

    selected = set(args.case or [])
    if selected:
        cases = [c for c in all_cases if c.id in selected]
    else:
        cases = all_cases

    if not cases:
        print(json.dumps({"error": "no cases selected"}, ensure_ascii=False))
        return 2

    started = datetime.now(JST)
    previous = load_previous_latest(LATEST_PATH)
    case_results = [evaluate_case(case, policy) for case in cases]
    passed = sum(1 for r in case_results if r.get("ok"))
    failed = len(case_results) - passed
    elapsed_values = [int(r.get("elapsed_ms", 0)) for r in case_results]
    retries_total = sum(int(r.get("transient_retry_used", 0)) for r in case_results)
    cases_with_retry = sum(1 for r in case_results if int(r.get("transient_retry_used", 0)) > 0)
    report: Dict[str, Any] = {
        "ts": started.isoformat(),
        "cases_path": str(cases_path),
        "policy_path": str(policy_path),
        "policy": {
            "max_failed_cases": policy.max_failed_cases,
            "max_failure_rate": policy.max_failure_rate,
            "allow_new_failures": policy.allow_new_failures,
            "max_p95_ms": policy.max_p95_ms,
            "max_avg_ms": policy.max_avg_ms,
            "max_retries": policy.max_retries,
            "retry_delay_ms": policy.retry_delay_ms,
        },
        "total": len(case_results),
        "passed": passed,
        "failed": failed,
        "failure_rate": (failed / len(case_results)) if case_results else 0.0,
        "latency": {
            "avg_ms": avg_ms(elapsed_values),
            "p95_ms": p95_ms(elapsed_values),
            "max_ms": max(elapsed_values) if elapsed_values else 0,
        },
        "retries": {
            "total": retries_total,
            "cases_with_retry": cases_with_retry,
        },
        "routes": summarize_routes(case_results),
        "results": case_results,
    }
    report["gates"] = evaluate_gates(report, previous, policy, args.skip_gates)
    report["all_ok"] = bool(report["gates"]["ok"])

    markdown_path = Path(args.write_markdown) if args.write_markdown else None
    write_outputs(report, markdown_path)
    if report.get("all_ok") is not None:
        try:
            append_audit_event(
                "evaluation_harness.run",
                {
                    "total": int(report["total"]),
                    "passed": int(report["passed"]),
                    "failed": int(report["failed"]),
                    "failure_rate": float(report["failure_rate"]),
                    "gate_ok": bool(report["gates"]["ok"]),
                    "new_failures": list(report["gates"].get("new_failures", [])),
                    "resolved_failures": list(report["gates"].get("resolved_failures", [])),
                    "avg_ms": int(report["latency"]["avg_ms"]),
                    "p95_ms": int(report["latency"]["p95_ms"]),
                },
                source="roby-eval-harness",
                run_id=str(report["ts"]),
                severity="error" if not bool(report["gates"]["ok"]) else "info",
            )
        except Exception:
            pass

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(
            f"[eval] total={report['total']} passed={passed} failed={failed} "
            f"failure_rate={report['failure_rate']:.3f} gate={'PASS' if report['gates']['ok'] else 'FAIL'}"
        )
        print(
            f"[eval] latency avg_ms={report['latency']['avg_ms']} p95_ms={report['latency']['p95_ms']} "
            f"retries={report['retries']['total']}"
        )
        for row in case_results:
            status = "PASS" if row.get("ok") else "FAIL"
            print(
                f"- {status} {row['id']}: {row.get('description','')} "
                f"(elapsed={row.get('elapsed_ms',0)}ms attempts={row.get('attempt_count',1)})"
            )
            if args.verbose and row.get("failures"):
                for reason in row["failures"]:
                    print(f"  - {reason}")
        if report["gates"]["failures"]:
            print("[eval] gate failures:")
            for reason in report["gates"]["failures"]:
                print(f"  - {reason}")
        print(f"[eval] latest={LATEST_PATH}")
        if markdown_path:
            print(f"[eval] markdown={markdown_path}")

    if args.soft_fail:
        return 0
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

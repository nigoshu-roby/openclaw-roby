#!/usr/bin/env python3
"""PBS Evaluation Harness (MVP).

Runs predefined orchestrator test cases and validates route/response expectations.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

JST = timezone(timedelta(hours=9))
OPENCLAW_REPO = Path("/Users/<user>/OpenClaw")
ORCH_SCRIPT = OPENCLAW_REPO / "scripts" / "roby-orchestrator.py"
DEFAULT_CASES_PATH = OPENCLAW_REPO / "config" / "pbs" / "eval_cases.json"
STATE_DIR = Path.home() / ".openclaw" / "roby" / "evals"
LATEST_PATH = STATE_DIR / "latest.json"
HISTORY_PATH = STATE_DIR / "history.jsonl"


@dataclass
class EvalCase:
    id: str
    description: str
    message: str
    route: str = "auto"
    execute: bool = False
    expect: Dict[str, Any] = None



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()



def load_cases(path: Path) -> List[EvalCase]:
    if not path.exists():
        raise FileNotFoundError(f"cases file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
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



def run_orchestrator(case: EvalCase) -> Tuple[int, str, Dict[str, Any]]:
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
    proc = subprocess.run(cmd, cwd=str(OPENCLAW_REPO), capture_output=True, text=True)
    stdout = (proc.stdout or "").strip()
    parsed: Dict[str, Any] = {}
    if stdout:
        try:
            parsed = json.loads(stdout)
        except Exception:
            parsed = {"_raw_stdout": stdout}
    return proc.returncode, stdout, parsed



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



def evaluate_case(case: EvalCase) -> Dict[str, Any]:
    returncode, stdout, parsed = run_orchestrator(case)
    failures: List[str] = []

    if returncode != 0:
        failures.append(f"orchestrator_exit: {returncode}")

    expect = case.expect or {}
    equals_rules = expect.get("equals") if isinstance(expect.get("equals"), dict) else {}
    contains_rules = expect.get("contains") if isinstance(expect.get("contains"), dict) else {}
    not_contains_rules = expect.get("not_contains") if isinstance(expect.get("not_contains"), dict) else {}
    min_len_rules = expect.get("min_len") if isinstance(expect.get("min_len"), dict) else {}

    failures.extend(check_equals(parsed, equals_rules))
    failures.extend(check_contains(parsed, contains_rules))
    failures.extend(check_not_contains(parsed, not_contains_rules))
    failures.extend(check_min_len(parsed, min_len_rules))

    return {
        "id": case.id,
        "description": case.description,
        "route": case.route,
        "execute": case.execute,
        "ok": len(failures) == 0,
        "failures": failures,
        "orchestrator_returncode": returncode,
        "result": parsed,
        "raw_stdout": stdout if not parsed else "",
    }



def write_outputs(report: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")



def main() -> int:
    args = parse_args()
    cases_path = Path(args.cases)
    all_cases = load_cases(cases_path)
    selected = set(args.case or [])
    if selected:
        cases = [c for c in all_cases if c.id in selected]
    else:
        cases = all_cases

    if not cases:
        print(json.dumps({"error": "no cases selected"}, ensure_ascii=False))
        return 2

    started = datetime.now(JST)
    case_results = [evaluate_case(case) for case in cases]
    passed = sum(1 for r in case_results if r.get("ok"))
    failed = len(case_results) - passed
    report = {
        "ts": started.isoformat(),
        "cases_path": str(cases_path),
        "total": len(case_results),
        "passed": passed,
        "failed": failed,
        "all_ok": failed == 0,
        "results": case_results,
    }

    write_outputs(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"[eval] total={report['total']} passed={passed} failed={failed}")
        for row in case_results:
            status = "PASS" if row.get("ok") else "FAIL"
            print(f"- {status} {row['id']}: {row.get('description','')}")
            if args.verbose and row.get("failures"):
                for reason in row["failures"]:
                    print(f"  - {reason}")
        print(f"[eval] latest={LATEST_PATH}")

    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

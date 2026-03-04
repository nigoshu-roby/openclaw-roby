#!/usr/bin/env python3
"""Immutable audit utilities for PBS.

Append-only JSONL events with hash chaining:
- each event contains `prev_hash`
- current `hash` is sha256(canonical_json(event_without_hash))
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

JST = timezone(timedelta(hours=9))
AUDIT_DIR = Path.home() / ".openclaw" / "roby" / "audit"
DEFAULT_AUDIT_PATH = AUDIT_DIR / "events.jsonl"
GENESIS_HASH = "GENESIS"


def _canonical_json(value: Dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _calc_hash(payload: Dict[str, Any]) -> str:
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _tail_last_line(path: Path) -> Optional[str]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("rb") as f:
        f.seek(0, 2)
        end = f.tell()
        pos = end
        chunk = b""
        while pos > 0:
            step = 4096 if pos >= 4096 else pos
            pos -= step
            f.seek(pos)
            data = f.read(step)
            chunk = data + chunk
            if b"\n" in data and len(chunk) > 1:
                break
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            return None
        return lines[-1].decode("utf-8", errors="replace")


def _read_last_event(path: Path) -> Dict[str, Any]:
    raw = _tail_last_line(path)
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return {}
    return {}


def append_audit_event(
    event_type: str,
    payload: Dict[str, Any],
    *,
    source: str = "",
    severity: str = "info",
    run_id: str = "",
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Append immutable audit event and return the inserted record."""
    audit_path = path or DEFAULT_AUDIT_PATH
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    last = _read_last_event(audit_path)
    prev_hash = str(last.get("hash") or GENESIS_HASH)
    prev_seq = int(last.get("seq") or 0) if isinstance(last.get("seq"), int) else int(last.get("seq") or 0)
    seq = prev_seq + 1

    core = {
        "ts": datetime.now(JST).isoformat(),
        "epoch": int(time.time()),
        "seq": seq,
        "event_type": event_type,
        "source": source,
        "severity": severity,
        "run_id": run_id,
        "prev_hash": prev_hash,
        "payload": payload,
    }
    digest = _calc_hash(core)
    row = dict(core)
    row["hash"] = digest

    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def verify_audit_file(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "file": str(path),
        "ok": True,
        "count": 0,
        "errors": [],
        "last_hash": "",
    }
    if not path.exists():
        result["ok"] = False
        result["errors"].append("file_not_found")
        return result

    prev_hash = GENESIS_HASH
    expected_seq = 1
    with path.open("r", encoding="utf-8") as f:
        for idx, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                result["ok"] = False
                result["errors"].append(f"line_{idx}:invalid_json")
                continue
            if not isinstance(row, dict):
                result["ok"] = False
                result["errors"].append(f"line_{idx}:non_object")
                continue

            got_prev = str(row.get("prev_hash") or "")
            if got_prev != prev_hash:
                result["ok"] = False
                result["errors"].append(
                    f"line_{idx}:prev_hash_mismatch expected={prev_hash} actual={got_prev}"
                )

            seq = row.get("seq")
            if seq != expected_seq:
                result["ok"] = False
                result["errors"].append(
                    f"line_{idx}:seq_mismatch expected={expected_seq} actual={seq}"
                )
            expected_seq += 1

            got_hash = str(row.get("hash") or "")
            core = dict(row)
            core.pop("hash", None)
            expect_hash = _calc_hash(core)
            if got_hash != expect_hash:
                result["ok"] = False
                result["errors"].append(f"line_{idx}:hash_mismatch")

            prev_hash = got_hash or prev_hash
            result["count"] += 1

    result["last_hash"] = prev_hash if result["count"] > 0 else GENESIS_HASH
    return result


def verify_audit(paths: List[Path]) -> Dict[str, Any]:
    details = [verify_audit_file(p) for p in paths]
    ok = all(d.get("ok") for d in details)
    errors = sum(len(d.get("errors", [])) for d in details)
    return {
        "ts": datetime.now(JST).isoformat(),
        "ok": ok,
        "files": len(details),
        "errors": errors,
        "details": details,
    }


def _parse_json_arg(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("payload must be JSON object")
    return obj


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    append = sub.add_parser("append")
    append.add_argument("--event-type", required=True)
    append.add_argument("--source", default="")
    append.add_argument("--severity", default="info")
    append.add_argument("--run-id", default="")
    append.add_argument("--payload-json", default="")
    append.add_argument("--payload-file", default="")
    append.add_argument("--path", default=str(DEFAULT_AUDIT_PATH))
    append.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify")
    verify.add_argument("--path", default=str(DEFAULT_AUDIT_PATH))
    verify.add_argument("--all", action="store_true")
    verify.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.cmd == "append":
        payload: Dict[str, Any] = {}
        if args.payload_file:
            payload = _parse_json_arg(Path(args.payload_file).read_text(encoding="utf-8"))
        elif args.payload_json:
            payload = _parse_json_arg(args.payload_json)
        row = append_audit_event(
            args.event_type,
            payload,
            source=args.source,
            severity=args.severity,
            run_id=args.run_id,
            path=Path(args.path),
        )
        if args.json:
            print(json.dumps(row, ensure_ascii=False))
        else:
            print(f"[audit] appended seq={row.get('seq')} hash={row.get('hash')}")
        return 0

    if args.cmd == "verify":
        if args.all:
            paths = sorted(AUDIT_DIR.glob("*.jsonl"))
        else:
            paths = [Path(args.path)]
        report = verify_audit(paths)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(
                f"[audit] files={report['files']} ok={report['ok']} errors={report['errors']}"
            )
            for detail in report["details"]:
                status = "PASS" if detail.get("ok") else "FAIL"
                print(
                    f"- {status} {detail.get('file')} count={detail.get('count')} errors={len(detail.get('errors', []))}"
                )
                for err in detail.get("errors", []):
                    print(f"  - {err}")
        return 0 if report.get("ok") else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

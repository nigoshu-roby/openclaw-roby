#!/usr/bin/env python3
"""Build a local Gmail reply-history / contact-importance index for PBS."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from gmail_triage import load_env

INDEX_PATH = Path.home() / ".openclaw" / "roby" / "gmail_contact_index.json"
RUN_LOG_PATH = Path.home() / ".openclaw" / "roby" / "gmail_contact_index_runs.jsonl"
PROGRESS_PATH = Path.home() / ".openclaw" / "roby" / "gmail_contact_index_progress.json"

DEFAULT_LOOKBACK_MONTHS = 18
DEFAULT_TIMEOUT_SEC = 600
DEFAULT_MAX_NEW_THREADS = 150
DEFAULT_SLEEP_SEC = 0.18
DEFAULT_CHECKPOINT_EVERY = 25
DEFAULT_RETRY_SLEEP_SEC = 6.0


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _safe_read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _sender_parts(raw_from: str) -> Tuple[str, str, str]:
    name, addr = parseaddr(raw_from or "")
    email = (addr or "").strip().lower()
    domain = email.split("@", 1)[1] if "@" in email else ""
    display = (name or email or (raw_from or "")).strip()
    return display, email, domain


def _all_addresses(value: str) -> List[Tuple[str, str, str]]:
    seen: set[str] = set()
    out: List[Tuple[str, str, str]] = []
    for name, addr in getaddresses([value or ""]):
        email = (addr or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        domain = email.split("@", 1)[1] if "@" in email else ""
        display = (name or email).strip()
        out.append((display, email, domain))
    return out


def month_windows(months: int, *, now: datetime | None = None) -> List[Tuple[datetime, datetime]]:
    current = now or datetime.now(timezone.utc)
    end = current
    windows: List[Tuple[datetime, datetime]] = []
    for _ in range(max(1, months)):
        start = end - timedelta(days=30)
        windows.append((start, end))
        end = start
    windows.reverse()
    return windows


def window_query(start: datetime, end: datetime, mailbox: str) -> str:
    return f"after:{start.strftime('%Y/%m/%d')} before:{end.strftime('%Y/%m/%d')} {mailbox}"


def _run_gog_json(cmd: List[str], env: Dict[str, str], *, timeout_sec: int, retries: int = 4) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            out = subprocess.check_output(cmd, env=env, timeout=timeout_sec, stderr=subprocess.STDOUT)
            return json.loads(out or "[]")
        except subprocess.CalledProcessError as exc:
            output = (exc.output or b"").decode("utf-8", errors="ignore")
            last_error = exc
            if "rateLimitExceeded" in output and attempt < retries - 1:
                time.sleep(DEFAULT_RETRY_SLEEP_SEC * (attempt + 1))
                continue
            raise
    if last_error:
        raise last_error
    return []


def gog_search_threads(account: str, query: str, env: Dict[str, str], *, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> List[Dict[str, Any]]:
    cmd = [
        "gog",
        "gmail",
        "search",
        query,
        "--all",
        "--json",
        "--results-only",
        "--no-input",
    ]
    if account:
        cmd += ["--account", account]
    return _run_gog_json(cmd, env, timeout_sec=timeout_sec)


def gog_get_thread(account: str, thread_id: str, env: Dict[str, str], *, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> Dict[str, Any]:
    cmd = [
        "gog",
        "gmail",
        "thread",
        "get",
        thread_id,
        "--json",
        "--results-only",
        "--no-input",
    ]
    if account:
        cmd += ["--account", account]
    data = _run_gog_json(cmd, env, timeout_sec=timeout_sec)
    if isinstance(data, dict) and "thread" in data:
        return data["thread"]
    return data if isinstance(data, dict) else {}


def importance_tier(thread_replied: bool, sender_thread_count: int, domain_thread_count: int) -> Tuple[str, int]:
    score = 0
    if thread_replied:
        score += 6
    if sender_thread_count >= 6:
        score += 4
    elif sender_thread_count >= 3:
        score += 3
    elif sender_thread_count >= 1:
        score += 2
    if domain_thread_count >= 12:
        score += 3
    elif domain_thread_count >= 6:
        score += 2
    elif domain_thread_count >= 2:
        score += 1

    if score >= 8:
        return "high", score
    if score >= 4:
        return "medium", score
    if score >= 2:
        return "low", score
    return "none", score


def _participant_candidates(thread: Dict[str, Any], self_emails: set[str]) -> List[Tuple[str, str, str]]:
    candidates: List[Tuple[str, str, str]] = []
    seen: set[str] = set()
    for message in thread.get("messages", []):
        payload = message.get("payload", {}) or {}
        headers = {h.get("name"): h.get("value") for h in payload.get("headers", []) if h.get("name")}
        for header_name in ("From", "To", "Cc"):
            for display, email, domain in _all_addresses(str(headers.get(header_name, ""))):
                if email in self_emails or not email:
                    continue
                if email in seen:
                    continue
                seen.add(email)
                candidates.append((display, email, domain))
    return candidates


def _thread_last_date(thread: Dict[str, Any]) -> str:
    last = ""
    for message in thread.get("messages", []):
        payload = message.get("payload", {}) or {}
        headers = {h.get("name"): h.get("value") for h in payload.get("headers", []) if h.get("name")}
        value = str(headers.get("Date", "") or "")
        if value and value > last:
            last = value
    return last


def build_contact_index(
    sent_threads: List[Dict[str, Any]],
    fetched_threads: Dict[str, Dict[str, Any]] | List[Dict[str, Any]],
    *,
    lookback_months: int,
    generated_at: str | None = None,
    processed_thread_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    if isinstance(fetched_threads, list):
        fetched_threads = {
            str(thread.get("thread_id", thread.get("id", ""))).strip(): {
                "subject": str(thread.get("subject", "")).strip(),
                "date": str(thread.get("date", "")).strip(),
                "participants": (
                    [
                        {
                            "sender_display": display,
                            "sender_email": email,
                            "sender_domain": domain,
                        }
                        for display, email, domain in [_sender_parts(str(thread.get("from", "")))]
                        if email
                    ]
                ),
            }
            for thread in fetched_threads
            if str(thread.get("thread_id", thread.get("id", ""))).strip()
        }

    replied_thread_ids = {
        str(thread.get("id", "")).strip()
        for thread in sent_threads
        if str(thread.get("id", "")).strip()
    }
    processed_ids = {str(x).strip() for x in (processed_thread_ids or []) if str(x).strip()}

    sender_counter: Counter[str] = Counter()
    domain_counter: Counter[str] = Counter()
    thread_index: Dict[str, Dict[str, Any]] = {}

    for thread_id in replied_thread_ids:
        thread = fetched_threads.get(thread_id)
        if not thread:
            continue
        participants = thread.get("participants", []) or []
        if not participants:
            continue
        primary = participants[0]
        sender_email = str(primary.get("sender_email", "")).strip().lower()
        sender_domain = str(primary.get("sender_domain", "")).strip().lower()
        if not sender_email:
            continue
        sender_counter[sender_email] += 1
        if sender_domain:
            domain_counter[sender_domain] += 1
        thread_index[thread_id] = {
            "thread_id": thread_id,
            "subject": str(thread.get("subject", "")).strip(),
            "date": str(thread.get("date", "")).strip(),
            "sender_display": str(primary.get("sender_display", "")).strip(),
            "sender_email": sender_email,
            "sender_domain": sender_domain,
            "participants": participants,
        }

    sender_index: Dict[str, Dict[str, Any]] = {}
    for thread in thread_index.values():
        email = thread["sender_email"]
        domain = thread["sender_domain"]
        tier, score = importance_tier(True, sender_counter[email], domain_counter.get(domain, 0))
        current = sender_index.get(email)
        candidate = {
            "sender_display": thread["sender_display"],
            "sender_email": email,
            "sender_domain": domain,
            "thread_count": sender_counter[email],
            "domain_thread_count": domain_counter.get(domain, 0),
            "last_date": thread["date"],
            "tier": tier,
            "score": score,
        }
        if not current or candidate["last_date"] > current.get("last_date", ""):
            sender_index[email] = candidate

    domain_index: Dict[str, Dict[str, Any]] = {}
    for domain, count in domain_counter.items():
        tier, score = importance_tier(False, 0, count)
        domain_index[domain] = {
            "sender_domain": domain,
            "thread_count": count,
            "tier": tier,
            "score": score,
        }

    return {
        "generated_at": generated_at or _iso_now(),
        "lookback_months": int(lookback_months),
        "sent_thread_count": len(sent_threads),
        "replied_thread_count": len(replied_thread_ids),
        "processed_thread_count": len(processed_ids),
        "indexed_thread_count": len(thread_index),
        "indexed_sender_count": len(sender_index),
        "indexed_domain_count": len(domain_index),
        "thread_index": thread_index,
        "sender_index": sender_index,
        "domain_index": domain_index,
    }


def write_run_log(entry: Dict[str, Any]) -> None:
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def save_progress(progress: Dict[str, Any]) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def load_progress() -> Dict[str, Any]:
    return _safe_read_json(PROGRESS_PATH, {})


def build_thread_cache(thread_obj: Dict[str, Any], self_emails: set[str]) -> Dict[str, Any]:
    participants = [
        {
            "sender_display": display,
            "sender_email": email,
            "sender_domain": domain,
        }
        for display, email, domain in _participant_candidates(thread_obj, self_emails)
    ]
    return {
        "subject": str(thread_obj.get("messages", [{}])[0].get("payload", {}).get("headers", [{}])),
        "date": _thread_last_date(thread_obj),
        "participants": participants,
        "subject": str(
            next(
                (
                    h.get("value")
                    for h in (thread_obj.get("messages", [{}])[0].get("payload", {}) or {}).get("headers", [])
                    if h.get("name") == "Subject"
                ),
                "",
            )
        ).strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="")
    parser.add_argument("--months", type=int, default=DEFAULT_LOOKBACK_MONTHS)
    parser.add_argument("--index-path", default=str(INDEX_PATH))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--max-new-threads", type=int, default=DEFAULT_MAX_NEW_THREADS)
    parser.add_argument("--sleep-sec", type=float, default=DEFAULT_SLEEP_SEC)
    parser.add_argument("--checkpoint-every", type=int, default=DEFAULT_CHECKPOINT_EVERY)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()

    env = load_env()
    account = args.account or env.get("GMAIL_ACCOUNT", "")
    months = max(1, int(args.months))
    index_path = Path(args.index_path).expanduser()
    started = time.time()
    now = datetime.now(timezone.utc)

    sent_threads: List[Dict[str, Any]] = []
    for start, end in month_windows(months, now=now):
        sent_threads.extend(gog_search_threads(account, window_query(start, end, "in:sent"), env))

    index_existing = _safe_read_json(index_path, {})
    existing_thread_cache = index_existing.get("thread_index", {}) if isinstance(index_existing, dict) else {}
    progress = load_progress()
    processed_ids = set(progress.get("processed_thread_ids", [])) if isinstance(progress, dict) else set()
    if args.force_refresh:
        existing_thread_cache = {}
        processed_ids = set()

    replied_thread_ids = []
    seen_thread_ids: set[str] = set()
    for thread in sent_threads:
        thread_id = str(thread.get("id", "")).strip()
        if not thread_id or thread_id in seen_thread_ids:
            continue
        seen_thread_ids.add(thread_id)
        replied_thread_ids.append(thread_id)

    self_emails = {account.lower()} if account else set()
    fetched_threads: Dict[str, Dict[str, Any]] = {k: v for k, v in existing_thread_cache.items() if k in seen_thread_ids}
    new_thread_ids = [tid for tid in replied_thread_ids if tid not in processed_ids or tid not in fetched_threads]

    max_new_threads = max(1, int(args.max_new_threads))
    processed_now = 0
    last_checkpoint = time.time()
    for idx, thread_id in enumerate(new_thread_ids[:max_new_threads], start=1):
        thread_obj = gog_get_thread(account, thread_id, env)
        fetched_threads[thread_id] = build_thread_cache(thread_obj, self_emails)
        processed_ids.add(thread_id)
        processed_now += 1
        if args.sleep_sec > 0:
            time.sleep(args.sleep_sec)
        if idx % max(1, int(args.checkpoint_every)) == 0 or (time.time() - last_checkpoint) > 20:
            partial = build_contact_index(
                sent_threads,
                fetched_threads,
                lookback_months=months,
                generated_at=_iso_now(),
                processed_thread_ids=processed_ids,
            )
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8")
            save_progress(
                {
                    "generated_at": partial["generated_at"],
                    "processed_thread_ids": sorted(processed_ids),
                    "processed_thread_count": len(processed_ids),
                    "remaining_thread_count": max(0, len(replied_thread_ids) - len(processed_ids)),
                    "last_thread_id": thread_id,
                }
            )
            last_checkpoint = time.time()

    index = build_contact_index(
        sent_threads,
        fetched_threads,
        lookback_months=months,
        generated_at=_iso_now(),
        processed_thread_ids=processed_ids,
    )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    save_progress(
        {
            "generated_at": index["generated_at"],
            "processed_thread_ids": sorted(processed_ids),
            "processed_thread_count": len(processed_ids),
            "remaining_thread_count": max(0, len(replied_thread_ids) - len(processed_ids)),
            "last_thread_id": replied_thread_ids[min(len(processed_ids), len(replied_thread_ids)) - 1] if processed_ids else "",
        }
    )

    summary = {
        "generated_at": index["generated_at"],
        "account": account,
        "lookback_months": months,
        "sent_thread_count": index["sent_thread_count"],
        "replied_thread_count": index["replied_thread_count"],
        "processed_thread_count": index["processed_thread_count"],
        "indexed_thread_count": index["indexed_thread_count"],
        "indexed_sender_count": index["indexed_sender_count"],
        "indexed_domain_count": index["indexed_domain_count"],
        "new_threads_processed": processed_now,
        "remaining_thread_count": max(0, len(replied_thread_ids) - len(processed_ids)),
        "elapsed_ms": int((time.time() - started) * 1000),
        "index_path": str(index_path),
        "progress_path": str(PROGRESS_PATH),
    }
    write_run_log(summary)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(
            "reply-history index updated: "
            f"{summary['indexed_sender_count']} senders / "
            f"{summary['indexed_thread_count']} threads "
            f"(new {summary['new_threads_processed']}, remaining {summary['remaining_thread_count']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

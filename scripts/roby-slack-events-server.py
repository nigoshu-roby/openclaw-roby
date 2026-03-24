#!/usr/bin/env python3
"""
Slack Events API receiver for roby operations.

Adds:
- startup / periodic backfill for messages posted while Roby was offline
- persisted per-channel checkpoints in ~/.openclaw/roby/slack_events_state.json
- optional plain-channel handling (no @mention required)
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Tuple

DEFAULT_ROBY_SCRIPT = "/Users/shu/OpenClaw/skills/roby-mail/scripts/gmail_triage.py"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8788
DEFAULT_EVENTS_PATH = "/slack/events"
DEFAULT_ENV_FILE = str(Path.home() / ".openclaw" / ".env")
DEFAULT_FORWARD_CMD = "python3 /Users/shu/OpenClaw/scripts/roby-mention-forward-bridge.py"
DEFAULT_STATE_PATH = str(Path.home() / ".openclaw" / "roby" / "slack_events_state.json")
DEFAULT_LOG_PATH = str(Path.home() / ".openclaw" / "roby" / "slack_events_runs.jsonl")
STATE_LOCK = threading.Lock()
INFLIGHT_EVENTS: set[tuple[str, str]] = set()


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


def _csv_set(name: str) -> set[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() not in {"", "0", "false", "off", "no"}


def _float_ts(value: str | float | int | None) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value))
    except Exception:
        return 0.0


def load_state(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        return {"channels": {}, "known_channels": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"channels": {}, "known_channels": []}
        data.setdefault("channels", {})
        data.setdefault("known_channels", [])
        return data
    except Exception:
        return {"channels": {}, "known_channels": []}


def save_state(path: str, state: dict) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def append_log(path: str, payload: dict) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


@dataclass
class Config:
    signing_secret: str
    bot_token: str
    roby_script: str
    default_account: str
    allowed_channels: set[str]
    backfill_channels: set[str]
    allowed_users: set[str]
    forward_cmd: str
    allow_plain_messages: bool
    state_path: str
    log_path: str
    backfill_on_start: bool
    backfill_interval_sec: int
    backfill_max_messages: int
    backfill_lookback_sec: int

    @classmethod
    def from_env(cls) -> "Config":
        signing_secret = os.getenv("SLACK_SIGNING_SECRET", "").strip()
        bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
        roby_script = os.getenv("ROBY_MAIL_SCRIPT", DEFAULT_ROBY_SCRIPT).strip() or DEFAULT_ROBY_SCRIPT
        default_account = os.getenv("ROBY_GMAIL_ACCOUNT", "").strip() or os.getenv("GOG_ACCOUNT", "").strip()
        allowed_channels = _csv_set("ROBY_ALLOWED_SLACK_CHANNELS")
        backfill_channels = _csv_set("ROBY_SLACK_BACKFILL_CHANNELS")
        allowed_users = _csv_set("ROBY_ALLOWED_SLACK_USERS")
        forward_cmd = os.getenv("ROBY_MENTION_FORWARD_CMD", "").strip() or DEFAULT_FORWARD_CMD
        allow_plain_messages = _truthy("ROBY_ALLOW_PLAIN_MESSAGES", "1")
        state_path = os.getenv("ROBY_SLACK_STATE_PATH", DEFAULT_STATE_PATH).strip() or DEFAULT_STATE_PATH
        log_path = os.getenv("ROBY_SLACK_LOG_PATH", DEFAULT_LOG_PATH).strip() or DEFAULT_LOG_PATH
        backfill_on_start = _truthy("ROBY_SLACK_BACKFILL_ON_START", "1")
        backfill_interval_sec = int(os.getenv("ROBY_SLACK_BACKFILL_INTERVAL_SEC", "90") or "90")
        backfill_max_messages = int(os.getenv("ROBY_SLACK_BACKFILL_MAX_MESSAGES", "50") or "50")
        backfill_lookback_sec = int(os.getenv("ROBY_SLACK_BACKFILL_LOOKBACK_SEC", str(6 * 3600)) or str(6 * 3600))

        if not signing_secret:
            raise RuntimeError("SLACK_SIGNING_SECRET is required")
        if not bot_token:
            raise RuntimeError("SLACK_BOT_TOKEN is required")
        if not Path(roby_script).exists():
            raise RuntimeError(f"roby script not found: {roby_script}")
        if forward_cmd and not Path(shlex.split(forward_cmd)[1] if forward_cmd.startswith('python') else shlex.split(forward_cmd)[0]).exists():
            # best effort only; actual subprocess may still work if command is shell-resolved
            pass
        return cls(
            signing_secret=signing_secret,
            bot_token=bot_token,
            roby_script=roby_script,
            default_account=default_account,
            allowed_channels=allowed_channels,
            backfill_channels=backfill_channels,
            allowed_users=allowed_users,
            forward_cmd=forward_cmd,
            allow_plain_messages=allow_plain_messages,
            state_path=state_path,
            log_path=log_path,
            backfill_on_start=backfill_on_start,
            backfill_interval_sec=backfill_interval_sec,
            backfill_max_messages=backfill_max_messages,
            backfill_lookback_sec=backfill_lookback_sec,
        )


def verify_signature(secret: str, body: bytes, timestamp: str, signature: str) -> bool:
    if not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > 300:
        return False

    base = f"v0:{timestamp}:{body.decode('utf-8', 'replace')}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)


def slack_api_post_json(token: str, method: str, payload: Dict) -> Dict:
    url = f"https://slack.com/api/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        out = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(out)
    except Exception:
        return {"ok": False, "error": "invalid_json", "raw": out}


def conversations_history(token: str, channel: str, oldest: float, limit: int) -> list[dict]:
    payload = {
        "channel": channel,
        "oldest": f"{oldest:.6f}",
        "inclusive": False,
        "limit": limit,
    }
    resp = slack_api_post_json(token, "conversations.history", payload)
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", "history_failed"))
    return list(resp.get("messages") or [])


def auth_test(token: str) -> dict:
    resp = slack_api_post_json(token, "auth.test", {})
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", "auth_test_failed"))
    return resp


def conversations_list(token: str, types: list[str], limit: int = 200) -> list[dict]:
    items: list[dict] = []
    cursor = ""
    while True:
        payload = {"exclude_archived": True, "limit": limit, "types": ",".join(types)}
        if cursor:
            payload["cursor"] = cursor
        resp = slack_api_post_json(token, "conversations.list", payload)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "conversations_list_failed"))
        items.extend(list(resp.get("channels") or []))
        cursor = (((resp.get("response_metadata") or {})).get("next_cursor") or "").strip()
        if not cursor:
            break
    return items


def post_message(token: str, channel: str, text: str, thread_ts: str = "") -> None:
    payload = {"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    slack_api_post_json(token, "chat.postMessage", payload)


def strip_mention_prefix(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^(?:<@[^>]+>\s*)+", "", s).strip()
    return s


def parse_triage_command(rest: str, default_account: str) -> Tuple[List[str], str]:
    tokens = shlex.split(rest) if rest.strip() else []

    parser = argparse.ArgumentParser(prog="triage", add_help=False)
    parser.add_argument("--account", default=default_account)
    parser.add_argument("--query", default="newer_than:2d in:inbox")
    parser.add_argument("--max", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-tasks", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--archive-ads", dest="archive_ads", action="store_true")
    parser.add_argument("--no-archive-ads", dest="archive_ads", action="store_false")
    parser.set_defaults(archive_ads=True)

    try:
        args = parser.parse_args(tokens)
    except SystemExit:
        return [], "usage_error"

    cmd = ["python3", DEFAULT_ROBY_SCRIPT, "--query", args.query, "--max", str(args.max)]
    if args.account:
        cmd.extend(["--account", args.account])
    if args.dry_run:
        cmd.append("--dry-run")
    if args.skip_tasks:
        cmd.append("--skip-tasks")
    if args.verbose:
        cmd.append("--verbose")
    if not args.archive_ads:
        cmd.append("--no-archive-ads")
    return cmd, "ok"


def help_text() -> str:
    return (
        "使い方:\n"
        "- @roby help\n"
        "- @roby triage\n"
        "- @roby triage --dry-run\n"
        "- @roby triage --query \"newer_than:1d in:inbox\" --max 30\n"
        "- @roby ask 相談内容...\n"
        "- @roby dev 開発指示...\n"
        "- メンションなしでもチャンネル投稿を処理できます\n\n"
        "※ ask/dev を省略した場合は自動判定します（開発系→dev、それ以外→ask）。"
    )


def run_triage(cfg: Config, channel: str, thread_ts: str, rest: str) -> None:
    cmd, status = parse_triage_command(rest, cfg.default_account)
    if status == "usage_error":
        post_message(cfg.bot_token, channel, "引数エラーです。`@roby help` を見てください。", thread_ts)
        return
    cmd = cmd.copy()
    cmd[1] = cfg.roby_script
    post_message(cfg.bot_token, channel, "📬 triage 実行中…", thread_ts)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        post_message(cfg.bot_token, channel, "❌ triage timeout (240s)", thread_ts)
        return
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:1500]
        post_message(cfg.bot_token, channel, f"❌ triage failed (exit={proc.returncode})\n{err}", thread_ts)
        return
    out = (proc.stdout or "").strip()
    summary_line = out.splitlines()[-1] if out else ""
    try:
        data = json.loads(summary_line) if summary_line else {}
    except Exception:
        data = {}
    if data:
        cats = data.get("categories", {})
        cat_text = ", ".join(f"{k}:{v}" for k, v in sorted(cats.items())) if cats else "-"
        msg = (
            "✅ triage 完了\n"
            f"total={data.get('total',0)} new={data.get('new',0)} archived={data.get('archived',0)} "
            f"tasks={data.get('tasks',0)} notified={data.get('notified',0)}\n"
            f"categories: {cat_text}"
        )
    else:
        msg = "✅ triage 完了\n" + (out[-1500:] if out else "(no output)")
    post_message(cfg.bot_token, channel, msg, thread_ts)


def infer_mode_from_text(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return "ask"
    triage_signals = ["--query", "newer_than:", "in:inbox", "--max", "--dry-run", "--skip-tasks"]
    if any(s in t for s in triage_signals):
        return "triage"
    dev_signals = [
        "開発", "実装", "修正", "バグ", "不具合", "リファクタ", "設計", "コード", "commit", "pr", "issue",
        "script", "api", "仕様",
    ]
    if any(s in t for s in dev_signals):
        return "dev"
    return "ask"


def run_forward(cfg: Config, mode: str, text: str, channel: str, thread_ts: str, user_id: str) -> None:
    if not text.strip():
        post_message(cfg.bot_token, channel, f"`@roby {mode} ...` の形式で指示してください。", thread_ts)
        return
    if not cfg.forward_cmd:
        post_message(cfg.bot_token, channel, f"`{mode}` 受信は有効ですが、ROBY_MENTION_FORWARD_CMD が未設定です。", thread_ts)
        return
    cmd = shlex.split(cfg.forward_cmd) + ["--mode", mode, "--text", text, "--channel", channel, "--thread", thread_ts, "--user", user_id]
    post_message(cfg.bot_token, channel, f"🛠️ {mode} を処理中…", thread_ts)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        post_message(cfg.bot_token, channel, f"❌ {mode} timeout (300s)", thread_ts)
        return
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:1500]
        post_message(cfg.bot_token, channel, f"❌ {mode} failed (exit={proc.returncode})\n{err}", thread_ts)
        return
    reply = (proc.stdout or "").strip() or f"✅ {mode} 完了"
    post_message(cfg.bot_token, channel, reply[:3000], thread_ts)


def _is_authorized(cfg: Config, channel: str, user_id: str) -> bool:
    if cfg.allowed_channels and channel not in cfg.allowed_channels:
        return False
    if cfg.allowed_users and user_id not in cfg.allowed_users:
        return False
    return True


def update_state_for_event(cfg: Config, channel: str, ts: str, source: str) -> None:
    with STATE_LOCK:
        state = load_state(cfg.state_path)
        channels = state.setdefault("channels", {})
        ch = channels.setdefault(channel, {})
        last_seen = _float_ts(ch.get("last_seen_ts"))
        new_ts = _float_ts(ts)
        if new_ts >= last_seen:
            ch["last_seen_ts"] = ts
            ch["last_source"] = source
            ch["updated_at"] = int(time.time())
        known = set(state.get("known_channels") or [])
        known.add(channel)
        state["known_channels"] = sorted(known)
        save_state(cfg.state_path, state)


def should_skip_event(cfg: Config, channel: str, ts: str) -> bool:
    with STATE_LOCK:
        state = load_state(cfg.state_path)
        last_seen = _float_ts((state.get("channels") or {}).get(channel, {}).get("last_seen_ts"))
    return _float_ts(ts) <= last_seen


def claim_event(cfg: Config, channel: str, ts: str) -> bool:
    key = (channel, ts)
    with STATE_LOCK:
        state = load_state(cfg.state_path)
        last_seen = _float_ts((state.get("channels") or {}).get(channel, {}).get("last_seen_ts"))
        if _float_ts(ts) <= last_seen:
            return False
        if key in INFLIGHT_EVENTS:
            return False
        INFLIGHT_EVENTS.add(key)
        return True


def release_event_claim(channel: str, ts: str) -> None:
    key = (channel, ts)
    with STATE_LOCK:
        INFLIGHT_EVENTS.discard(key)


def dispatch_mention(cfg: Config, event: Dict) -> None:
    channel = event.get("channel", "")
    user_id = event.get("user", "")
    text = strip_mention_prefix(event.get("text", ""))
    thread_ts = event.get("thread_ts") or event.get("ts") or ""
    if not _is_authorized(cfg, channel, user_id):
        return
    if not text or text == "help":
        post_message(cfg.bot_token, channel, help_text(), thread_ts)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), "mention_help")
        return
    if text.startswith("triage"):
        rest = text[len("triage"):].strip()
        run_triage(cfg, channel, thread_ts, rest)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), "mention_triage")
        return
    if text.startswith("ask"):
        run_forward(cfg, "ask", text[len("ask"):].strip(), channel, thread_ts, user_id)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), "mention_ask")
        return
    if text.startswith("dev"):
        run_forward(cfg, "dev", text[len("dev"):].strip(), channel, thread_ts, user_id)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), "mention_dev")
        return
    mode = infer_mode_from_text(text)
    if mode == "triage":
        run_triage(cfg, channel, thread_ts, text)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), "mention_triage_auto")
    else:
        run_forward(cfg, mode, text, channel, thread_ts, user_id)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), f"mention_{mode}")


def dispatch_message(cfg: Config, event: Dict) -> None:
    channel = event.get("channel", "")
    user_id = event.get("user", "")
    text = (event.get("text", "") or "").strip()
    thread_ts = event.get("thread_ts") or event.get("ts") or ""
    if not cfg.allow_plain_messages:
        return
    if not text:
        return
    if not _is_authorized(cfg, channel, user_id):
        return
    if text == "help":
        post_message(cfg.bot_token, channel, help_text(), thread_ts)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), "message_help")
        return
    if text.startswith("triage"):
        rest = text[len("triage"):].strip()
        run_triage(cfg, channel, thread_ts, rest)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), "message_triage")
        return
    if text.startswith("ask"):
        run_forward(cfg, "ask", text[len("ask"):].strip(), channel, thread_ts, user_id)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), "message_ask")
        return
    if text.startswith("dev"):
        run_forward(cfg, "dev", text[len("dev"):].strip(), channel, thread_ts, user_id)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), "message_dev")
        return
    mode = infer_mode_from_text(text)
    if mode == "triage":
        run_triage(cfg, channel, thread_ts, text)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), "message_triage_auto")
    else:
        run_forward(cfg, mode, text, channel, thread_ts, user_id)
        update_state_for_event(cfg, channel, event.get("ts", thread_ts), f"message_{mode}")


def handle_event(cfg: Config, ev: Dict, source: str) -> None:
    event_type = ev.get("type")
    ts = ev.get("ts") or ev.get("event_ts") or ""
    channel = ev.get("channel", "")
    if not channel or not ts:
        return
    if ev.get("bot_id") or ev.get("subtype"):
        return
    if not claim_event(cfg, channel, ts):
        return
    processed = False
    try:
        if event_type == "app_mention":
            dispatch_mention(cfg, ev)
            processed = True
        elif event_type == "message" and cfg.allow_plain_messages:
            dispatch_message(cfg, ev)
            processed = True
        else:
            return
        append_log(cfg.log_path, {
            "ts": int(time.time()),
            "event_ts": ts,
            "channel": channel,
            "source": source,
            "event_type": event_type,
            "user": ev.get("user", ""),
            "text": (ev.get("text", "") or "")[:500],
        })
    finally:
        release_event_claim(channel, ts)


def persist_known_channels(cfg: Config, channels: list[str], source: str) -> None:
    if not channels:
        return
    with STATE_LOCK:
        state = load_state(cfg.state_path)
        known = set(state.get("known_channels") or [])
        known.update(channels)
        state["known_channels"] = sorted(known)
        save_state(cfg.state_path, state)
    append_log(cfg.log_path, {
        "ts": int(time.time()),
        "source": source,
        "known_channels": sorted(channels),
    })


def _conversation_types_for_discovery() -> list[str]:
    return ["public_channel", "private_channel", "mpim", "im"]


def _conversation_is_direct(conversation: dict) -> bool:
    return bool(conversation.get("is_im") or conversation.get("is_mpim"))


def discover_recent_backfill_channels(cfg: Config) -> list[str]:
    try:
        bot_user_id = str(auth_test(cfg.bot_token).get("user_id") or "").strip()
    except Exception as exc:
        append_log(cfg.log_path, {
            "ts": int(time.time()),
            "source": "channel_discovery",
            "error": str(exc),
        })
        return []
    if not bot_user_id:
        return []
    channels: list[str] = []
    oldest = max(0.0, time.time() - cfg.backfill_lookback_sec)
    try:
        conversations = conversations_list(cfg.bot_token, _conversation_types_for_discovery())
    except Exception as exc:
        append_log(cfg.log_path, {
            "ts": int(time.time()),
            "source": "channel_discovery",
            "error": str(exc),
        })
        return []
    for conversation in conversations:
        channel_id = str(conversation.get("id") or "").strip()
        if not channel_id:
            continue
        if _conversation_is_direct(conversation):
            channels.append(channel_id)
            continue
        try:
            messages = conversations_history(cfg.bot_token, channel_id, oldest=oldest, limit=min(20, cfg.backfill_max_messages))
        except Exception:
            continue
        mention = f"<@{bot_user_id}>"
        if any(mention in str((msg.get("text") or "")) for msg in messages):
            channels.append(channel_id)
    return sorted(set(channels))


def resolve_backfill_channels(cfg: Config) -> tuple[list[str], str]:
    if cfg.backfill_channels:
        return sorted(cfg.backfill_channels), "backfill_channels"
    if cfg.allowed_channels:
        return sorted(cfg.allowed_channels), "allowed_channels"
    with STATE_LOCK:
        state = load_state(cfg.state_path)
        known_channels = sorted(set(state.get("known_channels") or []))
    if known_channels:
        return known_channels, "state_known_channels"
    discovered = discover_recent_backfill_channels(cfg)
    if discovered:
        persist_known_channels(cfg, discovered, "channel_discovery")
        return discovered, "discovered_recent_activity"
    return [], "no_channels"


def backfill_channels(cfg: Config, reason: str) -> None:
    with STATE_LOCK:
        state = load_state(cfg.state_path)
        per_channel = dict(state.get("channels") or {})
    channels, channel_source = resolve_backfill_channels(cfg)
    append_log(cfg.log_path, {
        "ts": int(time.time()),
        "source": f"backfill:{reason}",
        "channel_resolution": channel_source,
        "channel_count": len(channels),
    })
    if not channels:
        return
    now = time.time()
    for channel in channels:
        last_seen = _float_ts(per_channel.get(channel, {}).get("last_seen_ts"))
        oldest = last_seen if last_seen > 0 else max(0.0, now - cfg.backfill_lookback_sec)
        try:
            messages = conversations_history(cfg.bot_token, channel, oldest=oldest, limit=cfg.backfill_max_messages)
        except Exception as exc:
            append_log(cfg.log_path, {
                "ts": int(time.time()),
                "channel": channel,
                "source": f"backfill:{reason}",
                "error": str(exc),
            })
            continue
        backlog = []
        for msg in reversed(messages):
            if msg.get("bot_id") or msg.get("subtype"):
                continue
            text = (msg.get("text", "") or "").strip()
            if not text:
                continue
            event_type = "app_mention" if text.startswith("<@") else "message"
            backlog.append({
                "type": event_type,
                "channel": channel,
                "user": msg.get("user", ""),
                "text": text,
                "ts": msg.get("ts", ""),
                "thread_ts": msg.get("thread_ts") or msg.get("ts") or "",
            })
        if not backlog:
            continue
        append_log(cfg.log_path, {
            "ts": int(time.time()),
            "channel": channel,
            "source": f"backfill:{reason}",
            "count": len(backlog),
            "oldest": oldest,
        })
        for ev in backlog:
            handle_event(cfg, ev, source=f"backfill:{reason}")


class Handler(BaseHTTPRequestHandler):
    cfg: Config = None  # type: ignore
    path_events: str = DEFAULT_EVENTS_PATH

    def _send_json(self, code: int, payload: Dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if self.path != self.path_events:
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"ok": False, "error": "bad_length"})
            return
        body = self.rfile.read(length)
        ts = self.headers.get("X-Slack-Request-Timestamp", "")
        sig = self.headers.get("X-Slack-Signature", "")
        if not verify_signature(self.cfg.signing_secret, body, ts, sig):
            self._send_json(401, {"ok": False, "error": "bad_signature"})
            return
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            self._send_json(400, {"ok": False, "error": "bad_json"})
            return
        if payload.get("type") == "url_verification":
            self._send_json(200, {"challenge": payload.get("challenge", "")})
            return
        self._send_json(200, {"ok": True})
        if payload.get("type") == "event_callback":
            ev = payload.get("event", {})
            th = threading.Thread(target=handle_event, args=(self.cfg, ev, "event"), daemon=True)
            th.start()

    def log_message(self, fmt: str, *args) -> None:
        return


def start_backfill_loop(cfg: Config) -> None:
    if not cfg.backfill_on_start:
        return

    def worker() -> None:
        time.sleep(3)
        backfill_channels(cfg, reason="startup")
        while True:
            time.sleep(max(30, cfg.backfill_interval_sec))
            backfill_channels(cfg, reason="interval")

    threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    load_env_file()
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("ROBY_SLACK_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("ROBY_SLACK_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--path", default=os.getenv("ROBY_SLACK_EVENTS_PATH", DEFAULT_EVENTS_PATH))
    parser.add_argument("--backfill-once", action="store_true", help="Run one backfill pass and exit.")
    parser.add_argument("--backfill-reason", default="manual", help="Reason label to use with --backfill-once.")
    args = parser.parse_args()

    cfg = Config.from_env()
    Handler.cfg = cfg
    Handler.path_events = args.path

    if args.backfill_once:
        backfill_channels(cfg, reason=args.backfill_reason)
        print(f"[roby-events] backfill_once reason={args.backfill_reason}", flush=True)
        return 0

    start_backfill_loop(cfg)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[roby-events] listening on http://{args.host}:{args.port}{args.path}", flush=True)
    print(f"[roby-events] roby_script={cfg.roby_script}", flush=True)
    print("[roby-events] commands: help / triage / ask / dev", flush=True)
    print(f"[roby-events] plain_messages={cfg.allow_plain_messages}", flush=True)
    print(f"[roby-events] backfill_on_start={cfg.backfill_on_start} interval={cfg.backfill_interval_sec}s max={cfg.backfill_max_messages}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

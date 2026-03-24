#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


def _load_module():
    scripts_dir = Path(__file__).resolve().parents[1]
    script_path = scripts_dir / "roby-slack-events-server.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_slack_events_server_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RobySlackEventsServerTests(TestCase):
    def setUp(self):
        self.mod = _load_module()

    def _cfg(self, tmp: str, allowed_channels: set[str] | None = None):
        return self.mod.Config(
            signing_secret="secret",
            bot_token="token",
            roby_script="/bin/echo",
            default_account="",
            allowed_channels=allowed_channels or set(),
            backfill_channels=set(),
            allowed_users=set(),
            forward_cmd="",
            allow_plain_messages=True,
            state_path=str(Path(tmp) / "slack_events_state.json"),
            log_path=str(Path(tmp) / "slack_events_runs.jsonl"),
            backfill_on_start=True,
            backfill_interval_sec=90,
            backfill_max_messages=30,
            backfill_lookback_sec=21600,
        )

    def test_resolve_backfill_channels_prefers_allowed_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp, allowed_channels={"C2", "C1"})
            channels, source = self.mod.resolve_backfill_channels(cfg)
            self.assertEqual(channels, ["C1", "C2"])
            self.assertEqual(source, "allowed_channels")

    def test_resolve_backfill_channels_prefers_backfill_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp, allowed_channels={"C9"})
            cfg.backfill_channels = {"C2", "C1"}
            channels, source = self.mod.resolve_backfill_channels(cfg)
            self.assertEqual(channels, ["C1", "C2"])
            self.assertEqual(source, "backfill_channels")

    def test_resolve_backfill_channels_uses_known_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            Path(cfg.state_path).write_text(
                json.dumps({"known_channels": ["C9", "C3"], "channels": {}}, ensure_ascii=False),
                encoding="utf-8",
            )
            channels, source = self.mod.resolve_backfill_channels(cfg)
            self.assertEqual(channels, ["C3", "C9"])
            self.assertEqual(source, "state_known_channels")

    def test_resolve_backfill_channels_discovers_direct_and_mention_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            conversations = [
                {"id": "D123", "is_im": True},
                {"id": "C123", "is_channel": True},
                {"id": "C999", "is_channel": True},
            ]
            histories = {
                "C123": [{"text": "hello <@U-BOT>"}],
                "C999": [{"text": "plain chatter"}],
            }

            with (
                patch.object(self.mod, "auth_test", return_value={"ok": True, "user_id": "U-BOT"}),
                patch.object(self.mod, "conversations_list", return_value=conversations),
                patch.object(self.mod, "conversations_history", side_effect=lambda *args, **kwargs: histories[args[1]]),
            ):
                channels, source = self.mod.resolve_backfill_channels(cfg)

            self.assertEqual(channels, ["C123", "D123"])
            self.assertEqual(source, "discovered_recent_activity")
            state = json.loads(Path(cfg.state_path).read_text(encoding="utf-8"))
            self.assertEqual(state["known_channels"], ["C123", "D123"])

    def test_handle_event_skips_duplicate_when_same_ts_is_inflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            ev = {
                "type": "message",
                "channel": "C123",
                "user": "U123",
                "text": "メールからのタスク収集は動いていますか？",
                "ts": "1774327375.713519",
                "thread_ts": "1774327375.713519",
            }

            forward_calls = []

            def nested_forward(inner_cfg, mode, text, channel, thread_ts, user_id):
                forward_calls.append((channel, thread_ts))
                self.mod.handle_event(inner_cfg, dict(ev), source="backfill:interval")

            with patch.object(self.mod, "run_forward", side_effect=nested_forward):
                self.mod.handle_event(cfg, dict(ev), source="event")

            self.assertEqual(forward_calls, [("C123", "1774327375.713519")])
            state = json.loads(Path(cfg.state_path).read_text(encoding="utf-8"))
            self.assertEqual(state["channels"]["C123"]["last_seen_ts"], "1774327375.713519")


if __name__ == "__main__":
    main()

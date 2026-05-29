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
    script_path = scripts_dir / "roby-mail-reply-review.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_mail_reply_review_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RobyMailReplyReviewTests(TestCase):
    def setUp(self):
        self.mod = _load_module()

    def test_send_reply_adds_takata_cc(self):
        review = {
            "message_id": "m-1",
            "subject": "Re: 発注書のご確認",
            "sender": "\"佐田峰\" <sada@example.com>",
            "recipient_line": "サラーシュタインの佐田峰様",
            "account": "s.nigo@tokiwa-gi.com",
        }
        captured = {}

        def fake_run(cmd, capture_output, text, timeout):
            captured["cmd"] = cmd

            class Result:
                returncode = 0
                stdout = "sent"
                stderr = ""

            return Result()

        with (
            patch.object(self.mod, "build_reply_context", return_value={"cc": ""}),
            patch.object(self.mod.subprocess, "run", side_effect=fake_run),
        ):
            ok, detail = self.mod.send_reply(review, "本文です。")

        self.assertTrue(ok)
        self.assertEqual(detail, "sent")
        self.assertIn("--cc", captured["cmd"])
        cc_index = captured["cmd"].index("--cc") + 1
        self.assertIn(self.mod.TAKATA_EMAIL, captured["cmd"][cc_index])

    def test_send_reply_can_skip_takata_cc_when_disabled(self):
        review = {
            "message_id": "m-1",
            "subject": "Re: 発注書のご確認",
            "sender": "\"佐田峰\" <sada@example.com>",
            "recipient_line": "サラーシュタインの佐田峰様",
            "account": "s.nigo@tokiwa-gi.com",
            "include_takata_cc": False,
        }
        captured = {}

        def fake_run(cmd, capture_output, text, timeout):
            captured["cmd"] = cmd

            class Result:
                returncode = 0
                stdout = "sent"
                stderr = ""

            return Result()

        with (
            patch.object(self.mod, "build_reply_context", return_value={"cc": ""}),
            patch.object(self.mod.subprocess, "run", side_effect=fake_run),
        ):
            ok, detail = self.mod.send_reply(review, "本文です。")

        self.assertTrue(ok)
        self.assertEqual(detail, "sent")
        if "--cc" in captured["cmd"]:
            cc_index = captured["cmd"].index("--cc") + 1
            self.assertNotIn(self.mod.TAKATA_EMAIL, captured["cmd"][cc_index])

    def test_handle_slack_direct_body_sends_text_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {
                "reviews": {
                    "rid": {
                        "id": "rid",
                        "status": "pending",
                        "account": "s.nigo@tokiwa-gi.com",
                        "message_id": "m-1",
                        "thread_id": "thread-1",
                        "subject": "Re: 発注書のご確認",
                        "sender": "\"佐田峰\" <sada@example.com>",
                        "recipient_line": "サラーシュタインの佐田峰様",
                        "summary": "summary",
                        "candidates": [],
                        "channel": "C1",
                        "created_at": 1,
                        "llm_meta": {},
                    }
                },
                "by_thread": {"C1:thread-1": "rid"},
                "by_message": {"m-1": "rid"},
            }
            Path(self.mod.STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
            Path(self.mod.STATE_PATH).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            posts = []
            sent = []

            class Args:
                channel = "C1"
                thread = "thread-1"
                user = "U1"
                text = "直接返信の本文です。"

            with (
                patch.object(self.mod, "load_env_file", return_value=None),
                patch.dict(self.mod.os.environ, {"SLACK_BOT_TOKEN": "token"}, clear=False),
                patch.object(self.mod, "send_reply", return_value=(True, "sent")) as send_reply,
                patch.object(self.mod, "post_message", side_effect=lambda token, channel, text, thread_ts: posts.append((channel, text, thread_ts))),
                patch.object(self.mod, "append_log", side_effect=lambda payload: sent.append(payload)),
            ):
                rc = self.mod.handle_slack(Args())

            self.assertEqual(rc, 0)
            send_reply.assert_called_once()
            called_body = send_reply.call_args.args[1]
            self.assertEqual(called_body, "直接返信の本文です。")
            self.assertTrue(any("直接入力の本文" in text for _, text, _ in posts))

    def test_format_review_message_includes_cc_status(self):
        review = {
            "subject": "Re: 発注書のご確認",
            "sender": "\"佐田峰\" <sada@example.com>",
            "summary": "summary",
            "original_cc": "a.takata@tokiwa-gi.com",
            "original_cc_has_takata": True,
            "candidates": [{"label": "案1", "body": "本文"}],
        }
        text = self.mod.format_review_message(review)
        self.assertIn("CC判定", text)
        self.assertIn("高田さんあり", text)


if __name__ == "__main__":
    main()

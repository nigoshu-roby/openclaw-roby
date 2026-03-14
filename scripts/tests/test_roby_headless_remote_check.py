#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = pathlib.Path("/Users/shu/OpenClaw/scripts/roby-headless-remote-check.py")
SPEC = importlib.util.spec_from_file_location("roby_headless_remote_check", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class HeadlessRemoteCheckTests(unittest.TestCase):
    def test_ready_when_local_name_matches_active_ip_and_port_is_open(self) -> None:
        result = MODULE.evaluate_readiness(
            bonjour_host="shuM4-Mac-min.local",
            active_ipv4s={"en0": "192.168.0.111"},
            resolved_ipv4s=["127.0.0.1", "192.168.0.111"],
            screen_sharing_ready=True,
        )
        self.assertEqual("ready", result.status)
        self.assertEqual([], result.reasons)
        self.assertEqual(
            ["shuM4-Mac-min.local", "192.168.0.111"],
            result.preferred_targets,
        )

    def test_attention_when_local_name_does_not_resolve_to_active_ip(self) -> None:
        result = MODULE.evaluate_readiness(
            bonjour_host="shuM4-Mac-min.local",
            active_ipv4s={"en0": "192.168.0.111"},
            resolved_ipv4s=["127.0.0.1", "192.168.0.109"],
            screen_sharing_ready=True,
        )
        self.assertEqual("attention", result.status)
        self.assertIn(".local の解決結果が現在の LAN IP と一致しません", result.reasons)

    def test_attention_when_screen_sharing_port_is_closed(self) -> None:
        result = MODULE.evaluate_readiness(
            bonjour_host="shuM4-Mac-min.local",
            active_ipv4s={"en0": "192.168.0.111"},
            resolved_ipv4s=["192.168.0.111"],
            screen_sharing_ready=False,
        )
        self.assertEqual("attention", result.status)
        self.assertIn("画面共有ポート 5900 が待受していません", result.reasons)


if __name__ == "__main__":
    unittest.main()

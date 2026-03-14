#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable


def run_command(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()
    except Exception:
        return ""


def get_local_host_name() -> str:
    return (
        run_command(["scutil", "--get", "LocalHostName"])
        or socket.gethostname().split(".")[0]
        or "unknown-host"
    )


def get_interface_ipv4(interface: str) -> str | None:
    value = run_command(["ipconfig", "getifaddr", interface]).strip()
    return value or None


def get_active_ipv4s(interfaces: Iterable[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    for interface in interfaces:
        ip = get_interface_ipv4(interface)
        if ip:
            results[interface] = ip
    return results


def resolve_ipv4(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    resolved: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in resolved:
            resolved.append(ip)
    return resolved


def is_loopback(ip: str) -> bool:
    return ip.startswith("127.")


def check_port_open(host: str, port: int, timeout_sec: float = 0.75) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


@dataclass
class ReadinessResult:
    status: str
    reasons: list[str]
    preferred_targets: list[str]


def evaluate_readiness(
    bonjour_host: str,
    active_ipv4s: dict[str, str],
    resolved_ipv4s: list[str],
    screen_sharing_ready: bool,
) -> ReadinessResult:
    reasons: list[str] = []
    preferred_targets: list[str] = [bonjour_host]
    preferred_targets.extend(ip for ip in active_ipv4s.values() if ip not in preferred_targets)

    active_set = set(active_ipv4s.values())
    resolved_non_loopback = [ip for ip in resolved_ipv4s if not is_loopback(ip)]

    if not active_ipv4s:
        reasons.append("有効な LAN IPv4 が見つかりません")
    if not resolved_non_loopback:
        reasons.append(".local 名が LAN IPv4 に解決されません")
    elif active_set and not active_set.intersection(resolved_non_loopback):
        reasons.append(".local の解決結果が現在の LAN IP と一致しません")
    if not screen_sharing_ready:
        reasons.append("画面共有ポート 5900 が待受していません")

    status = "ready" if not reasons else "attention"
    return ReadinessResult(
        status=status,
        reasons=reasons,
        preferred_targets=preferred_targets,
    )


def build_payload() -> dict[str, object]:
    local_host_name = get_local_host_name()
    bonjour_host = f"{local_host_name}.local"
    active_ipv4s = get_active_ipv4s(["en0", "en1"])
    resolved_ipv4s = resolve_ipv4(bonjour_host)
    screen_sharing_ready = check_port_open("127.0.0.1", 5900)
    readiness = evaluate_readiness(
        bonjour_host=bonjour_host,
        active_ipv4s=active_ipv4s,
        resolved_ipv4s=resolved_ipv4s,
        screen_sharing_ready=screen_sharing_ready,
    )
    return {
        "bonjourHost": bonjour_host,
        "localHostName": local_host_name,
        "activeIpv4s": active_ipv4s,
        "resolvedIpv4s": resolved_ipv4s,
        "screenSharingReady": screen_sharing_ready,
        "status": readiness.status,
        "reasons": readiness.reasons,
        "preferredTargets": readiness.preferred_targets,
    }


def print_human(payload: dict[str, object]) -> None:
    active_ipv4s = payload["activeIpv4s"]
    assert isinstance(active_ipv4s, dict)
    resolved_ipv4s = payload["resolvedIpv4s"]
    assert isinstance(resolved_ipv4s, list)
    preferred_targets = payload["preferredTargets"]
    assert isinstance(preferred_targets, list)
    reasons = payload["reasons"]
    assert isinstance(reasons, list)

    print("Headless Remote Check")
    print(f"status: {payload['status']}")
    print(f"bonjour host: {payload['bonjourHost']}")
    if active_ipv4s:
        for interface, ip in active_ipv4s.items():
            print(f"active IPv4 [{interface}]: {ip}")
    else:
        print("active IPv4: none")
    if resolved_ipv4s:
        print("resolved IPv4:", ", ".join(resolved_ipv4s))
    else:
        print("resolved IPv4: none")
    print(f"screen sharing port 5900: {'open' if payload['screenSharingReady'] else 'closed'}")
    print("preferred targets:")
    for idx, target in enumerate(preferred_targets, start=1):
        print(f"  {idx}. {target}")
    if reasons:
        print("notes:")
        for reason in reasons:
            print(f"  - {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local headless remote readiness for iPad/VNC use.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()

    payload = build_payload()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_human(payload)
    return 0 if payload["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())

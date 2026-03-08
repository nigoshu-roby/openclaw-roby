#!/usr/bin/env python3
"""Shared Slack formatting helpers for PBS operational jobs."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple


def _to_bool_label(value: Any, ok: str = "OK", ng: str = "NG") -> str:
    return ok if bool(value) else ng


def _clean_value(value: Any, fallback: str = "-") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def build_slack_message(
    title: str,
    status: str,
    timestamp: str,
    summary_pairs: Sequence[Tuple[str, Any]],
    sections: Sequence[Tuple[str, Iterable[str]]],
) -> str:
    lines: List[str] = [f"【{title}】{status}", f"・実行時刻: {_clean_value(timestamp)}"]
    for label, value in summary_pairs:
        lines.append(f"・{label}: {_clean_value(value)}")
    for section_title, section_lines in sections:
        normalized = [line for line in section_lines if str(line).strip()]
        lines.append("")
        lines.append(f"■{section_title}")
        if normalized:
            lines.extend(normalized)
        else:
            lines.append("・なし")
    return "\n".join(lines)


def format_eval_slack(report: Dict[str, Any]) -> str:
    gate = (report.get("gates") or {}).get("ok", False)
    status = "PASS" if gate else "FAIL"
    summary_pairs = [
        ("評価ケース", f"{report.get('total', 0)}件"),
        ("成功 / 失敗", f"{report.get('passed', 0)} / {report.get('failed', 0)}"),
        ("品質ゲート", status),
        ("平均 / p95", f"{(report.get('latency') or {}).get('avg_ms', 0)}ms / {(report.get('latency') or {}).get('p95_ms', 0)}ms"),
    ]
    route_rows = []
    for route, row in sorted((report.get("routes") or {}).items()):
        route_rows.append(
            f"・{route}: total={row.get('total', 0)} / passed={row.get('passed', 0)} / failed={row.get('failed', 0)}"
        )
    gate_rows = [f"・{reason}" for reason in (report.get("gates") or {}).get("failures", [])]
    return build_slack_message(
        "PBS Evaluation Harness",
        status,
        _clean_value(report.get("ts")),
        summary_pairs,
        [
            ("ルート別結果", route_rows),
            ("ゲート失敗理由", gate_rows),
        ],
    )


def format_drill_slack(report: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    failed_checks = [str(x.get("id")) for x in rows if (not x.get("ok") and not x.get("skipped"))]
    skipped_checks = [str(x.get("id")) for x in rows if x.get("skipped")]
    status = "FAIL" if int(report.get("failed", 0)) > 0 else "PASS"
    summary_pairs = [
        ("チェック数", f"{report.get('total', 0)}件"),
        ("成功 / 失敗 / スキップ", f"{report.get('passed', 0)} / {report.get('failed', 0)} / {report.get('skipped', 0)}"),
        ("全体結果", _to_bool_label(report.get("all_ok"), "正常", "要対応")),
    ]
    return build_slack_message(
        "PBS Runbook Drill",
        status,
        _clean_value(report.get("ts")),
        summary_pairs,
        [
            ("失敗チェック", [f"・{item}" for item in failed_checks]),
            ("スキップチェック", [f"・{item}" for item in skipped_checks]),
        ],
    )


def format_weekly_slack(report: Dict[str, Any]) -> str:
    eval_s = report.get("eval") or {}
    drill_s = report.get("drill") or {}
    feedback_s = report.get("feedback") or {}
    audit_s = report.get("audit") or {}
    freshness = report.get("freshness") or {}
    ab_s = report.get("ab") or {}
    status = "WARN" if (
        int(eval_s.get("failed_runs", 0)) > 0
        or int(drill_s.get("failed_runs", 0)) > 0
        or not bool(audit_s.get("ok", False))
        or int(freshness.get("stale_count", 0)) > 0
    ) else "OK"
    summary_pairs = [
        ("期間", f"{report.get('window_days', 0)}日"),
        ("Evaluation", f"runs={eval_s.get('runs', 0)} / failed={eval_s.get('failed_runs', 0)}"),
        ("Runbook Drill", f"runs={drill_s.get('runs', 0)} / failed={drill_s.get('failed_runs', 0)}"),
        ("Feedback Loop", f"runs={feedback_s.get('runs', 0)} / actionable={feedback_s.get('actionable_count', 0)}"),
        ("監査", _to_bool_label(audit_s.get("ok"), "正常", "異常")),
    ]
    ops_rows = []
    for key in ("minutes_sync", "gmail_triage", "notion_sync", "self_growth"):
        row = (report.get("ops") or {}).get(key, {})
        ops_rows.append(f"・{key}: runs={row.get('runs', 0)} / errors={row.get('errors', 0)}")
    freshness_rows = [
        f"・stale_count: {freshness.get('stale_count', 0)}",
        f"・stale_components: {', '.join(freshness.get('stale_components', [])) or '-'}",
        f"・AB Router runs: {ab_s.get('runs', 0)} / guard_applied={ab_s.get('guard_applied_runs', 0)}",
    ]
    feedback_rows = [
        f"・reviewed: {feedback_s.get('reviewed_count', 0)}",
        f"・actionable: {feedback_s.get('actionable_count', 0)}",
        f"・good / bad / missed: {feedback_s.get('good', 0)} / {feedback_s.get('bad', 0)} / {feedback_s.get('missed', 0)}",
    ]
    return build_slack_message(
        "PBS 週次運用レポート",
        status,
        _clean_value(report.get("generated_at")),
        summary_pairs,
        [
            ("運用実行数", ops_rows),
            ("鮮度とAB Router", freshness_rows),
            ("Neuronic評価", feedback_rows),
        ],
    )

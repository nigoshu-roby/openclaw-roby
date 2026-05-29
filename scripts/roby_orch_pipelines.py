#!/usr/bin/env python3
import re
import shlex
from pathlib import Path
from typing import Any, Dict

from roby_orch_profiles import apply_gmail_profile, apply_minutes_llm_profile


def _shell_command(cmd: list[str]) -> str:
    return " ".join(shlex.quote(x) for x in cmd)


def build_subprocess_job_plan(
    route: str,
    cmd: list[str],
    env: Dict[str, str],
) -> Dict[str, Any]:
    return {
        "cmd": cmd,
        "child_env": dict(env),
        "result": {
            "route": route,
            "command": _shell_command(cmd),
            "executed": False,
        },
    }


def build_notion_sync_plan(
    env: Dict[str, str],
    *,
    notion_sync_script: Path,
    route: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    owner = env.get("ROBY_GH_OWNER", "nigoshu-roby")
    project_number = env.get("ROBY_GH_PROJECT_NUMBER", "1")
    page_id = env.get("ROBY_NOTION_SYNC_PAGE_ID", "")
    cmd = [
        "python3", str(notion_sync_script),
        "--owner", owner,
        "--project-number", str(project_number),
    ]
    if page_id:
        cmd.extend(["--page-id", page_id])
    if dry_run:
        cmd.append("--dry-run")
    return build_subprocess_job_plan(route, cmd, env)


def build_feedback_sync_plan(
    env: Dict[str, str],
    *,
    feedback_sync_script: Path,
    route: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    cmd = ["python3", str(feedback_sync_script), "--json"]
    if dry_run:
        cmd.append("--dry-run")
    return build_subprocess_job_plan(route, cmd, env)


def build_memory_sync_plan(
    env: Dict[str, str],
    *,
    memory_sync_script: Path,
    route: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    cmd = ["python3", str(memory_sync_script), "--json"]
    if dry_run:
        cmd.append("--dry-run")
    return build_subprocess_job_plan(route, cmd, env)


def build_eval_harness_plan(
    env: Dict[str, str],
    *,
    eval_harness_script: Path,
    route: str,
    verbose: bool = False,
) -> Dict[str, Any]:
    cmd = ["python3", str(eval_harness_script), "--json"]
    if verbose:
        cmd.append("--verbose")
    return build_subprocess_job_plan(route, cmd, env)


def build_runbook_drill_plan(
    env: Dict[str, str],
    *,
    drill_script: Path,
    route: str,
) -> Dict[str, Any]:
    cmd = ["python3", str(drill_script), "--json"]
    return build_subprocess_job_plan(route, cmd, env)


def build_weekly_report_plan(
    env: Dict[str, str],
    *,
    weekly_report_script: Path,
    route: str,
) -> Dict[str, Any]:
    cmd = ["python3", str(weekly_report_script), "--json"]
    return build_subprocess_job_plan(route, cmd, env)


def build_minutes_pipeline_plan(
    intent_text: str,
    env: Dict[str, str],
    *,
    minutes_script: Path,
    verbose: bool,
    route: str,
) -> Dict[str, Any]:
    select_match = re.search(r"--select\s+\"([^\"]+)\"|--select\s+'([^']+)'|--select\s+(\S+)", intent_text)
    select_val = None
    if select_match:
        select_val = next((g for g in select_match.groups() if g), None)

    run_mode = "list"
    if any(k in intent_text for k in ["実行", "取り込み", "連携", "Neuronic", "タスク化", "登録", "タスク登録"]):
        run_mode = "run"

    cmd = ["python3", str(minutes_script)]
    if run_mode == "run":
        cmd.append("--run")
    else:
        cmd.append("--list")
    if select_val:
        cmd.extend(["--select", select_val])
    elif run_mode == "run":
        cron_context = env.get("ROBY_ORCH_CRON_CONTEXT", "0") == "1"
        policy = env.get("ROBY_ORCH_MINUTES_POLICY", "").strip()
        if not policy and cron_context:
            policy = "ops_default"
        if policy:
            cmd.extend(["--policy", policy])
        if env.get("ROBY_ORCH_MINUTES_FORCE", "0") == "1":
            cmd.append("--force")
        if env.get("ROBY_ORCH_MINUTES_REFRESH", "0") == "1":
            cmd.append("--refresh")
        if env.get("ROBY_ORCH_MINUTES_SKIP_NOTION", "0") == "1":
            cmd.append("--skip-notion")
        if env.get("ROBY_ORCH_MINUTES_SKIP_GDOCS", "0") == "1":
            cmd.append("--skip-gdocs")
        if env.get("ROBY_ORCH_MINUTES_DAYS", "").strip():
            cmd.extend(["--days", env["ROBY_ORCH_MINUTES_DAYS"].strip()])
        max_items = env.get("ROBY_ORCH_MINUTES_MAX", "").strip()
        if not max_items and cron_context:
            max_items = env.get("ROBY_ORCH_MINUTES_CRON_MAX", "4").strip()
        if max_items:
            cmd.extend(["--max", max_items])
    if verbose:
        cmd.append("--debug")

    profile, profile_env = apply_minutes_llm_profile(env)
    child_env = dict(env)
    child_env.update(profile_env)
    if child_env.get("ROBY_ORCH_CRON_CONTEXT", "0") == "1":
        cron_local_preprocess = env.get("ROBY_ORCH_MINUTES_CRON_LOCAL_PREPROCESS", "0").strip()
        if cron_local_preprocess:
            child_env["MINUTES_LOCAL_PREPROCESS_ENABLE"] = cron_local_preprocess
    if child_env.get("ROBY_ORCH_CRON_CONTEXT", "0") == "1" and "MINUTES_DOC_TIMEOUT_SEC" not in env:
        child_env["MINUTES_DOC_TIMEOUT_SEC"] = child_env.get("ROBY_ORCH_MINUTES_CRON_DOC_TIMEOUT_SEC", "45") or "45"

    result = {
        "route": route,
        "mode": run_mode,
        "llm_profile": profile,
        "llm_overrides": profile_env,
        "command": _shell_command(cmd),
        "executed": False,
    }
    return {
        "cmd": cmd,
        "child_env": child_env,
        "result": result,
        "profile": profile,
        "profile_env": profile_env,
        "run_mode": run_mode,
    }


def build_gmail_pipeline_plan(
    message: str,
    env: Dict[str, str],
    *,
    gmail_triage_script: Path,
    verbose: bool,
    route: str,
) -> Dict[str, Any]:
    account = env.get("ROBY_GMAIL_ACCOUNT") or env.get("GOG_ACCOUNT") or ""
    query = env.get("ROBY_GMAIL_QUERY", "newer_than:1d in:inbox")
    max_items = env.get("ROBY_GMAIL_MAX", "20")

    m_query = re.search(r"(newer_than:\S+.*|in:inbox.*)$", message, flags=re.IGNORECASE)
    if m_query:
        query = m_query.group(1).strip()
    m_max = re.search(r"--max\s+(\d+)|(\d+)\s*件", message)
    if m_max:
        max_items = next((g for g in m_max.groups() if g), max_items)

    cmd = [
        "python3", str(gmail_triage_script),
        "--account", account,
        "--query", query,
        "--max", str(max_items),
    ]
    if verbose:
        cmd.append("--verbose")
    if any(k in message for k in ["dry-run", "ドライラン", "確認だけ", "一覧だけ"]):
        cmd.append("--dry-run")

    profile, profile_env = apply_gmail_profile(env)
    child_env = dict(env)
    child_env.update(profile_env)

    result = {
        "route": route,
        "command": _shell_command(cmd),
        "executed": False,
        "llm_profile": profile,
        "llm_overrides": profile_env,
        "account": account,
        "query": query,
        "max": int(max_items),
    }
    return {
        "cmd": cmd,
        "child_env": child_env,
        "result": result,
        "profile": profile,
        "profile_env": profile_env,
        "account": account,
        "query": query,
        "max_items": max_items,
    }

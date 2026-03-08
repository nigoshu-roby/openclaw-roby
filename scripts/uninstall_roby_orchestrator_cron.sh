#!/usr/bin/env bash
set -euo pipefail

TAG_SELF="ROBY_ORCH_CRON_SELF_GROWTH"
TAG_MINUTES="ROBY_ORCH_CRON_MINUTES_SYNC"
TAG_GMAIL="ROBY_ORCH_CRON_GMAIL_TRIAGE"
TAG_EVAL="ROBY_ORCH_CRON_EVAL_HARNESS"
TAG_DRILL="ROBY_ORCH_CRON_RUNBOOK_DRILL"
TAG_NOTION="ROBY_ORCH_CRON_NOTION_SYNC"
TAG_FEEDBACK="ROBY_ORCH_CRON_FEEDBACK_SYNC"
TAG_WEEKLY="ROBY_ORCH_CRON_WEEKLY_REPORT"

current="$(crontab -l 2>/dev/null || true)"
filtered="$(printf "%s\n" "$current" | sed "/${TAG_SELF}/d;/${TAG_MINUTES}/d;/${TAG_GMAIL}/d;/${TAG_EVAL}/d;/${TAG_DRILL}/d;/${TAG_NOTION}/d;/${TAG_FEEDBACK}/d;/${TAG_WEEKLY}/d")"
printf "%s\n" "$filtered" | awk 'NF' | crontab -

echo "Removed orchestrator cron jobs (${TAG_SELF}, ${TAG_MINUTES}, ${TAG_GMAIL}, ${TAG_EVAL}, ${TAG_DRILL}, ${TAG_NOTION}, ${TAG_FEEDBACK}, ${TAG_WEEKLY})."
echo
echo "Current crontab:"
crontab -l

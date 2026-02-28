#!/usr/bin/env bash
set -euo pipefail

TAG_SELF="ROBY_ORCH_CRON_SELF_GROWTH"
TAG_MINUTES="ROBY_ORCH_CRON_MINUTES_SYNC"
TAG_GMAIL="ROBY_ORCH_CRON_GMAIL_TRIAGE"

current="$(crontab -l 2>/dev/null || true)"
filtered="$(printf "%s\n" "$current" | sed "/${TAG_SELF}/d;/${TAG_MINUTES}/d;/${TAG_GMAIL}/d")"
printf "%s\n" "$filtered" | awk 'NF' | crontab -

echo "Removed orchestrator cron jobs (${TAG_SELF}, ${TAG_MINUTES}, ${TAG_GMAIL})."
echo
echo "Current crontab:"
crontab -l


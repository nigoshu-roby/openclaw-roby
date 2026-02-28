#!/usr/bin/env bash
set -euo pipefail

# Install persistent orchestrator cron jobs.
# Defaults (safe baseline):
# - self_growth : every hour at minute 5
# - minutes_sync: every 2 hours at minute 15
# - gmail_triage: every 30 minutes
#
# Usage:
#   scripts/install_roby_orchestrator_cron.sh
#   SELF_GROWTH_CRON="5 * * * *" MINUTES_SYNC_CRON="15 */2 * * *" GMAIL_TRIAGE_CRON="*/30 * * * *" scripts/install_roby_orchestrator_cron.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

SELF_GROWTH_CRON="${SELF_GROWTH_CRON:-5 * * * *}"
MINUTES_SYNC_CRON="${MINUTES_SYNC_CRON:-15 */2 * * *}"
GMAIL_TRIAGE_CRON="${GMAIL_TRIAGE_CRON:-*/30 * * * *}"

SELF_GROWTH_TIMEOUT="${SELF_GROWTH_TIMEOUT:-900}"
MINUTES_SYNC_TIMEOUT="${MINUTES_SYNC_TIMEOUT:-1800}"
GMAIL_TRIAGE_TIMEOUT="${GMAIL_TRIAGE_TIMEOUT:-900}"

TAG_SELF="ROBY_ORCH_CRON_SELF_GROWTH"
TAG_MINUTES="ROBY_ORCH_CRON_MINUTES_SYNC"
TAG_GMAIL="ROBY_ORCH_CRON_GMAIL_TRIAGE"

CMD_SELF="cd \"$ROOT_DIR\" && /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" self_growth ${SELF_GROWTH_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_self_growth.log\" 2>&1"
CMD_MINUTES="cd \"$ROOT_DIR\" && /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" minutes_sync ${MINUTES_SYNC_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_minutes_sync.log\" 2>&1"
CMD_GMAIL="cd \"$ROOT_DIR\" && /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" gmail_triage ${GMAIL_TRIAGE_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_gmail_triage.log\" 2>&1"

LINE_SELF="${SELF_GROWTH_CRON} ${CMD_SELF} # ${TAG_SELF}"
LINE_MINUTES="${MINUTES_SYNC_CRON} ${CMD_MINUTES} # ${TAG_MINUTES}"
LINE_GMAIL="${GMAIL_TRIAGE_CRON} ${CMD_GMAIL} # ${TAG_GMAIL}"

current="$(crontab -l 2>/dev/null || true)"
filtered="$(printf "%s\n" "$current" | sed "/${TAG_SELF}/d;/${TAG_MINUTES}/d;/${TAG_GMAIL}/d")"

{
  printf "%s\n" "$filtered"
  printf "%s\n" "$LINE_SELF"
  printf "%s\n" "$LINE_MINUTES"
  printf "%s\n" "$LINE_GMAIL"
} | awk 'NF' | crontab -

echo "Installed orchestrator cron jobs:"
echo "$LINE_SELF"
echo "$LINE_MINUTES"
echo "$LINE_GMAIL"
echo
echo "Current crontab:"
crontab -l


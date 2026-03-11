#!/usr/bin/env bash
set -euo pipefail

# Install persistent orchestrator cron jobs.
# Defaults (safe baseline):
# - self_growth : every hour at minute 5
# - minutes_sync: every 2 hours at minute 15 (last 3 days, max 4)
# - gmail_triage: every 30 minutes
# - eval_harness: disabled by default (set ROBY_ORCH_ENABLE_EVAL=1)
# - runbook_drill: disabled by default (set ROBY_ORCH_ENABLE_DRILL=1)
# - notion_sync: disabled by default (set ROBY_ORCH_ENABLE_NOTION_SYNC=1)
# - feedback_sync: disabled by default (set ROBY_ORCH_ENABLE_FEEDBACK_SYNC=1)
# - memory_sync: every day at 09:55 / 15:55 / 21:55
# - weekly_report: disabled by default (set ROBY_ORCH_ENABLE_WEEKLY_REPORT=1)
#
# Usage:
#   scripts/install_roby_orchestrator_cron.sh
#   SELF_GROWTH_CRON="5 * * * *" MINUTES_SYNC_CRON="15 */2 * * *" GMAIL_TRIAGE_CRON="*/30 * * * *" WEEKLY_REPORT_CRON="30 9 * * 1" scripts/install_roby_orchestrator_cron.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KEYCHAIN_SERVICE="${ROBY_KEYCHAIN_SERVICE:-roby-pbs}"
SECRET_WRAPPER="${ROBY_SECRET_WRAPPER:-$ROOT_DIR/scripts/roby-keychain-run.sh}"

SELF_GROWTH_CRON="${SELF_GROWTH_CRON:-5 * * * *}"
MINUTES_SYNC_CRON="${MINUTES_SYNC_CRON:-15 */2 * * *}"
MINUTES_SYNC_DAYS="${MINUTES_SYNC_DAYS:-3}"
MINUTES_SYNC_MAX="${MINUTES_SYNC_MAX:-4}"
GMAIL_TRIAGE_CRON="${GMAIL_TRIAGE_CRON:-*/30 * * * *}"
EVAL_HARNESS_CRON="${EVAL_HARNESS_CRON:-35 */6 * * *}"
RUNBOOK_DRILL_CRON="${RUNBOOK_DRILL_CRON:-20 8 * * 1}"
NOTION_SYNC_CRON="${NOTION_SYNC_CRON:-20 9 * * *}"
FEEDBACK_SYNC_CRON="${FEEDBACK_SYNC_CRON:-50 9,15,21 * * *}"
MEMORY_SYNC_CRON="${MEMORY_SYNC_CRON:-55 9,15,21 * * *}"
WEEKLY_REPORT_CRON="${WEEKLY_REPORT_CRON:-30 9 * * 1}"

SELF_GROWTH_TIMEOUT="${SELF_GROWTH_TIMEOUT:-900}"
MINUTES_SYNC_TIMEOUT="${MINUTES_SYNC_TIMEOUT:-1800}"
GMAIL_TRIAGE_TIMEOUT="${GMAIL_TRIAGE_TIMEOUT:-900}"
EVAL_HARNESS_TIMEOUT="${EVAL_HARNESS_TIMEOUT:-900}"
RUNBOOK_DRILL_TIMEOUT="${RUNBOOK_DRILL_TIMEOUT:-1200}"
NOTION_SYNC_TIMEOUT="${NOTION_SYNC_TIMEOUT:-600}"
FEEDBACK_SYNC_TIMEOUT="${FEEDBACK_SYNC_TIMEOUT:-600}"
MEMORY_SYNC_TIMEOUT="${MEMORY_SYNC_TIMEOUT:-600}"
WEEKLY_REPORT_TIMEOUT="${WEEKLY_REPORT_TIMEOUT:-900}"

TAG_SELF="ROBY_ORCH_CRON_SELF_GROWTH"
TAG_MINUTES="ROBY_ORCH_CRON_MINUTES_SYNC"
TAG_GMAIL="ROBY_ORCH_CRON_GMAIL_TRIAGE"
TAG_EVAL="ROBY_ORCH_CRON_EVAL_HARNESS"
TAG_DRILL="ROBY_ORCH_CRON_RUNBOOK_DRILL"
TAG_NOTION="ROBY_ORCH_CRON_NOTION_SYNC"
TAG_FEEDBACK="ROBY_ORCH_CRON_FEEDBACK_SYNC"
TAG_MEMORY="ROBY_ORCH_CRON_MEMORY_SYNC"
TAG_WEEKLY="ROBY_ORCH_CRON_WEEKLY_REPORT"

BASE_ENV="ROBY_KEYCHAIN_SERVICE=\"$KEYCHAIN_SERVICE\" ROBY_SECRET_WRAPPER=\"$SECRET_WRAPPER\""
MINUTES_ENV="${BASE_ENV} ROBY_ORCH_MINUTES_DAYS=\"$MINUTES_SYNC_DAYS\" ROBY_ORCH_MINUTES_CRON_MAX=\"$MINUTES_SYNC_MAX\""
CMD_SELF="cd \"$ROOT_DIR\" && ${BASE_ENV} /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" self_growth ${SELF_GROWTH_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_self_growth.log\" 2>&1"
CMD_MINUTES="cd \"$ROOT_DIR\" && ${MINUTES_ENV} /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" minutes_sync ${MINUTES_SYNC_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_minutes_sync.log\" 2>&1"
CMD_GMAIL="cd \"$ROOT_DIR\" && ${BASE_ENV} /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" gmail_triage ${GMAIL_TRIAGE_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_gmail_triage.log\" 2>&1"
CMD_EVAL="cd \"$ROOT_DIR\" && ${BASE_ENV} /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" eval_harness ${EVAL_HARNESS_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_eval_harness.log\" 2>&1"
CMD_DRILL="cd \"$ROOT_DIR\" && ${BASE_ENV} /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" runbook_drill ${RUNBOOK_DRILL_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_runbook_drill.log\" 2>&1"
CMD_NOTION="cd \"$ROOT_DIR\" && ${BASE_ENV} /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" notion_sync ${NOTION_SYNC_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_notion_sync.log\" 2>&1"
CMD_FEEDBACK="cd \"$ROOT_DIR\" && ${BASE_ENV} /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" feedback_sync ${FEEDBACK_SYNC_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_feedback_sync.log\" 2>&1"
CMD_MEMORY="cd \"$ROOT_DIR\" && ${BASE_ENV} /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" memory_sync ${MEMORY_SYNC_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_memory_sync.log\" 2>&1"
CMD_WEEKLY="cd \"$ROOT_DIR\" && ${BASE_ENV} /bin/bash \"$ROOT_DIR/scripts/roby-cron-dispatch.sh\" weekly_report ${WEEKLY_REPORT_TIMEOUT} >> \"$HOME/.openclaw/roby/cron_weekly_report.log\" 2>&1"

LINE_SELF="${SELF_GROWTH_CRON} ${CMD_SELF} # ${TAG_SELF}"
LINE_MINUTES="${MINUTES_SYNC_CRON} ${CMD_MINUTES} # ${TAG_MINUTES}"
LINE_GMAIL="${GMAIL_TRIAGE_CRON} ${CMD_GMAIL} # ${TAG_GMAIL}"
LINE_EVAL="${EVAL_HARNESS_CRON} ${CMD_EVAL} # ${TAG_EVAL}"
LINE_DRILL="${RUNBOOK_DRILL_CRON} ${CMD_DRILL} # ${TAG_DRILL}"
LINE_NOTION="${NOTION_SYNC_CRON} ${CMD_NOTION} # ${TAG_NOTION}"
LINE_FEEDBACK="${FEEDBACK_SYNC_CRON} ${CMD_FEEDBACK} # ${TAG_FEEDBACK}"
LINE_MEMORY="${MEMORY_SYNC_CRON} ${CMD_MEMORY} # ${TAG_MEMORY}"
LINE_WEEKLY="${WEEKLY_REPORT_CRON} ${CMD_WEEKLY} # ${TAG_WEEKLY}"

current="$(crontab -l 2>/dev/null || true)"
filtered="$(printf "%s\n" "$current" | sed "/${TAG_SELF}/d;/${TAG_MINUTES}/d;/${TAG_GMAIL}/d;/${TAG_EVAL}/d;/${TAG_DRILL}/d;/${TAG_NOTION}/d;/${TAG_FEEDBACK}/d;/${TAG_MEMORY}/d;/${TAG_WEEKLY}/d")"

{
  printf "%s\n" "$filtered"
  printf "%s\n" "$LINE_SELF"
  printf "%s\n" "$LINE_MINUTES"
  printf "%s\n" "$LINE_GMAIL"
  if [[ "${ROBY_ORCH_ENABLE_EVAL:-0}" == "1" ]]; then
    printf "%s\n" "$LINE_EVAL"
  fi
  if [[ "${ROBY_ORCH_ENABLE_DRILL:-0}" == "1" ]]; then
    printf "%s\n" "$LINE_DRILL"
  fi
  if [[ "${ROBY_ORCH_ENABLE_NOTION_SYNC:-0}" == "1" ]]; then
    printf "%s\n" "$LINE_NOTION"
  fi
  if [[ "${ROBY_ORCH_ENABLE_FEEDBACK_SYNC:-0}" == "1" ]]; then
    printf "%s\n" "$LINE_FEEDBACK"
  fi
  printf "%s\n" "$LINE_MEMORY"
  if [[ "${ROBY_ORCH_ENABLE_WEEKLY_REPORT:-0}" == "1" ]]; then
    printf "%s\n" "$LINE_WEEKLY"
  fi
} | awk 'NF' | crontab -

echo "Installed orchestrator cron jobs:"
echo "$LINE_SELF"
echo "$LINE_MINUTES"
echo "$LINE_GMAIL"
if [[ "${ROBY_ORCH_ENABLE_EVAL:-0}" == "1" ]]; then
  echo "$LINE_EVAL"
else
  echo "(not installed) eval_harness: set ROBY_ORCH_ENABLE_EVAL=1 to enable"
fi
if [[ "${ROBY_ORCH_ENABLE_DRILL:-0}" == "1" ]]; then
  echo "$LINE_DRILL"
else
  echo "(not installed) runbook_drill: set ROBY_ORCH_ENABLE_DRILL=1 to enable"
fi
if [[ "${ROBY_ORCH_ENABLE_NOTION_SYNC:-0}" == "1" ]]; then
  echo "$LINE_NOTION"
else
  echo "(not installed) notion_sync: set ROBY_ORCH_ENABLE_NOTION_SYNC=1 to enable"
fi
if [[ "${ROBY_ORCH_ENABLE_FEEDBACK_SYNC:-0}" == "1" ]]; then
  echo "$LINE_FEEDBACK"
else
  echo "(not installed) feedback_sync: set ROBY_ORCH_ENABLE_FEEDBACK_SYNC=1 to enable"
fi
echo "$LINE_MEMORY"
if [[ "${ROBY_ORCH_ENABLE_WEEKLY_REPORT:-0}" == "1" ]]; then
  echo "$LINE_WEEKLY"
else
  echo "(not installed) weekly_report: set ROBY_ORCH_ENABLE_WEEKLY_REPORT=1 to enable"
fi
echo
echo "Current crontab:"
crontab -l

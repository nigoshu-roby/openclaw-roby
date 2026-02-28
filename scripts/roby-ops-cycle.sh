#!/usr/bin/env bash
set -euo pipefail

# One safe ops cycle for Roby:
# - self_growth
# - minutes_sync
# - gmail_triage
#
# Optional env:
#   PBS_3H_END_EPOCH : unix epoch. If exceeded, this script removes the
#                      temporary cron entry tagged with PBS_3H_SPRINT.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${HOME}/.openclaw/roby"
LOG_FILE="${LOG_DIR}/three_hour_sprint.log"
mkdir -p "$LOG_DIR"

now_epoch="$(date +%s)"
ts="$(date '+%Y-%m-%d %H:%M:%S %Z')"

cleanup_temp_cron() {
  local current filtered
  current="$(crontab -l 2>/dev/null || true)"
  filtered="$(printf "%s\n" "$current" | sed '/PBS_3H_SPRINT/d')"
  printf "%s\n" "$filtered" | awk 'NF' | crontab -
}

if [[ -n "${PBS_3H_END_EPOCH:-}" ]] && [[ "$now_epoch" -ge "${PBS_3H_END_EPOCH}" ]]; then
  {
    echo "[$ts] INFO: sprint window ended. removing temporary cron."
  } >>"$LOG_FILE"
  cleanup_temp_cron
  exit 0
fi

run_task() {
  local name="$1"
  local timeout_sec="$2"
  shift 2
  local start_ts
  start_ts="$(date '+%Y-%m-%d %H:%M:%S %Z')"
  {
    echo "[$start_ts] START: $name (timeout=${timeout_sec}s)"
  } >>"$LOG_FILE"

  (
    "$@"
  ) >>"$LOG_FILE" 2>&1 &
  local pid=$!
  local start_epoch
  start_epoch="$(date +%s)"

  while kill -0 "$pid" 2>/dev/null; do
    local now
    now="$(date +%s)"
    if (( now - start_epoch > timeout_sec )); then
      {
        echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] TIMEOUT: $name (pid=$pid)"
      } >>"$LOG_FILE"
      kill "$pid" 2>/dev/null || true
      sleep 2
      kill -9 "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      {
        echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] FAIL : $name (timeout)"
      } >>"$LOG_FILE"
      return 124
    fi
    sleep 1
  done

  if wait "$pid"; then
    {
      echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] DONE : $name"
    } >>"$LOG_FILE"
  else
    local rc=$?
    {
      echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] FAIL : $name (rc=$rc)"
    } >>"$LOG_FILE"
  fi
}

cd "$ROOT_DIR"

run_task "self_growth" 900 python3 scripts/roby-orchestrator.py --cron-task self_growth --execute --json
run_task "minutes_sync" 1800 python3 scripts/roby-orchestrator.py --cron-task minutes_sync --execute --json
run_task "gmail_triage" 900 python3 scripts/roby-orchestrator.py --cron-task gmail_triage --execute --json

echo "[$ts] CYCLE COMPLETE" >>"$LOG_FILE"

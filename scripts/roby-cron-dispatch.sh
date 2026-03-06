#!/usr/bin/env bash
set -euo pipefail

# Dispatch one orchestrator cron task with:
# - per-task lock (skip if already running)
# - timeout guard
# - structured logging
#
# Usage:
#   scripts/roby-cron-dispatch.sh <self_growth|minutes_sync|gmail_triage|notion_sync|eval_harness|runbook_drill|weekly_report> [timeout_sec]

TASK="${1:-}"
TIMEOUT_SEC="${2:-}"

if [[ -z "$TASK" ]]; then
  echo "Usage: $0 <self_growth|minutes_sync|gmail_triage|notion_sync|eval_harness|runbook_drill|weekly_report> [timeout_sec]" >&2
  exit 2
fi

case "$TASK" in
  self_growth)
    DEFAULT_TIMEOUT=900
    ;;
  minutes_sync)
    DEFAULT_TIMEOUT=1800
    ;;
  gmail_triage)
    DEFAULT_TIMEOUT=900
    ;;
  notion_sync)
    DEFAULT_TIMEOUT=600
    ;;
  eval_harness)
    DEFAULT_TIMEOUT=900
    ;;
  runbook_drill)
    DEFAULT_TIMEOUT=1200
    ;;
  weekly_report)
    DEFAULT_TIMEOUT=900
    ;;
  *)
    echo "Unknown task: $TASK" >&2
    exit 2
    ;;
esac

if [[ -z "$TIMEOUT_SEC" ]]; then
  TIMEOUT_SEC="$DEFAULT_TIMEOUT"
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${HOME}/.openclaw/roby"
LOG_FILE="${LOG_DIR}/cron_${TASK}.log"
LOCK_DIR="/tmp/roby-cron-${TASK}.lock"
ENV_PATH="${HOME}/.openclaw/.env"
SECRET_WRAPPER="${ROBY_SECRET_WRAPPER:-}"

mkdir -p "$LOG_DIR"

# Cron-safe runtime paths (ensure gog/python3 from Homebrew are available)
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] FAIL: python3 not found in PATH=${PATH}" >>"$LOG_FILE"
  exit 127
fi

now() { date '+%Y-%m-%d %H:%M:%S %Z'; }

# load webhook from ~/.openclaw/.env when not injected by runner
if [[ -z "${SLACK_WEBHOOK_URL:-}" && -f "$ENV_PATH" ]]; then
  while IFS='=' read -r k v; do
    [[ -z "$k" ]] && continue
    [[ "$k" =~ ^[[:space:]]*# ]] && continue
    if [[ "$k" == "SLACK_WEBHOOK_URL" ]]; then
      SLACK_WEBHOOK_URL="${v%\"}"
      SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL#\"}"
      SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL%\'}"
      SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL#\'}"
      break
    fi
  done < "$ENV_PATH"
fi

notify_slack_fail() {
  local reason="${1:-unknown}"
  if [[ -z "${SLACK_WEBHOOK_URL:-}" ]]; then
    return 0
  fi
  local msg
  msg="【Roby Cron 異常通知】
・タスク: ${TASK}
・理由: ${reason}
・時刻: $(now)
・ホスト: $(hostname)
・ログ: ${LOG_FILE}"
  "$PYTHON_BIN" - "$SLACK_WEBHOOK_URL" "$msg" <<'PY' >/dev/null 2>&1 || true
import json
import sys
import urllib.request

url = sys.argv[1]
text = sys.argv[2]
data = json.dumps({"text": text}).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=8):
    pass
PY
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[$(now)] SKIP: ${TASK} already running (lock: ${LOCK_DIR})" >>"$LOG_FILE"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

cd "$ROOT_DIR"
echo "[$(now)] START: task=${TASK} timeout=${TIMEOUT_SEC}s" >>"$LOG_FILE"

(
  if [[ -n "$SECRET_WRAPPER" ]]; then
    ROBY_CRON_TASK="$TASK" \
    ROBY_CRON_PYTHON="$PYTHON_BIN" \
    bash -lc "$SECRET_WRAPPER \"\$ROBY_CRON_PYTHON\" scripts/roby-orchestrator.py --cron-task \"\$ROBY_CRON_TASK\" --execute --json"
  else
    "$PYTHON_BIN" scripts/roby-orchestrator.py --cron-task "$TASK" --execute --json
  fi
) >>"$LOG_FILE" 2>&1 &
pid=$!

start_epoch="$(date +%s)"
while kill -0 "$pid" 2>/dev/null; do
  now_epoch="$(date +%s)"
  if (( now_epoch - start_epoch > TIMEOUT_SEC )); then
    echo "[$(now)] TIMEOUT: task=${TASK} pid=${pid}" >>"$LOG_FILE"
    kill "$pid" 2>/dev/null || true
    sleep 2
    kill -9 "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    echo "[$(now)] FAIL: task=${TASK} reason=timeout" >>"$LOG_FILE"
    notify_slack_fail "timeout"
    exit 124
  fi
  sleep 1
done

if wait "$pid"; then
  echo "[$(now)] DONE: task=${TASK}" >>"$LOG_FILE"
else
  rc=$?
  echo "[$(now)] FAIL: task=${TASK} rc=${rc}" >>"$LOG_FILE"
  notify_slack_fail "rc=${rc}"
  exit "$rc"
fi

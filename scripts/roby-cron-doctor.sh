#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KEYCHAIN_SERVICE="${ROBY_KEYCHAIN_SERVICE:-roby-pbs}"
SECRET_WRAPPER="${ROBY_SECRET_WRAPPER:-$ROOT_DIR/scripts/roby-keychain-run.sh}"
STATE_DIR="${HOME}/.openclaw/roby"
CRON_SECRET_BEGIN="# ROBY_ORCH_CRON_SECRET_ENV_BEGIN"
CRON_SECRET_END="# ROBY_ORCH_CRON_SECRET_ENV_END"

DEEP=0
PROBE_SLACK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deep|--probe)
      DEEP=1
      shift
      ;;
    --probe-slack)
      PROBE_SLACK=1
      shift
      ;;
    -h|--help)
      cat <<'USAGE'
Usage:
  scripts/roby-cron-doctor.sh [--deep] [--probe-slack]

Checks PBS cron wiring, required secrets, gog auth, Neuronic health, and recent
cron logs. --deep adds read-only Gmail/Drive/Neuronic API probes.
--probe-slack sends a small Slack webhook test message.
USAGE
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

SECRET_KEYS=(
  GOG_KEYRING_PASSWORD
  GEMINI_API_KEY
  OPENAI_API_KEY
  NOTION_TOKEN
  NOTION_API_KEY
  SLACK_WEBHOOK_URL
  SLACK_SIGNING_SECRET
  SLACK_BOT_TOKEN
  NEURONIC_TOKEN
  OLLAMA_API_KEY
)

REQUIRED_CRON_TAGS=(
  ROBY_ORCH_CRON_SELF_GROWTH
  ROBY_ORCH_CRON_MINUTES_SYNC
  ROBY_ORCH_CRON_GMAIL_TRIAGE
  ROBY_ORCH_CRON_MEMORY_SYNC
)

FAILS=0
WARNS=0

ok() {
  printf 'OK   %s\n' "$1"
}

warn() {
  WARNS=$((WARNS + 1))
  printf 'WARN %s\n' "$1"
}

fail() {
  FAILS=$((FAILS + 1))
  printf 'FAIL %s\n' "$1"
}

has_command() {
  command -v "$1" >/dev/null 2>&1
}

run_with_secrets() {
  "$SECRET_WRAPPER" "$@"
}

echo "Roby cron doctor"
echo "repo=${ROOT_DIR}"
echo "keychain_service=${KEYCHAIN_SERVICE}"
echo

for cmd in bash python3 crontab security; do
  if has_command "$cmd"; then
    ok "command ${cmd}"
  else
    fail "command ${cmd} not found"
  fi
done
if has_command gog; then
  ok "command gog"
else
  fail "command gog not found"
fi
if [[ -x "$SECRET_WRAPPER" ]]; then
  ok "secret wrapper executable: ${SECRET_WRAPPER}"
else
  fail "secret wrapper not executable: ${SECRET_WRAPPER}"
fi

echo
echo "Secrets"
for key in "${SECRET_KEYS[@]}"; do
  if security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$key" -w >/dev/null 2>&1; then
    ok "${key}: keychain_present"
  else
    warn "${key}: keychain_missing"
  fi
done

echo
echo "Crontab"
CRONTAB_TEXT="$(crontab -l 2>/dev/null || true)"
if [[ -n "$CRONTAB_TEXT" ]]; then
  ok "crontab readable"
else
  warn "crontab is empty or unreadable"
fi
if grep -Fqx "$CRON_SECRET_BEGIN" <<<"$CRONTAB_TEXT" && grep -Fqx "$CRON_SECRET_END" <<<"$CRONTAB_TEXT"; then
  ok "cron secret env block present"
else
  warn "cron secret env block missing; run scripts/install_roby_orchestrator_cron.sh"
fi
for key in "${SECRET_KEYS[@]}"; do
  if grep -Eq "^${key}=" <<<"$CRONTAB_TEXT"; then
    ok "${key}: cron_env_present"
  else
    warn "${key}: cron_env_missing"
  fi
done
for tag in "${REQUIRED_CRON_TAGS[@]}"; do
  if grep -Fq "$tag" <<<"$CRONTAB_TEXT"; then
    ok "${tag}: installed"
  else
    fail "${tag}: missing"
  fi
done

echo
echo "gog auth"
GOG_AUTH_OUT=""
if GOG_AUTH_OUT="$(run_with_secrets gog auth list 2>&1)"; then
  ok "gog auth list"
else
  fail "gog auth list failed: ${GOG_AUTH_OUT//$'\n'/ }"
fi
GOG_ACCOUNT="$(awk 'NF && $1 ~ /@/ { print $1; exit }' <<<"$GOG_AUTH_OUT")"
if [[ -n "$GOG_ACCOUNT" ]]; then
  ok "gog account detected: ${GOG_ACCOUNT}"
  for svc in gmail drive docs; do
    if awk -v account="$GOG_ACCOUNT" -v svc="$svc" '$1 == account && $0 ~ svc { found = 1 } END { exit found ? 0 : 1 }' <<<"$GOG_AUTH_OUT"; then
      ok "gog service ${svc}"
    else
      fail "gog service ${svc} missing for ${GOG_ACCOUNT}"
    fi
  done
else
  fail "gog account not detected"
fi

echo
echo "Neuronic"
if run_with_secrets bash -lc 'curl -fsS --max-time 5 "${NEURONIC_HEALTH_URL:-http://127.0.0.1:5174/health}" >/dev/null'; then
  ok "Neuronic health"
else
  fail "Neuronic health failed"
fi

if [[ "$DEEP" == "1" ]]; then
  echo
  echo "Deep probes"
  if [[ -n "$GOG_ACCOUNT" ]] && run_with_secrets gog gmail messages search "newer_than:1d in:inbox" --max 1 --json --results-only --no-input --account "$GOG_ACCOUNT" >/dev/null 2>&1; then
    ok "Gmail read probe"
  else
    fail "Gmail read probe failed"
  fi
  if [[ -n "$GOG_ACCOUNT" ]] && run_with_secrets gog drive search "mimeType='application/vnd.google-apps.document' and trashed=false" --raw-query --json --results-only --max 1 --no-input --account "$GOG_ACCOUNT" >/dev/null 2>&1; then
    ok "Drive read probe"
  else
    fail "Drive read probe failed"
  fi
  if run_with_secrets bash -lc 'base="${NEURONIC_API_BASE_URL:-http://127.0.0.1:5174/api/v1}"; token="${NEURONIC_TOKEN:-${TASKD_AUTH_TOKEN:-}}"; curl -fsS --max-time 5 -H "Authorization: Bearer ${token}" "${base%/}/tasks?limit=1" >/dev/null'; then
    ok "Neuronic authenticated tasks probe"
  else
    fail "Neuronic authenticated tasks probe failed"
  fi
fi

echo
echo "Recent cron logs"
for task in gmail_triage minutes_sync self_growth memory_sync; do
  log="${STATE_DIR}/cron_${task}.log"
  if [[ ! -f "$log" ]]; then
    warn "${task}: log missing (${log})"
    continue
  fi
  last_status="$(grep -E "\] (DONE|FAIL|TIMEOUT|SKIP): task=${task}" "$log" | tail -n 1 || true)"
  if [[ "$last_status" == *"] DONE: task=${task}"* ]]; then
    ok "${task}: last status DONE"
  elif [[ "$last_status" == *"] SKIP: task=${task}"* ]]; then
    warn "${task}: last status SKIP"
  elif [[ -n "$last_status" ]]; then
    fail "${task}: last status ${last_status}"
  else
    warn "${task}: no status line found"
  fi
done

echo
echo "Slack"
if run_with_secrets bash -lc '[[ -n "${SLACK_WEBHOOK_URL:-}" ]]'; then
  ok "SLACK_WEBHOOK_URL present"
else
  warn "SLACK_WEBHOOK_URL missing"
fi
if [[ "$PROBE_SLACK" == "1" ]]; then
  if run_with_secrets python3 - <<'PY'
import json
import os
import socket
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

url = os.environ.get("SLACK_WEBHOOK_URL", "")
if not url:
    raise SystemExit(2)
jst = timezone(timedelta(hours=9))
text = f"Roby cron doctor probe OK\nhost={socket.gethostname()}\ntime={datetime.now(jst).isoformat()}"
req = urllib.request.Request(
    url,
    data=json.dumps({"text": text}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=8):
    pass
PY
  then
    ok "Slack webhook probe sent"
  else
    fail "Slack webhook probe failed"
  fi
fi

echo
echo "Summary: fails=${FAILS} warnings=${WARNS}"
if (( FAILS > 0 )); then
  exit 1
fi
exit 0

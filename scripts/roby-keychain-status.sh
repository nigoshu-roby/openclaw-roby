#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ROBY_ENV_FILE:-$HOME/.openclaw/.env}"
KEYCHAIN_SERVICE="${ROBY_KEYCHAIN_SERVICE:-roby-pbs}"

SECRET_KEYS=(
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

echo "keychain_service=${KEYCHAIN_SERVICE}"
echo ".env=$([[ -f "$ENV_FILE" ]] && echo present || echo missing)"

plain_present=0
keychain_present=0

for key in "${SECRET_KEYS[@]}"; do
  raw_line="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 || true)"
  if [[ -n "$raw_line" ]]; then
    echo "${key}: plain_env_present"
    plain_present=$((plain_present + 1))
  fi
  if security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$key" -w >/dev/null 2>&1; then
    echo "${key}: keychain_present"
    keychain_present=$((keychain_present + 1))
  fi
done

echo "summary_plain_env_present=${plain_present}"
echo "summary_keychain_present=${keychain_present}"

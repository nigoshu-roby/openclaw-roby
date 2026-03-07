#!/usr/bin/env bash
set -euo pipefail

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

for key in "${SECRET_KEYS[@]}"; do
  if [[ -n "${!key:-}" ]]; then
    continue
  fi
  value="$(security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$key" -w 2>/dev/null || true)"
  if [[ -n "$value" ]]; then
    export "$key=$value"
  fi
done

exec "$@"

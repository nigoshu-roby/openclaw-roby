#!/bin/zsh
set -euo pipefail

ENV_FILE="${HOME}/.openclaw/.env"
ENV_1P_FILE="${HOME}/.openclaw/.env.1p"

secret_keys=(
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

echo "op_cli: $(command -v op >/dev/null 2>&1 && echo installed || echo missing)"

if command -v op >/dev/null 2>&1; then
  if op account list --format json >/dev/null 2>&1; then
    account_count="$(op account list --format json | jq 'length')"
    if [[ "${account_count}" -gt 0 ]]; then
      echo "op_account: configured"
    else
      echo "op_account: not_configured"
    fi
  else
    echo "op_account: unavailable"
  fi
fi

echo ".env: $([[ -f "${ENV_FILE}" ]] && echo present || echo missing)"
echo ".env.1p: $([[ -f "${ENV_1P_FILE}" ]] && echo present || echo missing)"

if [[ ! -f "${ENV_FILE}" ]]; then
  exit 0
fi

plain_present=0
ref_present=0

for key in "${secret_keys[@]}"; do
  raw_line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
  ref_line=""
  if [[ -f "${ENV_1P_FILE}" ]]; then
    ref_line="$(grep -E "^${key}=" "${ENV_1P_FILE}" | tail -n 1 || true)"
  fi

  if [[ -n "${raw_line}" ]]; then
    value="${raw_line#*=}"
    if [[ -n "${value}" ]]; then
      echo "${key}: plain_env_present"
      plain_present=$((plain_present + 1))
    else
      echo "${key}: plain_env_empty"
    fi
  fi

  if [[ -n "${ref_line}" ]]; then
    ref_value="${ref_line#*=}"
    if [[ "${ref_value}" == op://* ]]; then
      echo "${key}: onepassword_ref_present"
      ref_present=$((ref_present + 1))
    else
      echo "${key}: onepassword_ref_invalid"
    fi
  fi
done

echo "summary_plain_env_present=${plain_present}"
echo "summary_onepassword_ref_present=${ref_present}"

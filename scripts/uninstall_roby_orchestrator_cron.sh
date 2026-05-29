#!/usr/bin/env bash
set -euo pipefail

TAG_SELF="ROBY_ORCH_CRON_SELF_GROWTH"
TAG_MINUTES="ROBY_ORCH_CRON_MINUTES_SYNC"
TAG_GMAIL="ROBY_ORCH_CRON_GMAIL_TRIAGE"
TAG_EVAL="ROBY_ORCH_CRON_EVAL_HARNESS"
TAG_DRILL="ROBY_ORCH_CRON_RUNBOOK_DRILL"
TAG_NOTION="ROBY_ORCH_CRON_NOTION_SYNC"
TAG_FEEDBACK="ROBY_ORCH_CRON_FEEDBACK_SYNC"
TAG_MEMORY="ROBY_ORCH_CRON_MEMORY_SYNC"
TAG_WEEKLY="ROBY_ORCH_CRON_WEEKLY_REPORT"
CRON_SECRET_BEGIN="# ROBY_ORCH_CRON_SECRET_ENV_BEGIN"
CRON_SECRET_END="# ROBY_ORCH_CRON_SECRET_ENV_END"
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

print_crontab_redacted() {
  crontab -l | awk -v keys="${SECRET_KEYS[*]}" '
    BEGIN {
      n = split(keys, arr, " ")
      for (i = 1; i <= n; i++) {
        secret[arr[i]] = 1
      }
    }
    /^[A-Za-z_][A-Za-z0-9_]*=/ {
      key = $0
      sub(/=.*/, "", key)
      if (key in secret) {
        print key "=<redacted>"
        next
      }
    }
    { print }
  '
}

current="$(crontab -l 2>/dev/null || true)"
filtered="$(
  printf "%s\n" "$current" \
    | awk -v begin="$CRON_SECRET_BEGIN" -v end="$CRON_SECRET_END" '
        $0 == begin { skip = 1; next }
        $0 == end { skip = 0; next }
        !skip { print }
      ' \
    | sed "/${TAG_SELF}/d;/${TAG_MINUTES}/d;/${TAG_GMAIL}/d;/${TAG_EVAL}/d;/${TAG_DRILL}/d;/${TAG_NOTION}/d;/${TAG_FEEDBACK}/d;/${TAG_MEMORY}/d;/${TAG_WEEKLY}/d"
)"
printf "%s\n" "$filtered" | awk 'NF' | crontab -

echo "Removed orchestrator cron jobs (${TAG_SELF}, ${TAG_MINUTES}, ${TAG_GMAIL}, ${TAG_EVAL}, ${TAG_DRILL}, ${TAG_NOTION}, ${TAG_FEEDBACK}, ${TAG_MEMORY}, ${TAG_WEEKLY})."
echo
echo "Current crontab (secret values redacted):"
print_crontab_redacted

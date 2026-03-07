#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ROBY_ENV_FILE:-$HOME/.openclaw/.env}"
KEYCHAIN_SERVICE="${ROBY_KEYCHAIN_SERVICE:-roby-pbs}"
PRUNE_ENV="${PRUNE_ENV:-0}"

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

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env_file_missing"
  exit 1
fi

extract_value() {
  local key="$1"
  python3 - "$ENV_FILE" "$key" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    if k.strip() != key:
        continue
    value = v.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    print(value)
    break
PY
}

imported=0
skipped=0

for key in "${SECRET_KEYS[@]}"; do
  value="$(extract_value "$key")"
  if [[ -z "$value" ]]; then
    skipped=$((skipped + 1))
    continue
  fi
  security add-generic-password -U -s "$KEYCHAIN_SERVICE" -a "$key" -w "$value" >/dev/null
  imported=$((imported + 1))
done

if [[ "$PRUNE_ENV" == "1" ]]; then
  python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
secret_keys = {
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "NOTION_TOKEN",
    "NOTION_API_KEY",
    "SLACK_WEBHOOK_URL",
    "SLACK_SIGNING_SECRET",
    "SLACK_BOT_TOKEN",
    "NEURONIC_TOKEN",
    "OLLAMA_API_KEY",
}
out = []
for raw in path.read_text(encoding="utf-8").splitlines():
    stripped = raw.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        out.append(raw)
        continue
    key = stripped.split("=", 1)[0].strip()
    if key in secret_keys:
        continue
    out.append(raw)
path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY
fi

echo "imported=${imported}"
echo "skipped=${skipped}"
echo "keychain_service=${KEYCHAIN_SERVICE}"
echo "env_pruned=${PRUNE_ENV}"

#!/usr/bin/env bash
set -euo pipefail

# Install temporary 3-hour cron cycle for Roby.
# - Runs every 30 minutes
# - Auto-removes itself after 3 hours
#
# Usage:
#   scripts/install_roby_3h_cycle_cron.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
JOB_TAG="PBS_3H_SPRINT"
END_EPOCH="$(($(date +%s) + 3 * 60 * 60))"

CMD="cd \"$ROOT_DIR\" && PBS_3H_END_EPOCH=${END_EPOCH} /bin/bash \"$ROOT_DIR/scripts/roby-ops-cycle.sh\""
CRON_LINE="*/30 * * * * ${CMD} # ${JOB_TAG}"

current="$(crontab -l 2>/dev/null || true)"
filtered="$(printf "%s\n" "$current" | sed "/${JOB_TAG}/d")"
{
  printf "%s\n" "$filtered"
  printf "%s\n" "$CRON_LINE"
} | awk 'NF' | crontab -

echo "Installed temporary 3-hour cron cycle."
echo "End epoch: ${END_EPOCH}"
echo "Cron line : ${CRON_LINE}"
echo
echo "Current crontab:"
crontab -l


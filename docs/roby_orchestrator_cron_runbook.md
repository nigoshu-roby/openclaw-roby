# Roby Orchestrator Cron Runbook

## Purpose

Unify scheduled jobs into a single entrypoint:

- `self_growth`
- `minutes_sync`
- `gmail_triage`

All jobs run via:
`scripts/roby-cron-dispatch.sh -> scripts/roby-orchestrator.py --cron-task ...`

## Install

```bash
cd /Users/<user>/OpenClaw
chmod +x scripts/roby-cron-dispatch.sh scripts/install_roby_orchestrator_cron.sh scripts/uninstall_roby_orchestrator_cron.sh
./scripts/install_roby_orchestrator_cron.sh
```

## Default schedule

- self_growth: `5 * * * *`
- minutes_sync: `15 */2 * * *`
- gmail_triage: `*/30 * * * *`

## Custom schedule

```bash
cd /Users/<user>/OpenClaw
SELF_GROWTH_CRON="5 * * * *" \
MINUTES_SYNC_CRON="45 */1 * * *" \
GMAIL_TRIAGE_CRON="*/20 * * * *" \
./scripts/install_roby_orchestrator_cron.sh
```

## Logs

- `~/.openclaw/roby/cron_self_growth.log`
- `~/.openclaw/roby/cron_minutes_sync.log`
- `~/.openclaw/roby/cron_gmail_triage.log`

## Safety controls

- Per-task lock (`/tmp/roby-cron-<task>.lock`) to avoid overlap.
- Timeout kill for each task (default 900/1800/900 sec).
- Structured JSON output from orchestrator is preserved in logs.

## Manual run

```bash
cd /Users/<user>/OpenClaw
./scripts/roby-cron-dispatch.sh self_growth 900
./scripts/roby-cron-dispatch.sh minutes_sync 1800
./scripts/roby-cron-dispatch.sh gmail_triage 900
```

## Uninstall

```bash
cd /Users/<user>/OpenClaw
./scripts/uninstall_roby_orchestrator_cron.sh
```

## Rollback

1. Remove new orchestrator cron entries:
   ```bash
   ./scripts/uninstall_roby_orchestrator_cron.sh
   ```
2. Restore previous cron entries from backup/history (if maintained).
3. Verify with:
   ```bash
   crontab -l
   ```

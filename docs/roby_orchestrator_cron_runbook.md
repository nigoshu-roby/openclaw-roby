# Roby Orchestrator Cron Runbook

## Purpose

Unify scheduled jobs into a single entrypoint:

- `self_growth`
- `minutes_sync`
- `gmail_triage`
- `notion_sync` (optional)

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
- notion_sync: disabled (enable via env)

## Enable Notion sync job

Set these in `~/.openclaw/.env`:

```bash
ROBY_ORCH_ENABLE_NOTION_SYNC=1
ROBY_NOTION_SYNC_PAGE_ID=<your notion page id>
ROBY_GH_OWNER=nigoshu-roby
ROBY_GH_PROJECT_NUMBER=1
```

Reinstall cron:

```bash
cd /Users/<user>/OpenClaw
./scripts/install_roby_orchestrator_cron.sh
```

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
- `~/.openclaw/roby/cron_notion_sync.log` (if enabled)

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
./scripts/roby-cron-dispatch.sh notion_sync 600
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

# Roby Orchestrator Cron Runbook

## Purpose

Unify scheduled jobs into a single entrypoint:

- `self_growth`
- `minutes_sync`
- `gmail_triage`
- `eval_harness` (optional)
- `runbook_drill` (optional)
- `notion_sync` (optional)
- `weekly_report` (optional)

All jobs run via:
`scripts/roby-cron-dispatch.sh -> scripts/roby-orchestrator.py --cron-task ...`

## Install

```bash
cd <OPENCLAW_REPO>
chmod +x scripts/roby-cron-dispatch.sh scripts/install_roby_orchestrator_cron.sh scripts/uninstall_roby_orchestrator_cron.sh
./scripts/install_roby_orchestrator_cron.sh
```

## Secrets policy

cron jobs are installed in Keychain-first mode by default.

- secrets source: macOS Keychain (`service=roby-pbs`)
- helper wrapper: `scripts/roby-keychain-run.sh`
- fallback config: `~/.openclaw/.env` (low-risk settings only)

Check current status:

```bash
./scripts/roby-keychain-status.sh
```

## Default schedule

- self_growth: `5 * * * *`
- minutes_sync: `15 */2 * * *`
- gmail_triage: `*/30 * * * *`
- eval_harness: disabled (enable via env)
- runbook_drill: disabled (enable via env)
- notion_sync: disabled (enable via env)
- weekly_report: disabled (enable via env)

## Enable Evaluation Harness job

Set this in `~/.openclaw/.env`:

```bash
ROBY_ORCH_ENABLE_EVAL=1
```

Optional schedule/timeout overrides:

```bash
EVAL_HARNESS_CRON="35 */6 * * *"
EVAL_HARNESS_TIMEOUT=900
```

## Enable Runbook Drill job

Set this in `~/.openclaw/.env`:

```bash
ROBY_ORCH_ENABLE_DRILL=1
```

Optional schedule/timeout overrides:

```bash
RUNBOOK_DRILL_CRON="20 8 * * 1"
RUNBOOK_DRILL_TIMEOUT=1200
```

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
cd <OPENCLAW_REPO>
./scripts/install_roby_orchestrator_cron.sh
```

## Enable Weekly report job

Set this in `~/.openclaw/.env`:

```bash
ROBY_ORCH_ENABLE_WEEKLY_REPORT=1
```

Optional schedule/timeout overrides:

```bash
WEEKLY_REPORT_CRON="30 9 * * 1"
WEEKLY_REPORT_TIMEOUT=900
ROBY_WEEKLY_REPORT_NOTIFY=1
```

Artifacts:

- `~/.openclaw/roby/reports/weekly_latest.json`
- `~/.openclaw/roby/reports/weekly_latest.md`
- `~/.openclaw/roby/reports/weekly_history.jsonl`

## Custom schedule

```bash
cd <OPENCLAW_REPO>
SELF_GROWTH_CRON="5 * * * *" \
MINUTES_SYNC_CRON="45 */1 * * *" \
GMAIL_TRIAGE_CRON="*/20 * * * *" \
EVAL_HARNESS_CRON="35 */6 * * *" \
RUNBOOK_DRILL_CRON="20 8 * * 1" \
WEEKLY_REPORT_CRON="30 9 * * 1" \
./scripts/install_roby_orchestrator_cron.sh
```

## Logs

- `~/.openclaw/roby/cron_self_growth.log`
- `~/.openclaw/roby/cron_minutes_sync.log`
- `~/.openclaw/roby/cron_gmail_triage.log`
- `~/.openclaw/roby/cron_eval_harness.log` (if enabled)
- `~/.openclaw/roby/cron_runbook_drill.log` (if enabled)
- `~/.openclaw/roby/cron_notion_sync.log` (if enabled)
- `~/.openclaw/roby/cron_weekly_report.log` (if enabled)

## Safety controls

- Per-task lock (`/tmp/roby-cron-<task>.lock`) to avoid overlap.
- Timeout kill for each task (default 900/1800/900 sec).
- Structured JSON output from orchestrator is preserved in logs.
- `scripts/roby-cron-dispatch.sh` は失敗時（timeout / non-zero exit）に Slack 通知します。
  - 通知先: `SLACK_WEBHOOK_URL`（環境変数 / `.env` / Keychain）
  - 通知内容: `task`, `reason`, `time`, `host`, `log path`

## QA AB Router (optional)

`qa_gemini` の実行時に A/B を使いたい場合は `~/.openclaw/.env` に設定:

```bash
ROBY_ORCH_AB_ROUTER=1
```

設定ファイルは `config/pbs/ab_router.json`。  
実行ログは `~/.openclaw/roby/ab_router_runs.jsonl` に保存されます。

## Immutable Audit

主要実行イベントは監査ログに append-only で保存されます。

- 監査ログ: `~/.openclaw/roby/audit/events.jsonl`
- 無効化（非推奨）: `ROBY_IMMUTABLE_AUDIT=0`

整合性チェック:

```bash
python3 ./scripts/roby_audit.py verify --json
```

## Runbook Drill

運用スモークチェック:

```bash
python3 ./scripts/roby-drill.py --json
```

通知仕様:

- 既定: fail時のみ Slack 通知
- 成功時も通知したい場合: `ROBY_DRILL_NOTIFY_ON_PASS=1`

詳細Runbook:

- `docs/roby_runbook_drill.md`

## Manual run

```bash
cd <OPENCLAW_REPO>
./scripts/roby-cron-dispatch.sh self_growth 900
./scripts/roby-cron-dispatch.sh minutes_sync 1800
./scripts/roby-cron-dispatch.sh gmail_triage 900
./scripts/roby-cron-dispatch.sh eval_harness 900
./scripts/roby-cron-dispatch.sh runbook_drill 1200
./scripts/roby-cron-dispatch.sh notion_sync 600
./scripts/roby-cron-dispatch.sh weekly_report 900
```

## Uninstall

```bash
cd <OPENCLAW_REPO>
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

## Launchd scope

Current PBS secret handling does not require changes to the OpenClaw gateway LaunchAgent or the UI LaunchAgent.

- `ai.openclaw.gateway`: unchanged
- `com.openclaw.ui3000`: unchanged

Reason:

- PBS secrets are consumed by Roby Python jobs and cron dispatch
- UI and gateway do not need direct secret injection for this path

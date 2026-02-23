---
name: roby-mail
description: Gmail triage via gog. Classify mail, archive ads, notify Slack, extract tasks, and send to Neuronic.
metadata:
  {
    "openclaw":
      { "emoji": "📬", "requires": { "bins": ["gog", "summarize"], "env": ["NEURONIC_TOKEN"] } },
  }
---

# roby-mail

Triage Gmail and turn important messages into tasks.

## What this skill does

- Searches Gmail via `gog` (messages search).
- Classifies mail into: `needs_reply`, `needs_review`, `later_check`, `archive`.
- Archives ads/low-value mail (removes `INBOX`).
- Sends Slack notifications (if `SLACK_WEBHOOK_URL` is set).
- Extracts tasks via `summarize` and posts to Neuronic (if configured).

## Configuration

Environment variables (recommended in `~/.openclaw/.env`):

- `SLACK_WEBHOOK_URL` (optional, enables Slack notifications)
- `NEURONIC_URL` (optional, default `http://127.0.0.1:5174/api/v1/tasks/import`)
- `NEURONIC_TOKEN` (optional, Bearer token)

## Run

```bash
python3 /Users/<user>/OpenClaw/skills/roby-mail/scripts/gmail_triage.py   --account <your-work-email>   --query "newer_than:2d in:inbox"   --max 50
```

Options:

- `--dry-run` (no archive, no Slack, no Neuronic)
- `--archive-ads/--no-archive-ads` (default: on)

## Output

- Prints a concise summary to stdout.
- Writes run logs to `~/.openclaw/roby/gmail_triage_runs.jsonl`.
- Tracks processed message IDs in `~/.openclaw/roby/gmail_triage_state.json`.

## Classification rules (current)

- **needs_reply / needs_review**: deadlines, requests, consultation, approvals, client questions.
- **later_check**: service/tool notifications that are relevant to work tools.
- **archive**: low‑value service notifications unrelated to work.

Adjust keywords in the script if needed.

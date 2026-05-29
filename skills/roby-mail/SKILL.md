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
- Applies Gmail labels for operational buckets:
  - `一括保管`: clear low-value mail, archived.
  - `後で読む`: low-priority digest/read-later mail, archived by default.
  - `要確認`: review/actionable mail, kept in the inbox.
- Archives ads/low-value mail (removes `INBOX`).
- Sends Slack notifications (if `SLACK_WEBHOOK_URL` is set).
- For high-confidence human reply/action mail, can post two draft replies to Slack for approval.
- Extracts tasks via `summarize` and posts to Neuronic (if configured).

## Configuration

Environment variables (recommended in `~/.openclaw/.env`):

- `SLACK_WEBHOOK_URL` (optional, enables Slack notifications)
- `NEURONIC_URL` (optional, default `http://127.0.0.1:5174/api/v1/tasks/import`)
- `NEURONIC_TOKEN` (optional, Bearer token)
- `GMAIL_TRIAGE_APPLY_LABELS` (optional, default `1`; set `0` to avoid Gmail label changes)
- `GMAIL_TRIAGE_ARCHIVE_DIGEST` (optional, default `1`; set `0` to keep `後で読む` in the inbox)
- `GMAIL_REPLY_REVIEW_ENABLE` (optional, default `1`; set `0` to disable Slack reply-review proposals)
- `GMAIL_REPLY_REVIEW_CHANNEL` (optional; Slack channel ID for reply-review proposals)

## Run

```bash
python3 ./skills/roby-mail/scripts/gmail_triage.py --account <your-work-email> --query "newer_than:2d in:inbox" --max 50
```

Options:

- `--dry-run` (no archive, no Slack, no Neuronic)
- `--archive-ads/--no-archive-ads` (default: on)
- `--apply-labels/--no-apply-labels` (default controlled by `GMAIL_TRIAGE_APPLY_LABELS`)
- `--reply-review/--no-reply-review` (default controlled by `GMAIL_REPLY_REVIEW_ENABLE`)

## Output

- Prints a concise summary to stdout.
- Writes run logs to `~/.openclaw/roby/gmail_triage_runs.jsonl`.
- Tracks processed message IDs in `~/.openclaw/roby/gmail_triage_state.json`.

## Classification rules (current)

- **needs_reply / needs_review**: deadlines, requests, consultation, approvals, client questions.
- **later_check**: service/tool notifications that are relevant to work tools.
- **archive**: low‑value service notifications unrelated to work.

Adjust keywords in the script if needed.

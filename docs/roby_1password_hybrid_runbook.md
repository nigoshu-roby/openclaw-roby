# Roby 1Password Hybrid Runbook

Deprecated:

- This runbook is no longer the recommended PBS path.
- Current recommendation is macOS Keychain:
  - `docs/roby_keychain_hybrid_runbook.md`

## Purpose

This runbook defines a hybrid secrets strategy for Roby/PBS:

- developer workflows stay fast
- scheduled automation keeps running without Touch ID prompts
- plaintext `.env` no longer needs to hold high-risk secrets

## Policy

Use two lanes:

1. Development lane

- keep low-risk settings in a local env file
- inject high-risk secrets with `op run`
- allow manual Touch ID when needed

2. Automation lane

- use 1Password non-interactive auth for cron/daemon
- inject secrets before launching Roby jobs
- do not depend on Touch ID

## Supported behavior in Roby scripts

Roby scripts now follow this precedence:

1. exported environment variables
2. `ROBY_ENV_FILE` if set
3. default `~/.openclaw/.env`

This means `op run ... command` can safely override any fallback values from `.env`.

## Recommended file split

### `~/.openclaw/.env`

Keep only non-secret or low-risk config here:

```dotenv
ROBY_GMAIL_ACCOUNT=<your-work-email>
ROBY_GH_OWNER=nigoshu-roby
ROBY_GH_PROJECT_NUMBER=1
ROBY_ORCH_GEMINI_QA_NATIVE=1
ROBY_ORCH_OLLAMA_MODEL=qwen2.5:7b
ROBY_ENV_FILE=$HOME/.openclaw/.env
```

### `~/.openclaw/.env.1p`

Put 1Password references here:

```dotenv
OPENAI_API_KEY=op://<vault>/<item>/api_key
GEMINI_API_KEY=op://<vault>/<item>/api_key
NOTION_TOKEN=op://<vault>/<item>/token
SLACK_WEBHOOK_URL=op://<vault>/<item>/webhook_url
SLACK_BOT_TOKEN=op://<vault>/<item>/bot_token
SLACK_SIGNING_SECRET=op://<vault>/<item>/signing_secret
NEURONIC_TOKEN=op://<vault>/<item>/token
```

## Manual development usage

Example:

```bash
op run --env-file="$HOME/.openclaw/.env.1p" -- \
  python3 ./scripts/roby-orchestrator.py \
  --message "現在の機能をリスト化してください" --execute --json
```

If you want shorthand locally, `opx` is acceptable for manual runs only.

## Cron / daemon usage

`./scripts/roby-cron-dispatch.sh` supports a wrapper:

```bash
export ROBY_SECRET_WRAPPER='op run --env-file="$HOME/.openclaw/.env.1p" --'
```

Then the cron dispatcher will execute orchestrator jobs through 1Password injection.

## Recommended automation auth model

For unattended PBS jobs, use a non-interactive 1Password auth path such as a service account token.

Do not rely on Touch ID for:

- self-growth
- minutes sync
- gmail triage
- notion sync
- weekly report
- runbook drill

Touch ID is acceptable for:

- one-off local experiments
- manual debugging
- temporary secret inspection

## Migration order

1. Move high-risk secrets from `~/.openclaw/.env` to `~/.openclaw/.env.1p`
2. Keep non-secret config in `~/.openclaw/.env`
3. Run manual commands with `op run`
4. Enable `ROBY_SECRET_WRAPPER` for cron
5. After validation, remove raw secrets from `~/.openclaw/.env`

## Validation checklist

1. Manual run succeeds with `op run`
2. `python3 ./scripts/roby-orchestrator.py --message "ollama導入できましたか？" --json`
   returns expected output under injected env
3. `roby-cron-dispatch.sh gmail_triage` works with wrapper enabled
4. Slack notification still sends
5. Notion sync still writes
6. Neuronic import still authenticates

## Risks

- If cron uses Touch ID-based auth, jobs will hang or fail silently
- If raw `.env` values remain, agents can still read live secrets from disk
- If `ROBY_SECRET_WRAPPER` is misquoted, cron jobs may fail to start

## Decision

PBS should adopt hybrid mode:

- manual development can stay fast
- automation remains unattended
- high-risk secrets move out of plaintext `.env`

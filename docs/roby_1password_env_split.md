# Roby 1Password Env Split

目的:
`~/.openclaw/.env` に混在している設定を、`平文で残す設定` と `1Password参照へ移す設定` に分離する。

運用方針:

- `~/.openclaw/.env`
  - 秘密性が低い設定
  - 実行ポリシーやモデル選択、各種閾値、コマンド文字列
- `~/.openclaw/.env.1p`
  - APIキー、トークン、Webhook URL、認証情報
  - 値は `op://...` のみを置く

## 1Password 側へ移すキー

- `GEMINI_API_KEY`
- `OPENAI_API_KEY`
- `NOTION_TOKEN`
- `NOTION_API_KEY`
- `SLACK_WEBHOOK_URL`
- `SLACK_SIGNING_SECRET`
- `SLACK_BOT_TOKEN`
- `NEURONIC_TOKEN`
- `OLLAMA_API_KEY`

理由:

- 外部サービスへの認証や送信権限を持つ
- 漏洩時の被害が直接大きい

## 平文 `.env` に残してよいキー

- `NOTION_DATABASE_ID`
- `ROBY_GMAIL_ACCOUNT`
- `SELF_GROWTH_TEST_CMD`
- `SELF_GROWTH_TEST_TIMEOUT`
- `SELF_GROWTH_RESTART_CMD`
- `TOKIWAGI_ROOT_ID`
- `GDRIVE_MINUTES_FOLDER_ID`
- `GOG_ACCOUNT`
- `ROBY_ORCH_GEMINI_QA_NATIVE`
- `ROBY_ORCH_GEMINI_QA_PROMPT`
- `ROBY_ORCH_QA_MAX_TOKENS`
- `ROBY_ORCH_CODEX_CMD`
- `ROBY_MENTION_FORWARD_CMD`
- `ROBY_NOTION_SYNC_PAGE_ID`
- `ROBY_ORCH_ENABLE_NOTION_SYNC`
- `ROBY_GH_OWNER`
- `ROBY_GH_PROJECT_NUMBER`
- `MINUTES_REVIEW_MODELS`
- `MINUTES_TASKS_MODELS`
- `MINUTES_SUMMARY_MODELS`
- `MINUTES_MAX_TASKS_PER_DOC`
- `MINUTES_MAX_SUBTASKS_PER_PARENT`
- `MINUTES_HEURISTIC_MAX_PROJECTS`
- `MINUTES_HEURISTIC_MAX_ITEMS_PER_PROJECT`
- `MINUTES_REVIEW_RETRY_MAX_TOKENS`
- `MINUTES_TASKS_RETRY_MAX_TOKENS`
- `MINUTES_SUMMARIZE_RETRY_MAX_TOKENS`
- `ROBY_ORCH_ENABLE_DRILL`
- `ROBY_ORCH_ENABLE_EVAL`
- `ROBY_ORCH_ENABLE_WEEKLY_REPORT`
- `ROBY_WEEKLY_REPORT_NOTIFY`
- `ROBY_ORCH_OLLAMA_MODEL`
- `ROBY_ORCH_OLLAMA_TIMEOUT_SEC`
- `ROBY_ORCH_OLLAMA_FALLBACK_QA`
- `ROBY_ORCH_OLLAMA_BASE_URL`
- `ROBY_ORCH_OLLAMA_TEMPERATURE`
- `ROBY_ORCH_OLLAMA_TOP_P`
- `ROBY_ORCH_OLLAMA_REPEAT_PENALTY`
- `ROBY_ORCH_OLLAMA_NUM_PREDICT`
- `ROBY_ORCH_OLLAMA_MIN_OUTPUT_CHARS`
- `ROBY_ORCH_MINUTES_LLM_PROFILE`
- `ROBY_ORCH_MINUTES_LOCAL_FAST_MODEL`
- `ROBY_ORCH_MINUTES_LOCAL_QUALITY_MODEL`
- `ROBY_ORCH_MINUTES_CLOUD_MODEL`
- `ROBY_ORCH_GMAIL_PROFILE`
- `ROBY_ORCH_GMAIL_LLM_FAST_MODEL`
- `ROBY_ORCH_GMAIL_LLM_QUALITY_MODEL`
- `ROBY_ORCH_GMAIL_LLM_MAX_REVIEWS_HYBRID`
- `ROBY_ORCH_GMAIL_LLM_MAX_REVIEWS_QUALITY`

理由:

- モデル選択、閾値、ルーティング、ID、コマンド設定が中心
- 単独漏洩で直ちに外部権限奪取に繋がりにくい

## 境界項目の扱い

以下は厳密には秘密ではないが、内部運用情報として扱う:

- `NOTION_DATABASE_ID`
- `TOKIWAGI_ROOT_ID`
- `GDRIVE_MINUTES_FOLDER_ID`
- `ROBY_NOTION_SYNC_PAGE_ID`

推奨:

- 当面は `.env` に残してよい
- 将来的に「構成情報も秘匿したい」方針へ寄せるなら `.env.1p` 側へ移してもよい

## 推奨ファイル構成

`~/.openclaw/.env`

```dotenv
NOTION_DATABASE_ID=...
ROBY_GMAIL_ACCOUNT=<your-work-email>
SELF_GROWTH_TEST_CMD=...
ROBY_ORCH_GEMINI_QA_PROMPT=...
ROBY_ORCH_OLLAMA_MODEL=qwen2.5:14b
ROBY_ORCH_OLLAMA_BASE_URL=http://127.0.0.1:11434
```

`~/.openclaw/.env.1p`

```dotenv
OPENAI_API_KEY=op://<vault>/OpenAI/api_key
GEMINI_API_KEY=op://<vault>/Gemini/api_key
SLACK_BOT_TOKEN=op://<vault>/Slack/bot_token
```

## 実行方法

手動実行:

```bash
op run --env-file="$HOME/.openclaw/.env.1p" -- \
  python3 /Users/<user>/OpenClaw/scripts/roby-orchestrator.py --message "現在の機能をリスト化してください" --json
```

cron / 常駐実行:

```bash
export ROBY_SECRET_WRAPPER='op run --env-file="$HOME/.openclaw/.env.1p" --'
```

既存スクリプトは、すでに `os.environ` を優先し、未設定のキーだけ `.env` から補完する。

## 移行順

1. 1Password に秘密値を登録
2. `~/.openclaw/.env.1p` に `op://...` を書く
3. `~/.openclaw/.env` から秘密値の生値を削除
4. 手動で `op run ...` テスト
5. cron / 常駐に `ROBY_SECRET_WRAPPER` を適用

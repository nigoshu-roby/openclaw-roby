# Roby Keychain Hybrid Runbook

目的:

- PBS の secrets を平文 `.env` から外す
- 開発速度を落としすぎない
- cron / 常駐運用を壊さない

方針:

- 低リスク設定は `~/.openclaw/.env`
- 高リスク secrets は macOS Keychain
- Roby スクリプトは `環境変数 -> .env -> Keychain` の順で読む

対象 secret:

- `GEMINI_API_KEY`
- `OPENAI_API_KEY`
- `NOTION_TOKEN`
- `NOTION_API_KEY`
- `SLACK_WEBHOOK_URL`
- `SLACK_SIGNING_SECRET`
- `SLACK_BOT_TOKEN`
- `NEURONIC_TOKEN`
- `OLLAMA_API_KEY`

## 追加スクリプト

- `scripts/roby-keychain-import.sh`
  - `.env` の対象 secret を Keychain に移す
  - `PRUNE_ENV=1` で `.env` から secret 行を削除
- `scripts/roby-keychain-run.sh`
  - 実行前に Keychain から secret を読み込んで環境変数へ注入
- `scripts/roby-keychain-status.sh`
  - 平文 `.env` 残存と Keychain 格納状況を確認

## 初回移行

```bash
chmod +x ./scripts/roby-keychain-*.sh
PRUNE_ENV=1 ./scripts/roby-keychain-import.sh
```

これで:

- Keychain service: `roby-pbs`
- account: 各環境変数名

として保存される。

## 状態確認

```bash
./scripts/roby-keychain-status.sh
```

このスクリプトは値を表示しない。

## 手動実行

```bash
./scripts/roby-keychain-run.sh \
  python3 ./scripts/roby-orchestrator.py \
  --message "現在の機能をリスト化してください" --json
```

主要 Roby スクリプトは Keychain fallback を直接持つため、通常は wrapper なしでも動く。

## cron / 常駐

必要なら:

```bash
export ROBY_SECRET_WRAPPER='./scripts/roby-keychain-run.sh'
```

`scripts/roby-cron-dispatch.sh` は Slack 異常通知の webhook も Keychain から参照できる。

## ログ安全性

- Keychain import/status スクリプトは secret 値を表示しない
- `security find-generic-password` の値は標準出力に出さず内部利用のみ
- 実行ログには件数と状態のみを残す

## ロールバック

もし何か問題があれば:

- `.env` に対象 secret を戻す
- Keychain fallback をそのまま残しても問題ない
- 二重定義時は `環境変数 -> .env -> Keychain` の優先順

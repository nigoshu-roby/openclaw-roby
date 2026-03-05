# PBS Runbook / Drill

最終更新: 2026-03-04

## 目的

運用中に「壊れてから調べる」のではなく、定期的に疎通・監査・品質ゲートを確認する。

## 実行コマンド

```bash
python3 /Users/<user>/OpenClaw/scripts/roby-drill.py --json
```

成功時も含めてSlack通知したい場合:

```bash
python3 /Users/<user>/OpenClaw/scripts/roby-drill.py --json --notify
```

出力:

- 最新JSON: `~/.openclaw/roby/drills/latest.json`
- 履歴: `~/.openclaw/roby/drills/history.jsonl`
- 可読レポート: `~/.openclaw/roby/drills/latest.md`

## 実行チェック項目（現行）

1. `gateway_status`（必須）
2. `ollama_health`（任意, `ROBY_DRILL_REQUIRE_OLLAMA=1` で必須化）
3. `orchestrator_qa_smoke`（必須）
4. `eval_harness_smoke`（必須）
5. `eval_self_awareness_cases`（必須）
6. `audit_verify`（必須）
7. `minutes_neuronic_regression`（必須）
8. `gmail_neuronic_regression`（必須）
9. `gmail_triage_dry_run`（任意, `GOG_ACCOUNT` 未設定ならSKIP）

## 部分実行

```bash
python3 /Users/<user>/OpenClaw/scripts/roby-drill.py --check gateway_status --check audit_verify --json
```

## 失敗時の一次対応

### #3 Evaluationケース（自己把握/プロンプト漏れ）の見方

対象ケース:

- `qa_local_status_ollama`
- `qa_local_status_neuronic`
- `qa_feature_list_quality`
- `qa_no_prompt_leak_for_detailed_question`

確認コマンド:

```bash
python3 /Users/<user>/OpenClaw/scripts/roby-eval-harness.py --json
```

失敗ケースだけ確認:

```bash
jq '.results[] | select(.ok==false) | {id, failures, route, execute, elapsed_ms}' ~/.openclaw/roby/evals/latest.json
```

ケース単体で再現:

```bash
python3 /Users/<user>/OpenClaw/scripts/roby-eval-harness.py --case qa_local_status_neuronic --json
```

切り分け観点:

- `route` が想定と違う
  - `scripts/roby-orchestrator.py` の `classify_intent_heuristic` / `SELF_STATUS_HINTS` を確認
- `action.mode` が `local_status` にならない
  - `is_self_status_request(...)` の判定語を確認
- `not_contains` 違反（プロンプト断片漏れ）
  - `is_broken_qa_output(...)` / `compact_qa_message(...)` 付近を確認
- レイテンシのみ失敗
  - `config/pbs/eval_policy.json` の `max_p95_ms` と AB制御（eval実行時はAB無効）を確認

### gateway_status FAIL

- `node /Users/<user>/OpenClaw/openclaw.mjs gateway status`
- 必要なら `node /Users/<user>/OpenClaw/openclaw.mjs gateway restart`
- 起動系は `/Users/<user>/OpenClaw/docs/roby_orchestrator_cron_runbook.md` を参照

### orchestrator_qa_smoke FAIL

- `python3 /Users/<user>/OpenClaw/scripts/roby-orchestrator.py --route qa_gemini --message "こんにちは" --execute --json`
- `~/.openclaw/roby/orchestrator_runs.jsonl` を確認

### ollama_health FAIL

- `ollama --version` でCLI確認
- `curl -s http://127.0.0.1:11434/api/tags` でAPI確認
- `ROBY_ORCH_OLLAMA_MODEL` が `models` に含まれるか確認
- Ollama導入を必須運用にする場合は `ROBY_DRILL_REQUIRE_OLLAMA=1` を設定

### eval_harness_smoke FAIL

- `python3 /Users/<user>/OpenClaw/scripts/roby-eval-harness.py --json`
- `~/.openclaw/roby/evals/latest.json` の `gates.failures` / `results[].failures` を確認

### eval_self_awareness_cases FAIL

- `python3 /Users/<user>/OpenClaw/scripts/roby-eval-harness.py --case qa_local_status_ollama --case qa_local_status_neuronic --case qa_feature_list_quality --case qa_no_prompt_leak_for_detailed_question --json`
- `~/.openclaw/roby/evals/latest.json` で `results[].id` と `failures` を確認
- 切り分け観点は本書「#3 Evaluationケース（自己把握/プロンプト漏れ）の見方」を参照

### audit_verify FAIL

- `python3 /Users/<user>/OpenClaw/scripts/roby_audit.py verify --json`
- 監査ログ: `~/.openclaw/roby/audit/events.jsonl`
- 破損行がある場合はファイル退避後、以降を新規作成し、原因調査をIssue化

### gmail_triage_dry_run FAIL

- OAuth資格情報と `GOG_ACCOUNT` を確認
- `python3 /Users/<user>/OpenClaw/skills/roby-mail/scripts/gmail_triage.py --account <account> --query "newer_than:1d in:inbox" --max 5 --dry-run --verbose`

### minutes_neuronic_regression FAIL

- `python3 /Users/<user>/OpenClaw/scripts/tests/test_roby_minutes_neuronic.py`
- 重点確認:
  - `parent_origin_id / sibling_order` 正常系
  - 413分割再送
  - legacyレスポンス互換

### gmail_neuronic_regression FAIL

- `python3 /Users/<user>/OpenClaw/skills/roby-mail/scripts/test_gmail_triage_neuronic.py`
- 重点確認:
  - `/tasks/import` 404時 `/tasks/bulk` フォールバック
  - 413分割再送

## 監査連携

`ROBY_IMMUTABLE_AUDIT=1` 時、drill実行結果は監査ログへ記録:

- `event_type=runbook_drill.run`
- 保存先: `~/.openclaw/roby/audit/events.jsonl`

## Slack通知

- 既定: **失敗時のみ通知**
- Webhook: `SLACK_WEBHOOK_URL`
- 成功時も通知したい場合:
  - 実行時 `--notify` を付与
  - または env `ROBY_DRILL_NOTIFY_ON_PASS=1`

## 推奨運用

- 毎週1回（作業開始前）に drill を実行
- FAIL発生時はその週の新規開発より先に修復
- drill結果を GitHub Project の Weekly Focus / Done This Week に反映

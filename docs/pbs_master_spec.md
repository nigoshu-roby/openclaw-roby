# PBS Master Spec (v1.0 Working)

更新日: 2026-03-04  
オーナー: Shu / Roby  
目的: PBS（Project Beyond Synapse）の正本仕様として、構成・契約・運用・進捗確認方法を一本化する。

---

## 1. Mission / Scope

### Mission

- ローカル優先で、仕事の入力（議事録/メール）を高品質に処理し、実行可能なタスクへ変換して行動に接続する。
- 抽出結果に対してユーザーが低コストでフィードバックできる運用を作る。
- 品質・速度・コストのバランスを維持しながら継続的に改善する。

### In Scope

- Notion / Google Docs / Gmail の入力処理
- タスク抽出・細分化・Neuronic連携
- Orchestrator（QA/Coding/Pipelineのルーティング）
- 評価ハーネス、ABルーター、監査ログ、Runbook

### Out of Scope（当面）

- 外部販売向けのマルチテナント化
- 高可用性クラスタ構成

---

## 2. Architecture

### 2.1 システム境界

- Roby/OpenClaw: `/Users/<user>/OpenClaw`
- Neuronic API: `/Users/<user>/Documents/New project/taskd`
- Neuronic UI: `/Users/<user>/Documents/New project/TaskToolMac`
- Neuronic 公開リポジトリ（統合先）:
  - `https://github.com/nigoshu-roby/Neuronic.git`

### 2.2 オーケストレーション方針

- QA系: Gemini ルート（相談・要件整理）
- Coding系: Codex ルート（実装）
- Pipeline系: minutes / gmail / self-growth
- 実装の原則:
  - ルーティングは明示的に残す
  - 実行結果は「結論 / 実行ログ / エラー理由」で表示
  - 再実行可能なUI導線を維持

### 2.3 Local First / Cloud for Brilliance

- ローカル: 安定処理・低コスト処理（将来 Ollama を段階導入）
- クラウド: 複雑推論・実装生成

### 2.4 AB Router（#9）

- 対象: QAルート（`qa_gemini`）
- 目的: 品質/速度のバランスをA/B実験で最適化
- 設定:
  - `config/pbs/ab_router.json`
  - `ROBY_ORCH_AB_ROUTER=1` で有効化
- 記録:
  - `~/.openclaw/roby/ab_router_runs.jsonl`
- 初期状態:
  - 既定は `enabled=false`（安全デフォルト）
  - Arm A=baseline / Arm B=quality_plus（重み付き）

---

## 3. Data Contracts（統合観点）

### 3.1 Roby -> Neuronic（import）

- Endpoint:
  - `POST /api/v1/tasks/import`
  - 404時: `POST /api/v1/tasks/bulk`
- 必須:
  - `title`, `source`, `origin_id`
- 親子/順序:
  - `parent_origin_id`, `sibling_order`
- 拡張メタ:
  - `external_ref`, `run_id`, `feedback_state`, `source_doc_id`, `source_doc_title`
- キー互換:
  - snake_case / camelCase の両受理

### 3.2 Response

- 基本:
  - `created`, `updated`, `skipped`, `errors`
- 拡張:
  - `hierarchy_applied`, `order_applied`

### 3.3 フィードバック

- Endpoint:
  - `POST /api/v1/tasks/:id/feedback`
- `feedback_type`:
  - `good | bad | missed`

### 3.4 タグ運用

- `group:*` は移行対象（廃止方向）
- UIでは内部タグ（`group:` / `origin:`）を非表示

---

## 4. PBS Phases（P0-P5）

### P0: 基盤

- オーケストレーター稼働
- ランタイム/起動/ログの安定化

### P1: 入力処理

- Notion/GDocs/Gmailの取り込み
- 抽出前の対象確認フロー

### P2: 実行接続

- Neuronic連携（親子・順序）
- フィードバック収集導線

### P3: 品質

- Evaluation Harness
- fallback抑制とRecall改善

### P4: 運用

- Runbook/Drill
- 監視・定期ジョブ運用

### P5: 最適化

- AB Router
- コスト/レイテンシ最適化

---

## 5. Progress Tracking（進捗確認方法）

### 5.1 GitHub（進捗管理）

- Weekly Focus: 今週やること
- Done This Week: 完了確認
- Blocks/Bugs: リスクと障害

### 5.2 Notion（意味管理）

- PBS Snapshot:
  - 目的、意思決定、方針変更、次アクション
- Phase列（P0-P5）で状態同期

### 5.3 ローカルCLI（実行確認）

- Orchestrator:
  - `python3 /Users/<user>/OpenClaw/scripts/roby-orchestrator.py --message "現在の機能をリスト化してください" --execute --json`
- Minutes:
  - `python3 /Users/<user>/OpenClaw/scripts/roby-minutes.py --list`
- Gmail:
  - `python3 /Users/<user>/OpenClaw/skills/roby-mail/scripts/gmail_triage.py --account <MAIL> --query "newer_than:1d in:inbox" --max 20 --dry-run --verbose`
- taskd health:
  - `curl -s http://127.0.0.1:5174/health`
- Drill:
  - `python3 /Users/<user>/OpenClaw/scripts/roby-drill.py --json`

---

## 6. Quality Gates

### 6.1 契約ゲート

- 変更前に I/F差分（request/response）を明示
- 互換性破壊がある場合は移行手順を必須化

### 6.2 実装ゲート

- build/testが通ること
- ランタイム確認（health / launchd）

### 6.3 運用ゲート

- 実行ログに再現可能な証跡を残す
- 失敗時の回復手順をRunbookへ反映

### 6.4 Evaluation Harness Gate（#8）

- ケース定義: `config/pbs/eval_cases.json`
- ポリシー定義: `config/pbs/eval_policy.json`
- 出力:
  - 最新JSON: `~/.openclaw/roby/evals/latest.json`
  - 履歴: `~/.openclaw/roby/evals/history.jsonl`
  - 可読レポート: `~/.openclaw/roby/evals/latest.md`
- ゲート判定（hardening）:
  - `max_failed_cases`
  - `max_failure_rate`
  - `allow_new_failures`（前回比較）
  - `max_avg_ms` / `max_p95_ms`
  - 一時障害の自動リトライ（`max_retries` / `retry_delay_ms`）

### 6.5 Immutable Audit（#7）

- 監査ログ: `~/.openclaw/roby/audit/events.jsonl`
- 方式: append-only + hash chain（`prev_hash` / `hash`）
- 収集イベント（現行）:
  - `orchestrator.run`
  - `self_growth.run`
  - `minutes_sync.run`
  - `evaluation_harness.run`
- 検証コマンド:
  - `python3 /Users/<user>/OpenClaw/scripts/roby_audit.py verify --json`
- 制御:
  - `ROBY_IMMUTABLE_AUDIT=1`（既定ON）

---

## 7. Risk Register（初版）

- R-01: 文脈肥大による回答品質低下
  - 対応: 直近会話ウィンドウ上限、ログ表示分離
- R-02: groupタグ汚染によるUIフィルタ劣化
  - 対応: 内部ID分離、内部タグ非表示
- R-03: 401/404/413の再発
  - 対応: preflight / fallback / payload分割
- R-04: 仕様と実装の乖離
  - 対応: 本書とハンドオフ仕様を同時更新

---

## 8. Immediate Backlog（統合優先）

- 現在の優先バックログは消化済み（#7/#8/#9/#10/#11/#12 完了）
- 次アクションは GitHub Weekly Focus で管理

### 8.1 Completion Update（#11/#12）

- GitHub Issue:
  - #11: `https://github.com/nigoshu-roby/openclaw-roby/issues/11`（Closed）
  - #12: `https://github.com/nigoshu-roby/openclaw-roby/issues/12`（Closed）
- GitHub Project（PBS Program）:
  - #11 / #12 を `Done` に移行済み（2026-03-04）

### 8.2 Completion Update（#8 Evaluation Harness hardening）

- 完了日: 2026-03-04
- 実装:
  - `scripts/roby-eval-harness.py` を production hardening 版へ更新
  - `config/pbs/eval_policy.json` を追加（閾値/リトライ/ドリフト判定）
  - `scripts/install_roby_orchestrator_cron.sh` に `eval_harness` 定期実行オプションを追加
  - `scripts/roby-cron-dispatch.sh` / `scripts/uninstall_roby_orchestrator_cron.sh` を `eval_harness` 対応
- 運用:
  - `ROBY_ORCH_ENABLE_EVAL=1` で cron 有効化
  - `~/.openclaw/roby/evals/latest.md` で最新結果を可視化

### 8.3 Completion Update（#9 AB Router）

- 完了日: 2026-03-04
- 実装:
  - `scripts/roby-orchestrator.py` に QA向けAB選択ロジックを追加
  - `config/pbs/ab_router.json` を追加（A/B arm定義）
  - 実行ログを `~/.openclaw/roby/ab_router_runs.jsonl` へ保存
- 運用:
  - `ROBY_ORCH_AB_ROUTER=1` で有効化
  - 初期設定は `enabled=true`（A/B比率は `config/pbs/ab_router.json` で調整）

### 8.4 Completion Update（#7 Immutable Audit）

- 完了日: 2026-03-04
- 実装:
  - `scripts/roby_audit.py` を追加（append / verify）
  - hash chain付き append-only 監査ログを導入
  - `roby-orchestrator.py` / `roby-self-growth.py` / `roby-minutes.py` / `roby-eval-harness.py`
    から監査イベントを自動記録
- 運用:
  - 監査確認: `python3 /Users/<user>/OpenClaw/scripts/roby_audit.py verify --json`

### 8.5 Completion Update（#10 Runbook/Drill）

- 完了日: 2026-03-04
- 実装:
  - `scripts/roby-drill.py` を追加（運用ドリル実行）
  - 出力:
    - `~/.openclaw/roby/drills/latest.json`
    - `~/.openclaw/roby/drills/history.jsonl`
    - `~/.openclaw/roby/drills/latest.md`
  - 監査連携:
    - `event_type=runbook_drill.run`
- Runbook:
  - `docs/roby_runbook_drill.md`
- 通知:
  - 既定は失敗時のみ Slack 通知（`SLACK_WEBHOOK_URL`）
  - 成功時も通知する場合は `ROBY_DRILL_NOTIFY_ON_PASS=1`

### 8.6 Completion Update（週次運用レポート自動化）

- 完了日: 2026-03-05
- 実装:
  - `scripts/roby-weekly-report.py` を追加
    - Evaluation/Drill/AB/Audit を 7日窓で集計
    - 出力:
      - `~/.openclaw/roby/reports/weekly_latest.json`
      - `~/.openclaw/roby/reports/weekly_latest.md`
      - `~/.openclaw/roby/reports/weekly_history.jsonl`
  - `scripts/roby-orchestrator.py` に `weekly_report` ルート追加
  - `scripts/roby-cron-dispatch.sh` に `weekly_report` タスク追加
  - cron install/uninstall / runbook ドキュメントを `weekly_report` 対応
- 運用:
  - `ROBY_ORCH_ENABLE_WEEKLY_REPORT=1` で cron 有効化
  - 通知: `ROBY_WEEKLY_REPORT_NOTIFY=1`（Slack Webhook設定時）

---

## 9. Change Management

- 本書更新トリガー:
  - 契約変更（API/スキーマ）
  - フェーズ進捗の状態遷移
  - 運用手順の変更
- 変更時は以下を必ず更新:
  - GitHubタスク状態
  - Notion PBS Snapshot
  - 必要なら本書

---

## 10. Handoff Checkpoint（#11/#12）

- 日付: 2026-03-04
- 目的: 別スレッドで現状確認できるよう、#11/#12の実装状態を固定化

### OpenClaw（Roby側）

- Commit: `1d81e4170`
- Branch: `main`
- Push先: `origin (nigoshu-roby/openclaw-roby)` 反映済み
- 対象:
  - `scripts/roby-minutes.py`
  - `skills/roby-mail/scripts/gmail_triage.py`
  - `docs/pbs_master_spec.md`

### Neuronic（ローカル統合リポジトリ）

- Commit:
  - `aa1de0e`（#11/#12 実装）
  - `a55f304`（#12 契約整合の最終化）
  - `96769b8`（Canonical Remote/公開運用ルール追記）
- Branch: `master`
- Push先: `origin (nigoshu-roby/Neuronic)` 反映済み
- 対象:
  - `taskd` feedback API / feedback_state 永続化 / internal tag分離
  - `TaskToolMac` 一覧行評価ボタン / 永続ハイライト / 詳細評価UI削除
  - `docs/neuronic_outsourcing_handoff_2026-03-04.md`
- GitHubステータス:
  - Issue #11/#12: Closed
  - Project Status: Done

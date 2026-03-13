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

- Roby/OpenClaw: `<OPENCLAW_REPO>`
- Neuronic API: `<NEURONIC_TASKD_REPO>`
- Neuronic UI: `<NEURONIC_UI_REPO>`
- Neuronic 公開リポジトリ（統合先）:
  - `https://github.com/nigoshu-roby/Neuronic.git`

### 2.2 オーケストレーション方針

- QA系: Gemini ルート（相談・要件整理）
- QAローカル系: Ollama ルート（明示指定時にローカル回答、失敗時はGeminiへフォールバック）
- Coding系: Codex ルート（実装）
- Pipeline系: minutes / gmail / self-growth
- Route別負荷分離（新規）:
  - minutes: `ROBY_ORCH_MINUTES_LLM_PROFILE=local|hybrid|cloud`
  - gmail: `ROBY_ORCH_GMAIL_PROFILE=fast|hybrid|quality`
  - 既定は `minutes=hybrid`, `gmail=fast`（コスト/速度優先）
- 実装の原則:
  - ルーティングは明示的に残す
  - 実行結果は「結論 / 実行ログ / エラー理由」で表示
  - 再実行可能なUI導線を維持
  - 機能一覧要求は `ROBY_ORCH_FEATURE_LIST_LOCAL_FIRST=1`（既定）でローカル実体ベース回答を優先

### 2.3 Local First / Cloud for Brilliance

- ローカル: 安定処理・低コスト処理（将来 Ollama を段階導入）
- クラウド: 複雑推論・実装生成
- 導入方針（現行）:
  - `qa_ollama` を明示ルートとして追加
  - `ROBY_ORCH_OLLAMA_MODEL` / `ROBY_ORCH_OLLAMA_TIMEOUT_SEC` で運用調整
  - `ROBY_ORCH_OLLAMA_FALLBACK_QA=1` で失敗時に `qa_gemini` へ自動フォールバック
  - 品質チューニング項目:
    - `ROBY_ORCH_OLLAMA_TEMPERATURE`（既定 0.25）
    - `ROBY_ORCH_OLLAMA_TOP_P`（既定 0.9）
    - `ROBY_ORCH_OLLAMA_REPEAT_PENALTY`（既定 1.05）
    - `ROBY_ORCH_OLLAMA_NUM_PREDICT`（既定 2200）
    - `ROBY_ORCH_OLLAMA_MIN_OUTPUT_CHARS`（既定 40）
  - 品質ガード:
    - broken/truncated/短すぎる出力は `qa_gemini` に自動フォールバック
    - 「短く/3行で」等の短文要求時は最小文字数チェックを緩和

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
- 運用ガード（新規）:
  - `qa_gemini.health_guard` で劣化armを自動退避
  - 既定:
    - `guarded_arm_ids=["B"]`
    - `fallback_arm_id="A"`
    - `window_runs=50`
    - `min_samples=3`
    - `max_fail_rate=0.15`
    - `max_avg_elapsed_ms=20000`
  - 目的:
    - B armの品質/速度劣化時にAへ自動フォールバックし、体感品質を維持

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
  - 同期ポリシー:
    - 既定: `ROBY_NEURONIC_HIERARCHY_MODE=create_only`
    - 初回作成時のみ親子/順序を送信し、再同期時は階層フィールドを省略
    - 目的: Neuronic上で手動変更した親子構造（再親化/子の子化）を保持
    - 常時上書きが必要な場合のみ `ROBY_NEURONIC_HIERARCHY_MODE=always`
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

### 現在の優先領域（2026-03-12時点）

- Phase 0〜5 はコア完了扱い
- 現在は追加機能ではなく `Precision Sprint` を優先する
- 詳細仕様: `docs/pbs_precision_sprint_spec.md`
- 進行管理: `docs/pbs_precision_status.md`
- 追加開発バックログは Core と分けて管理する

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
  - `python3 ./scripts/roby-orchestrator.py --message "現在の機能をリスト化してください" --execute --json`
- Minutes:
  - `python3 ./scripts/roby-minutes.py --list`
- Gmail:
  - `python3 ./skills/roby-mail/scripts/gmail_triage.py --account <MAIL> --query "newer_than:1d in:inbox" --max 20 --dry-run --verbose`
- taskd health:
  - `curl -s http://127.0.0.1:5174/health`
- Drill:
  - `python3 ./scripts/roby-drill.py --json`

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
  - `python3 ./scripts/roby_audit.py verify --json`
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
- 次アクションは GitHub Weekly Focus と `docs/pbs_precision_status.md` で管理
- 完了更新履歴と handoff checkpoint の詳細は `/Users/shu/OpenClaw/docs/pbs_completion_log.md` を参照

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

- #11/#12 を含む handoff の履歴詳細は `/Users/shu/OpenClaw/docs/pbs_completion_log.md` を参照
- 現在の正本は以下の3点
  - `/Users/shu/OpenClaw/docs/pbs_master_spec.md`
  - `/Users/shu/OpenClaw/docs/pbs_precision_status.md`
  - `/Users/shu/OpenClaw/MEMORY.md`

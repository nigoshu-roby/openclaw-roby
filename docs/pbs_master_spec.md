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

1. #8 Evaluation Harness（production hardening残作業）
2. #9 AB Router
3. #7 Immutable Audit
4. #10 Runbook/Drill
5. #11 NeuronicフィードバックUI/API 仕上げ
6. #12 内部ID分離移行 完全化

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

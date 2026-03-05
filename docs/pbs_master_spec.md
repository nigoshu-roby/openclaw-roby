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

### 8.10 Completion Update（Ollama導入フェーズ着手）

- 完了日: 2026-03-05
- 実装/運用:
  - Homebrewで `ollama` を導入し、LaunchAgent常駐化
  - 初期モデルを導入:
    - `qwen2.5:7b`（標準ローカルQA）
    - `llama3.2:3b`（軽量/高速QA）
  - `qa_ollama` ルートを有効運用化（失敗時 `qa_gemini` 自動フォールバック）
  - `~/.openclaw/.env` にローカル運用設定を反映:
    - `ROBY_ORCH_OLLAMA_MODEL=qwen2.5:7b`
    - `ROBY_ORCH_OLLAMA_TIMEOUT_SEC=120`
    - `ROBY_ORCH_OLLAMA_FALLBACK_QA=1`

### 8.11 Completion Update（minutes/gmail の Ollama段階導入）

- 完了日: 2026-03-05
- 実装:
  - `roby-orchestrator.py` に route別 LLM profile 適用を追加
    - minutes: 実行前に `MINUTES_*_MODELS` を profileに応じて動的上書き
    - gmail: 実行前に `GMAIL_TRIAGE_LLM_*` を profileに応じて動的上書き
  - `gmail_triage.py` に optional LLM判定を追加（既定OFF）
    - 曖昧カテゴリ（needs_review/later_check/needs_reply）のみ対象
    - 明示ルール適用メールは対象外
    - `needs_reply -> archive` の危険な降格は抑止
    - summaryに `llm_reviewed` / `llm_overrides` を追加
- 運用デフォルト:
  - `ROBY_ORCH_GMAIL_PROFILE=fast`（LLM未使用）
  - 必要時のみ `hybrid` / `quality` へ切替

### 8.12 Completion Update（#5 Recall改善: coverage pass）

- 完了日: 2026-03-06
- 実装:
  - `scripts/roby-minutes.py` に **coverage pass** を追加
    - 2段階抽出（review + tasks）後、長文かつ件数不足時のみ追加LLM抽出を実行
    - 既存タイトルを入力し、重複を避けながら不足タスクを補完
  - 追加設定キー:
    - `MINUTES_COVERAGE_MODELS`
    - `MINUTES_COVERAGE_MAX_TOKENS`
    - `MINUTES_COVERAGE_RETRY_MAX_TOKENS`
    - `MINUTES_COVERAGE_LENGTH`
    - `MINUTES_COVERAGE_TIMEOUT_SEC`
    - `MINUTES_COVERAGE_RETRY_TIMEOUT_SEC`
- 目的:
  - fallback（heuristic）に依存せず、LLMのみで抽出件数（Recall）を戻す
  - 既存のメモ混入抑制方針を維持

### 8.13 Completion Update（#4 Gmail連携の堅牢化）

- 完了日: 2026-03-06
- 実装:
  - `skills/roby-mail/scripts/gmail_triage.py` の Neuronic送信を強化
    - 413 (`Payload Too Large`) 発生時に自動分割して再送
    - `/tasks/import` 404 時は `/tasks/bulk` へフォールバック維持
    - 送信集計を `created/updated/skipped/error_count` で集約
    - `hierarchy_applied/order_applied` 返却がある場合は summary に反映
  - 回帰テストを追加:
    - `skills/roby-mail/scripts/test_gmail_triage_neuronic.py`
      - 413分割再送の成功ケース
      - 404フォールバックの成功ケース
- 目的:
  - Gmail→Neuronic連携での大容量失敗を自動回復し、運用停止を防ぐ

### 8.14 Completion Update（#4 Gmail運用閾値チューニング）

- 完了日: 2026-03-06
- 実装:
  - `skills/roby-mail/scripts/gmail_triage.py`
    - Slack通知上限を追加: `GMAIL_TRIAGE_NOTIFY_MAX_PER_RUN`（既定: `12`）
    - 1メールあたり抽出アクション上限: `GMAIL_TRIAGE_TASK_MAX_ACTIONS_PER_MAIL`（既定: `6`）
    - 1実行あたり送信タスク上限: `GMAIL_TRIAGE_TASK_MAX_ITEMS_PER_RUN`（既定: `120`）
    - summaryへ運用メトリクス追加:
      - `notify_suppressed`
      - `task_actions_capped`
      - `task_run_cap_reached`
  - 回帰テストを追加/更新:
    - `skills/roby-mail/scripts/test_gmail_triage_classify.py`
      - `cap_extracted_actions` の上限制御テスト
- 目的:
  - 通知スパムと過剰タスク生成を抑え、日次運用を安定化

### 8.15 Completion Update（#5 Minutes運用閾値チューニング）

- 完了日: 2026-03-06
- 実装:
  - `scripts/roby-minutes.py`
    - 1実行あたり候補件数の上限を追加:
      - `MINUTES_MAX_CANDIDATES_PER_RUN`（既定: `30`）
    - 1実行あたり送信タスク上限を追加:
      - `MINUTES_MAX_TASKS_PER_RUN`（既定: `120`）
    - Slack通知の抑制制御:
      - `MINUTES_NOTIFY_ON_NO_CHANGE`（既定: `0`）
      - デフォルトは「タスク生成あり or エラー時のみ通知」
    - summary/監査に運用メトリクスを追加:
      - `candidates_total`, `candidates_selected`, `candidate_items_capped`
      - `task_run_capped`, `task_run_cap_reached`
      - `slack_notified`
- 目的:
  - 大量候補時の負荷/ノイズを抑えつつ、定常運用を安定化

### 8.16 Completion Update（#6 回帰テストの運用ドリル統合）

- 完了日: 2026-03-06
- 実装:
  - `scripts/roby-drill.py` に必須チェックを追加
    - `minutes_neuronic_regression`
    - `gmail_neuronic_regression`
  - Runbook更新:
    - `docs/roby_runbook_drill.md`
    - 新チェックの確認手順と失敗時一次対応を追記
- 目的:
  - #6の回帰観点（階層/順序/フォールバック/分割再送）を定期ドリルで自動監視

### 8.17 Completion Update（Ollamaヘルスチェックを運用ドリルへ統合）

- 完了日: 2026-03-06
- 実装:
  - `scripts/roby-drill.py`
    - `ollama_health` チェックを追加
    - 判定内容:
      - Ollama CLI有無
      - `ROBY_ORCH_OLLAMA_BASE_URL` へのAPI接続（`/api/tags`）
      - `ROBY_ORCH_OLLAMA_MODEL` の存在確認
    - 運用スイッチ:
      - `ROBY_DRILL_REQUIRE_OLLAMA=1` で必須化（未導入/未接続/モデル未検出をFAIL）
      - 未設定時は任意チェック（未導入ならSKIP）
  - Runbook更新:
    - `docs/roby_runbook_drill.md`
    - `ollama_health` の一次対応手順を追記
- 目的:
  - Ollama導入フェーズ前後で「導入済みなのに使えない」状態を定期ドリルで即検知する

### 8.18 Completion Update（#3 想定失敗ケースのEvaluation強化）

- 完了日: 2026-03-06
- 実装:
  - `config/pbs/eval_cases.json` に回帰ケースを追加
    - `qa_local_status_ollama`
      - Ollama導入確認が `qa_ollama/local_status` で返ること
      - 幻覚系文言（「情報はありません」等）を禁止
    - `qa_local_status_neuronic`
      - Neuronic連携確認が `qa_gemini/local_status` で返ること
    - `qa_feature_list_local_summary`
      - 機能一覧要求でローカル検出サマリにフォールバックできること
    - `qa_no_prompt_leak_for_detailed_question`
      - 詳細質問時にプロンプト断片漏れ（`Extracted content length` 等）が出ないこと
  - `scripts/roby-eval-harness.py`
    - 評価実行時は `ROBY_ORCH_AB_ROUTER=0` を強制し、AB影響を除外した再現可能な測定へ統一
- 目的:
  - 過去に発生した「自己把握失敗」「機能一覧の低品質出力」「プロンプト漏れ/途中切れ」を
    Evaluation Harnessで常時回帰監視する

### 8.19 Completion Update（#3 失敗時Runbook切り分け手順の追加）

- 完了日: 2026-03-06
- 実装:
  - `docs/roby_runbook_drill.md`
    - #3向けの運用手順を追加
      - 対象ケース一覧
      - 失敗ケース抽出コマンド（`jq`）
      - ケース単体再実行コマンド
      - 判定失敗の切り分け観点（route/mode/not_contains/latency）
- 目的:
  - Eval失敗時に「どこを見るか」を固定化し、復旧時間を短縮する

### 8.20 Completion Update（#3 ケースをRunbook Drill必須チェックへ統合）

- 完了日: 2026-03-06
- 実装:
  - `scripts/roby-drill.py`
    - `eval_self_awareness_cases` チェックを追加（必須）
    - 実行内容:
      - `qa_local_status_ollama`
      - `qa_local_status_neuronic`
      - `qa_feature_list_quality`
      - `qa_no_prompt_leak_for_detailed_question`
        を `roby-eval-harness.py --case ...` でまとめて検証
  - `docs/roby_runbook_drill.md`
    - チェック一覧・一次対応手順に `eval_self_awareness_cases` を追記
- 目的:
  - 自己把握品質とプロンプト漏れ防止の回帰を、日次/週次ドリルで常時監視する

### 8.21 Completion Update（誤実行防止: Neuronic相談の自動ルーティング修正）

- 完了日: 2026-03-06
- 実装:
  - `scripts/roby-orchestrator.py`
    - `MINUTES_EXEC_HINTS` から汎用語（`連携`, `同期`）を除外
    - 意図:
      - 「Neuronicとの連携は？」のような相談/確認を `minutes_pipeline` へ誤分類しない
      - 実行系は `登録/抽出/タスク化` など明示動詞がある場合のみ実行ルートへ寄せる
  - `config/pbs/eval_cases.json`
    - `qa_local_status_neuronic` を `route=auto` に戻し、誤実行防止を回帰監視
- 目的:
  - 相談系メッセージでの意図せぬパイプライン実行を防ぎ、安全側（QA）に倒す

### 8.22 Completion Update（Notion同期の監査化 + ドリル組み込み）

- 完了日: 2026-03-06
- 実装:
  - `scripts/roby-notion-sync.py`
    - 実行結果に `phase_counts` を追加（dry-run/本実行共通）
    - 例外処理を明示化し、失敗時はJSONエラーで終了
    - `event_type=notion_sync.run` を Immutable Audit へ記録
  - `scripts/roby-drill.py`
    - `notion_sync_dry_run` チェックを追加（任意）
    - Notion token未設定時はSKIP、設定済みなら dry-run 実行
  - `docs/roby_runbook_drill.md`
    - `notion_sync_dry_run` の一次対応手順を追加
- 目的:
  - GitHub→Notion同期の健全性を定期ドリルに組み込み、同期不全を早期検知する

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

### 8.7 Completion Update（#6 Neuronic階層連携 回帰テスト）

- 完了日: 2026-03-05
- 実装:
  - `scripts/tests/test_roby_minutes_neuronic.py` を追加
  - 受け入れ条件に対応する3テストを実装:
    - `parent_origin_id / sibling_order` 正常系
    - legacyレスポンス互換（`hierarchy_applied/order_applied` 未返却）
    - `Payload Too Large` 時の分割再送（413→分割→成功）
- 検証:
  - `python3 /Users/<user>/OpenClaw/scripts/tests/test_roby_minutes_neuronic.py` で `OK`

### 8.8 Completion Update（#5 Minutes抽出精度改善）

- 完了日: 2026-03-05
- 実装:
  - `scripts/roby-minutes.py` のノイズ判定を強化（メモ/背景/曖昧タイトルを除外）
  - `infer_primary_project(...)` を追加し、議事録本文とタイトルから既知プロジェクトを推定
  - GDocs/Notion双方で project default を推定値に寄せ、`project:*` の精度を改善
  - 親タスクがノイズで子1件のみの場合は子タスクへ自動フラット化（階層の安定化）
- テスト:
  - 追加: `scripts/tests/test_roby_minutes_quality.py`
    - メモ混入抑制
    - project推定
    - 親子フラット化
  - 既存回帰: `scripts/tests/test_roby_minutes_neuronic.py` も通過

### 8.9 Completion Update（#4 Gmail仕訳 ルール投入/精度調整）

- 完了日: 2026-03-05
- 実装:
  - `skills/roby-mail/scripts/gmail_triage.py`
    - 重複していたルール関数定義を整理（単一実装化）
    - 初期ルールセットをデフォルト投入（`force_archive / force_review / force_reply`）
    - 既存ルールファイルへ defaults をマージして自動補完
    - `match_user_override` を `cc` 含め判定
  - 初期ルール方針:
    - 既知プロモーション送信元を `force_archive` に投入
    - 社内/運用通知系（`tokiwa-gi.com`, `crmstyle.com`, `autoro.io` など）を `force_review` に投入
- テスト:
  - 追加: `skills/roby-mail/scripts/test_gmail_triage_classify.py`
    - ルールbootstrap
    - internal domain in CC
    - promo domain archive
    - actionable notice review
    - LINE no-reply approval archive
    - force_reply override

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

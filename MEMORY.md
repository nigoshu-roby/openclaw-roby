# MEMORY.md

## Durable Facts

- 中核タスク管理は Neuronic。
- 議事録ソースは Notion と Google Docs。
- メール処理は Gmail triage で、必要に応じて Neuronic に親子タスク登録する。
- Slack は運用通知の主チャネル。
- Local First の前処理は Ollama、最終抽出や高品質判断は Gemini / Codex を使う。

## Stable Preferences

- UI / 通知 / ダッシュボードの表記は日本語優先。
- 「現在状態」と「履歴集計」は混同しない。
- タスク抽出は project 単位で見えることを重視する。
- フィードバックは low effort で返せる構造を優先する。

## Project Focus

- PBS を自律型 AI ワーカー基盤として完成させる。
- Quality Gate, Feedback Loop, Local First, Neuronic 連携を中核に据える。

## Precision Sprint Program

- 追加機能開発は一旦ステイし、メール・議事録・評価基盤の精度向上を優先する。
- Sprint A: Email Precision Sprint
  - 返信履歴ベース importance
  - archive/digest/review/task の4分類
  - high-confidence task のみ Neuronic 投入
- Sprint B: Minutes Precision Sprint
  - TOKIWAGI corpus 読込
  - project / owner / action pattern registry
  - project segmentation first と task rewrite / decomposition
- Sprint C: Eval Sprint
  - golden set / false negative / precision-recall 指標化
- 進行確認は `docs/pbs_precision_status.md` を正本とする。

## Post-PBS追加開発バックログ

- 詳細な候補一覧と検討メモは `/Users/shu/OpenClaw/docs/pbs_post_backlog.md` を参照。
- この `MEMORY.md` には、PBS の durable facts と live snapshot を優先して残す。

<!-- ROBY:MEMORY-SNAPSHOT:START -->

### 現在の運用状態

- 最終同期: 2026-06-19T15:55:00.421629+09:00
- heartbeat: HEARTBEAT_ATTENTION
- 未解消項目: Runbook Drill fail 1/13 / stale component: self_growth / notion_sync

### 監視ソース

- 週次集計: 2026-06-15T08:20:09.530692+09:00
- feedback: 2026-06-19T15:50:01.028525+09:00
- evaluation: 2026-06-19T12:35:00.893077+09:00
- drill: 2026-06-15T08:20:10.797440+09:00

### 品質ゲート

- evaluation(current): PASS 0/7
- drill(current): FAIL 1/13
- audit errors(7d): 0
- stale component(now): self_growth / notion_sync

### フィードバック要約

- reviewed 296 / actionable 205 / good 91 / bad 205 / missed 0
- Bad理由の上位:
  - wrong_project: 52
  - newsletter_false_positive: 25
  - should_be_review_only: 21
  - unclear: 15
  - duplicate: 9

### 直近の改善フォーカス

- 案件判定: 52
  - projectタグ付けと案件名推定のルールを見直す。
- メルマガ判定: 25
  - Gmail仕訳で広告・メルマガ判定を強め、確認/タスク化を抑制する。
- 確認タスク判定: 21
  - メールは『確認のみ』と『実行タスク化』を分け、確認止まりの条件を明確化する。

### 直近の要確認評価

- [bad / wrong_project] チケットショップのダッシュボードのグラフ、レイアウト、フィルターを整備する
- [bad / wrong_project] 営業資料強化：位置情報利用の促進のための営業資料のリニューアル。次週提示予定。（週内にGeminiで作成予定）
- [bad / wrong_project] 売上増戦略：名古屋の二次代理店（株）クリエーターズプラットフォームの自社運用案件の一広経由受注業務拡大に向けて基本的合意。自社運用の業務軽減を目的に発注システム利用の要望あり。
<!-- ROBY:MEMORY-SNAPSHOT:END -->

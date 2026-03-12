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

- 外部アクセス対応（PBS / Neuronic）
  - ローカル構築済みの PBS を外部アクセス可能にする。
  - iPhone / iPad から PBS にアクセスして処理実行できるようにする。
  - Neuronic も外部アクセス可能にし、iPhone からタスク追加・確認・ステータス変更できるようにする。

- タスクツールとカレンダー連携
  - 現在スケジュールを参照し、タスクの割り当て・時間配置を自動化する。
  - スケジュールに応じたタスク生成（例: 資料作成、アジェンダ送付）を行う。

- タスク細分化 / 生成精度向上
  - 議事録→タスク生成時に、メモ断片の貼り付け的な出力を減らす。
  - タスクとして実行可能な粒度・内容に改善する。
  - タスク名を適切な長さに凝縮する。

- OpenClaw / Ollama のアップデート運用
  - 最新アップデート情報を定期取得する。
  - 常に最新状態を維持できる更新フローを整備する。

- AI / OpenClaw 情報の継続収集
  - 最新AI事情・OpenClaw関連情報を自動収集する。
  - Robyへの機能追加や有用機能の導入可否を継続検討する。

- Workspace運用コードの本体統合
  - ~/.openclaw/workspace 側にある重要スクリプト（Slack受信サーバー、bridge等）を、将来的に /Users/shu/OpenClaw 側へ段階移管する。
  - 方針は「コードは repo、本番状態・secret・logs は ~/.openclaw」に寄せる。

- Slack投稿の取りこぼし防止
  - ローカルで Roby が停止していた間に投稿された Slack 内容を、起動後に確認できるようにする。
  - オフライン期間中の投稿取りこぼしが発生しない設計にする。

<!-- ROBY:MEMORY-SNAPSHOT:START -->

### 現在の運用状態

- 最終同期: 2026-03-12T01:49:27.387941+09:00
- heartbeat: HEARTBEAT_OK
- 未解消項目: なし

### 監視ソース

- 週次集計: 2026-03-11T20:21:17.368080+09:00
- feedback: 2026-03-08T23:33:09.242648+09:00
- evaluation: 2026-03-11T23:29:53.598954+09:00
- drill: 2026-03-11T20:21:17.150005+09:00

### 品質ゲート

- evaluation(current): PASS 0/7
- drill(current): PASS 0/13
- audit errors(7d): 0
- stale component(now): なし

### フィードバック要約

- reviewed 93 / actionable 69 / good 24 / bad 69 / missed 0
- Bad理由の上位:
  - not_actionable: 2

### 直近の改善フォーカス

- タスク抽出閾値: 2
  - 議事録/Gmail抽出で『依頼・期限・担当・次アクション』が弱い文を除外する。

### 直近の要確認評価

- [bad / not_actionable] メール確認: 自動支払いが完了しました
- [bad / not_actionable] メール確認: brodo_japan ― フィードでpresidenrepublikindonesia、0pipi_chuchuなどをチェックしよう
- [bad] 4/21
<!-- ROBY:MEMORY-SNAPSHOT:END -->

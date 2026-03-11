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

<!-- ROBY:MEMORY-SNAPSHOT:START -->

- 最終同期: 2026-03-12T01:08:28.937382+09:00
- heartbeat: HEARTBEAT_ATTENTION
- 週次集計の更新: 2026-03-11T20:21:17.368080+09:00
- feedback更新: 2026-03-08T23:33:09.242648+09:00
- フィードバック: reviewed 93 / actionable 69 / good 24 / bad 69 / missed 0
- 未解消項目: stale component: gmail_triage
- 直近の改善フォーカス:
  - タスク抽出閾値: 2
    - 議事録/Gmail抽出で『依頼・期限・担当・次アクション』が弱い文を除外する。
- 直近の要確認評価:
  - [bad / not_actionable] メール確認: 自動支払いが完了しました
  - [bad / not_actionable] メール確認: brodo_japan ― フィードでpresidenrepublikindonesia、0pipi_chuchuなどをチェックしよう
  - [bad] 4/21
  <!-- ROBY:MEMORY-SNAPSHOT:END -->

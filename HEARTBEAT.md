# HEARTBEAT.md

優先順で確認すること:

1. `現在の稼働状況` に stale がないか。
2. Evaluation Harness が fail していないか。
3. Runbook Drill が fail していないか。
4. Feedback Loop に actionable な失敗傾向が増えていないか。
5. 重大な障害がなければ `HEARTBEAT_OK` を返す。

通知する条件:

- stale が 1 件以上ある
- evaluation / drill が fail
- 週次レポートで未解消の運用異常がある

<!-- ROBY:HEARTBEAT-STATUS:START -->

- 最終同期: 2026-03-12T01:08:28.937382+09:00
- 現在状態: HEARTBEAT_ATTENTION
- stale component: gmail_triage
- eval fail runs (7d): 23
- drill fail runs (7d): 1
- audit errors (7d): 0
- 現在の未解消事項:
  - stale component: gmail_triage
- 次に見るべき改善対象:
  - タスク抽出閾値 - 議事録/Gmail抽出で『依頼・期限・担当・次アクション』が弱い文を除外する。
  <!-- ROBY:HEARTBEAT-STATUS:END -->

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

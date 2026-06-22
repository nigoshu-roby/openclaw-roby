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

### 判定

- 最終同期: 2026-06-19T15:55:00.421629+09:00
- 現在状態: HEARTBEAT_ATTENTION

### いま見るべき運用信号

- stale component: self_growth / notion_sync
- evaluation(current): PASS 0/7
- drill(current): FAIL 1/13
- eval fail runs (7d): 0
- drill fail runs (7d): 0
- audit errors (7d): 0

### 現在の未解消事項

- Runbook Drill fail 1/13
- stale component: self_growth / notion_sync

### 次に見るべき改善対象

- 案件判定
  - projectタグ付けと案件名推定のルールを見直す。
- メルマガ判定
  - Gmail仕訳で広告・メルマガ判定を強め、確認/タスク化を抑制する。
- 確認タスク判定 - メールは『確認のみ』と『実行タスク化』を分け、確認止まりの条件を明確化する。
<!-- ROBY:HEARTBEAT-STATUS:END -->

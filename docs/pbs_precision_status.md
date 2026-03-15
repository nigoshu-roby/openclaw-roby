# PBS Precision Sprint Status

更新日: 2026-03-12  
参照先: `docs/pbs_precision_sprint_spec.md`

---

## 現在の判断

- PBS Core（Phase 0〜5）: 完了
- 現在の優先領域: Precision Sprint（A/B/C）
- 追加機能開発: 一旦ステイ

## Precision KPI

### 現在値（基準）

- `overall precision`: `0.261`
- `gmail precision`: `0.304`
- `minutes precision`: `0.200`

### Sprint 1 目標

- `overall precision >= 0.50`
- `gmail precision >= 0.55`
- `minutes precision >= 0.45`

### Sprint 2 目標

- `overall precision >= 0.65`
- `gmail precision >= 0.70`
- `minutes precision >= 0.60`

### 補助観測

- Gmail:
  - `newsletter_false_positive` の削減
  - `should_be_review_only` の削減
- Minutes:
  - `wrong_project` の削減
  - `too_broad` の削減
  - `not_actionable` の削減

---

## 事前ゲート

| ID  | タスク                  | 状態 | GitHub Issue | 備考                                                                       |
| --- | ----------------------- | ---- | ------------ | -------------------------------------------------------------------------- |
| G1  | Gemini Bulk Budget Gate | 完了 | -            | 大規模 corpus 処理前に token 見積りを出し、confirm_required 以上は確認必須 |

## Sprint A: Email Precision Sprint

| ID  | タスク                                    | 状態 | GitHub Issue | 備考                                                                                           |
| --- | ----------------------------------------- | ---- | ------------ | ---------------------------------------------------------------------------------------------- |
| A1  | Reply history / contact importance index  | 完了 | #13          | `gmail_contact_index.py` でローカル索引化し、triage に known-contact 昇格を接続                |
| A2  | 4分類（archive/digest/review/task）導入   | 完了 | #13          | 運用バケット導入済み。Slack通知は digest/review/task、Neuronic投入は task のみ                 |
| A3  | newsletter / tool notify の重み付け見直し | 完了 | #13          | weighted bucket 導入。marketing は digest に寄せ、既知 tool notice は review に昇格            |
| A4  | 実行タスク・返信タスク抽出の再設計        | 完了 | #13          | reply / action を分離し、needs_reply は reply task を最低1件補完                               |
| A5  | Neuronic投入ゲート強化                    | 完了 | #13          | task bucket を confidence gate に通し、低信頼メールは review に降格                            |
| A6  | Gmail golden set / missed set 作成        | 完了 | #13          | `/Users/shu/OpenClaw/scripts/roby-gmail-eval-corpus.py` で local corpus 生成に接続             |
| A7  | context seed の送信者ヒント接続           | 完了 | #13          | `/Users/shu/OpenClaw/docs/pbs_context_seed.md` の重要送信者/メールを contact importance に接続 |

## Sprint B: Minutes Precision Sprint

| ID  | タスク                               | 状態 | GitHub Issue | 備考                                                                                               |
| --- | ------------------------------------ | ---- | ------------ | -------------------------------------------------------------------------------------------------- |
| B1  | TOKIWAGI corpus 読込設計             | 完了 | #14          | `TOKIWAGIインナー議事録` + `基礎情報` の237ページを corpus 化                                      |
| B2  | project registry 生成                | 完了 | #14          | `/Users/shu/.openclaw/roby/tokiwagi_master_registry_latest.json` に初期 registry 出力              |
| B3  | owner registry 生成                  | 完了 | #14          | owner mention 抽出の初期版を実装・出力                                                             |
| B4  | action pattern registry 生成         | 完了 | #14          | action pattern 集計の初期版を実装・出力                                                            |
| B5  | project segmentation first 実装      | 完了 | #14          | registry hints を使って task 抽出前に案件分割を実装                                                |
| B6  | task rewrite / decomposition 実装    | 完了 | #14          | 実行可能な粒度へ正規化。メモ系タイトルの書き換えと複数アクション分解を実装                         |
| B7  | minutes golden set / missed set 作成 | 完了 | #14          | `/Users/shu/OpenClaw/scripts/roby-minutes-eval-corpus.py` と local corpus 生成で接続               |
| B8  | owner filter / assignee gating 実装  | 完了 | #14          | 自分担当外の明示 assignee を Neuronic へ送らないよう minutes 側でフィルタを追加                    |
| B9  | project confidence gate 実装         | 完了 | #14          | project 根拠が弱い task や conflict project を minutes 側で除外して `wrong_project` を抑制         |
| B10 | document project hint gate 実装      | 完了 | #14          | source 文書から抽出した project hints に乗らない弱い task を minutes 側で除外                      |
| B11 | context seed の project / owner 接続 | 完了 | #14          | `/Users/shu/OpenClaw/docs/pbs_context_seed.md` の alias / owner / task ヒントを minutes 判定へ接続 |
| B12 | context seed の task 基準接続        | 完了 | #14          | project ごとの `task にしやすいもの / しなくてよいもの` を minutes 送信ゲートへ接続                |

## Sprint C: Eval Sprint

| ID  | タスク                      | 状態 | GitHub Issue | 備考                                                                   |
| --- | --------------------------- | ---- | ------------ | ---------------------------------------------------------------------- |
| C1  | Gmail golden set 整備       | 完了 | #15          | `roby-gmail-golden-curate.py` で代表ケースを curated set 化            |
| C2  | Minutes golden set 整備     | 完了 | #15          | `roby-minutes-golden-curate.py` で代表ケースを curated set 化          |
| C3  | false negative capture flow | 完了 | #15          | Gmail/Minutes の manual missed capture と eval corpus 連携で漏れ回収   |
| C4  | precision / recall 指標化   | 完了 | #15          | `roby-precision-metrics.py` で Gmail / Minutes / overall を算出        |
| C5  | weekly / dashboard 反映     | 完了 | #15          | Precision Metrics を週次/Slack/Robyダッシュボードへ反映                |
| C6  | Precision Eval Job          | 完了 | #15          | curated corpus を自動採点し precision / recall / usefulness を継続測定 |

---

## 進行ルール

- 実装着手時はこの表の状態を `進行中` に変える
- 完了時は `完了` に変える
- 仕様変更があれば `docs/pbs_precision_sprint_spec.md` も更新する
- GitHub Project / Notion PBS Snapshot へ同期して、ユーザーとRobyの双方が同じ状態を参照できるようにする

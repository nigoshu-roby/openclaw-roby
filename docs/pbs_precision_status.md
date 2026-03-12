# PBS Precision Sprint Status

更新日: 2026-03-12  
参照先: `docs/pbs_precision_sprint_spec.md`

---

## 現在の判断

- PBS Core（Phase 0〜5）: 完了
- 現在の優先領域: Precision Sprint（A/B/C）
- 追加機能開発: 一旦ステイ

---

## 事前ゲート

| ID  | タスク                  | 状態 | GitHub Issue | 備考                                                                       |
| --- | ----------------------- | ---- | ------------ | -------------------------------------------------------------------------- |
| G1  | Gemini Bulk Budget Gate | 完了 | -            | 大規模 corpus 処理前に token 見積りを出し、confirm_required 以上は確認必須 |

## Sprint A: Email Precision Sprint

| ID  | タスク                                    | 状態   | GitHub Issue | 備考                             |
| --- | ----------------------------------------- | ------ | ------------ | -------------------------------- |
| A1  | Reply history / contact importance index  | 未着手 | #13          | 過去返信履歴を重要信号として使う |
| A2  | 4分類（archive/digest/review/task）導入   | 未着手 | #13          | Neuronic投入は task のみ         |
| A3  | newsletter / tool notify の重み付け見直し | 未着手 | #13          | 請求/見積/契約は例外強化         |
| A4  | 実行タスク・返信タスク抽出の再設計        | 未着手 | #13          | 子タスクの実用性改善             |
| A5  | Neuronic投入ゲート強化                    | 未着手 | #13          | high-confidence only             |
| A6  | Gmail golden set / missed set 作成        | 未着手 | #13          | Eval Sprint と接続               |

## Sprint B: Minutes Precision Sprint

| ID  | タスク                               | 状態   | GitHub Issue | 備考                                   |
| --- | ------------------------------------ | ------ | ------------ | -------------------------------------- |
| B1  | TOKIWAGI corpus 読込設計             | 未着手 | #14          | 一括読込→構造化知識化                  |
| B2  | project registry 生成                | 未着手 | #14          | alias / related company / page mapping |
| B3  | owner registry 生成                  | 未着手 | #14          | 自分担当/他担当の切り分け              |
| B4  | action pattern registry 生成         | 未着手 | #14          | 会議調整/提案/実装等                   |
| B5  | project segmentation first 実装      | 未着手 | #14          | task抽出前に案件分割                   |
| B6  | task rewrite / decomposition 実装    | 未着手 | #14          | 実行可能な粒度へ正規化                 |
| B7  | minutes golden set / missed set 作成 | 未着手 | #14          | Eval Sprint と接続                     |

## Sprint C: Eval Sprint

| ID  | タスク                      | 状態   | GitHub Issue | 備考                |
| --- | --------------------------- | ------ | ------------ | ------------------- |
| C1  | Gmail golden set 整備       | 未着手 | #15          | 代表ケース 30〜50件 |
| C2  | Minutes golden set 整備     | 未着手 | #15          | 代表ケース 20〜30件 |
| C3  | false negative capture flow | 未着手 | #15          | 漏れ回収導線        |
| C4  | precision / recall 指標化   | 未着手 | #15          | usefulness も含む   |
| C5  | weekly / dashboard 反映     | 未着手 | #15          | 傾向を可視化        |

---

## 進行ルール

- 実装着手時はこの表の状態を `進行中` に変える
- 完了時は `完了` に変える
- 仕様変更があれば `docs/pbs_precision_sprint_spec.md` も更新する
- GitHub Project / Notion PBS Snapshot へ同期して、ユーザーとRobyの双方が同じ状態を参照できるようにする

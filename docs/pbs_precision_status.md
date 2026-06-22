# PBS Precision Sprint Status

更新日: 2026-06-22
参照先: `docs/pbs_precision_sprint_spec.md`

---

## 2026-06-22 ゴール逆算の精度改善

### 最高クオリティのゴール

- Neuronic に入る task は、`project / owner / actionable / parent-child / duplicate` の5条件を満たす high-confidence item に限定する。
- 議事録・Google Meet docs 由来 task は、単に親が存在するだけでなく、意味的に正しい project parent 配下へ入る。
- 再実行・同一 doc 再処理・抽出順変更があっても、同じ作業は同じ `origin_id` / 同じ親子 group に収まり、重複 task を増やさない。
- `wrong_project` のような粗い feedback reason は、再発防止に使える refined cause へ分解して観測する。

### 今回実施

- `semantic_parent_misnested` を precision diagnostics に追加。
  - `parent_origin_id` が存在していても、子 task の強い固有語が別 project を示している場合は、構造 orphan ではなく semantic misnesting として扱う。
  - dry-run で `運営会社一覧の情報提供依頼` が `ボーネルンド` 配下ではなく `LINE広告配信` 候補として検出されることを確認。
- duplicate diagnostics に `kind` を追加。
  - source title 由来の auto parent 重複は `parent_group_duplicate`、実作業の重複は `child_action_duplicate` として分けて見る。
- minutes sanitize の親 project 補正を強化。
  - 親 task の project が誤っていても、子 task 群の title/note に強い keyword hint があれば、親 project を補正してから Neuronic task を組み立てる。
  - `ボーネルンド / 2026/06/02社内定例MTG` の子に `運営会社一覧の情報提供依頼` があるケースを regression test 化。
- `roby-precision-repair-candidates.py` を追加。
  - 既存 live task を変更せず、semantic misnesting / duplicate の repair 候補を dry-run JSON として出力する。
  - 出力先: `/Users/shu/.openclaw/roby/precision_repair_candidates_latest.json`
  - 現在の live API 可視範囲では `semantic_parent_misnested=5`, `duplicate_groups=0`。
- `weak_project_evidence` 対策として、doc hints がある場合は LLM の explicit project label だけでは通さず、seed/doc/section/keyword/registry の外部根拠を要求する gate を追加。
- LINE広告配信の環境変化を context seed / diagnostics に反映。
  - BW / ブログウォッチャー / ビーコン管理システム / 広告識別子 / GAS / 本番移行は現在 LINE広告配信側の強い文脈として扱う。
  - 旧SNW/777BEACON 側は `777BEACON`, `SSBP`, `スイッチスマイル`, `ピナブル`, `サミネ` などの旧運用文脈で判定する。
  - ただし BW連携による接触数増加で SNW社との協力関係が再び強まる可能性があるため、SNW社との再始動・協業方針が明示される場合は `SNW様-777BEACON` も候補に残す。
  - repair 候補の `BWのビーコン管理システム...` と `ビーコン管理システム構築の進捗確認` は LINE広告配信に補正。
  - 更新後の repair 候補: `semantic_parent_misnested=6`, `duplicate_groups=0`。
- Gmail task UX を改善。
  - 単一の返信/対応タスクは親子化せず、フラットな1タスクとして Neuronic に投入する。
  - 複数の具体アクションがある場合だけ `メール対応: <件名>` を親にして子タスク化する。
  - 返信タスクのタイトルは `Re:` を落として、`【送信者】【返信】<件名>` の形に整える。
  - `URL + 候補日程 + ◯✕/回答 + 期限` のメールは、`指定のURLから候補日程の◯✕を回答する` と `回答したら<相手>に返信する` に分解し、期限を task due date に入れる。
- Gmail task extraction を deterministic pattern 主体から LLM task reader 主体へ変更。
  - `summarize_tasks()` のプロンプトを、件名コピーではなく本文から実行手順・完了条件・期限を読む設計へ更新。
  - LLM が具体 task を返した場合はそれを採用し、deterministic 抽出は LLM が task を返せなかった場合の fallback として使う。
  - LLM 返却 JSON は `tasks/action_items/actions` を正規化し、`due_date` の `YYYY/MM/DD` や `7月10日` 形式を `YYYY-MM-DD` に揃える。
  - 具体的な返信 task がある場合、`Re: <件名>` をそのままコピーした generic reply は削除する。
  - Gmail profile から `GMAIL_TRIAGE_TASK_LLM_MODEL` を明示し、fast/hybrid/quality のどの運用でも task extraction は LLM を使う。
- Gmail の「task 処理が必要かどうか」の初期判定を LLM-first semantic triage へ移行。
  - `semantic_triage_decision()` を追加し、`category / requires_user_action / requires_reply / deadline / is_broadcast / is_auto_notice / confidence / action_type / risk_flags / reason` を構造化して返す。
  - AI 判定のハードルを下げ、profile 既定では fast/hybrid/quality いずれも `GMAIL_TRIAGE_SEMANTIC_TRIAGE_ENABLE=1`、最大 `50` 件/run まで semantic triage を通す。
  - semantic triage の `requires_user_action` は `explicit_action_request` signal として既存 gate に接続し、`requires_reply` は `needs_reply` として task bucket に接続する。
  - archive 判定だけは安全弁を残し、broadcast / auto notice / promo 根拠がない archive は review 側に guarded する。
  - ChatGPT Plus/Business の通常サブスクは API 利用とは別請求のため、常時判定はローカル/低コストモデル、曖昧・重要ケースは Gemini/GPT API へ昇格できる構成を維持する。
- Gmail の文章理解から task extraction までは Gemini API を標準に変更。
  - `GMAIL_TRIAGE_SEMANTIC_TRIAGE_MODEL` と `GMAIL_TRIAGE_TASK_LLM_MODEL` の既定を `google/gemini-3-flash-preview` に設定。
  - `ROBY_ORCH_GMAIL_CLOUD_MODEL` または `GMAIL_TRIAGE_GEMINI_MODEL` で Gemini model を差し替え可能。
  - ローカル preclassify は補助として残すが、task 必要性判断と本文からの task 抽出は Gemini を主系にする。

### ゴールから逆算した次の計画

1. semantic misnesting の apply 方針を決める。
   - repair 候補 5 件を確認し、移動・再作成・完了済み放置のどれにするかを選ぶ。
2. `weak_project_evidence` の運用評価を続ける。
   - 今回の gate 後、次回 run で wrong_project が減るか、逆に false negative が増えないかを見る。
3. duplicate を「新規発生防止」と「既存整理」に分ける。
   - 新規は stable origin/group key で防ぎ、既存は `source_doc_id + similarity_key + project/suggested_project` で整理候補を出す。
4. missed capture を増やして recall を測れる状態にする。
   - 現在は precision 改善が中心。最高品質には、作りすぎだけでなく拾い漏れも評価対象にする必要がある。

### 検証

- `python3 -m unittest scripts.tests.test_roby_minutes_quality`: PASS `58 tests`
- `python3 -m unittest scripts.tests.test_roby_precision_diagnostics`: PASS `8 tests`
- `python3 -m unittest scripts.tests.test_roby_precision_repair_candidates`: PASS `2 tests`
- `python3 -m unittest scripts.tests.test_roby_gmail_tasks`: PASS `12 tests`
- `python3 -m unittest scripts.tests.test_roby_orch_profiles`: PASS `4 tests`
- `python3 -m unittest discover scripts/tests`: PASS `225 tests`
- `python3 -m unittest skills/roby-mail/scripts/test_gmail_triage_classify.py`: PASS `52 tests`
- `python3 -m unittest skills/roby-mail/scripts/test_gmail_triage_classify.py skills/roby-mail/scripts/test_gmail_triage_neuronic.py`: PASS `59 tests`

---

## 2026-06-15 精度再評価・本質改善

### 実行結果

- `roby-precision-diagnostics.py` を追加し、raw feedback reason を再利用可能な failure mode に再分類。
- 最新診断出力: `/Users/shu/.openclaw/roby/precision_diagnostics_latest.json`
- `python3 -m unittest discover scripts/tests`: PASS `205 tests`

### 現在の主要課題

| priority | 領域          | refined cause                            | 件数 | 対応方針                                                                                       |
| -------- | ------------- | ---------------------------------------- | ---: | ---------------------------------------------------------------------------------------------- |
| P0       | minutes       | `weak_project_evidence`                  |   43 | project 根拠が弱い task を作らない/送らない。section・doc・seed keyword の一致を必須寄りへ強化 |
| P0       | gmail         | `broadcast_mail_overcaptured`            |   24 | newsletter / broadcast を review/digest に寄せ、business word だけで task 化しない             |
| P1       | minutes       | `cross_project_topic_collision`          |    9 | alias ではなく強い keyword hint で project 衝突を検出し、誤 project を抑制                     |
| P1       | minutes       | `duplicate_same_doc_action`              |    8 | 同一文書・同一 project 内の類似 action を similarity key で抑制                                |
| P2       | minutes/gmail | `unclear`, `not_actionable`, `too_broad` |   28 | task shape の gate を強め、review-only と broad parent を分離                                  |

### 今回入れた改善

- context seed の `keywords` を alias とは別の `PROJECT_KEYWORD_HINTS_REGISTRY` に接続。
  - `設定`, `配信`, `導入` のような汎用語は除外。
  - `ビーコン`, `IDFA`, `BW` のような強い文脈語だけを project 判定・confidence gate に使う。
- minutes project confidence gate に keyword conflict を追加。
  - 例: `SNW様-第三者広告配信` に見えても本文が `ビーコン/IDFA` なら `SNW様-777BEACON` 側を優先/誤 project を抑制。
- 同一文書・同一 project 内の類似 child task を dedupe。
  - 完全一致だけでなく、project prefix・語尾・助詞差分を吸収した conservative key を使用。
- Gmail の broadcast / promo mail に business word が含まれるケースを review 止まりにする gate を追加。
  - `請求`, `契約`, `見積` などが含まれても、一斉配信形状で直接依頼がなければ Neuronic task にしない。
- `docs/pbs_context_seed.md` の `SNW様-777BEACON` 固有語に `ビーコン`, `IDFA`, `ブログウォッチャー`, `BW` を追加。

### 2026-06-15 親子化・重複の追加確認

- live Neuronic API で roby tasks を確認。
  - minutes/Google Meet docs 由来と思われる task: `612`
  - `Gemini によるメモ` 由来: `167`
  - orphan child（`parent_origin_id` があるが親 origin が存在しない）: `0`
  - 同一 `source_doc_id + project + title` の重複 key: `27`
- ただし `orphan child=0` は構造参照の欠落だけの確認であり、意味的に wrong parent へ入る misnesting は別問題として扱う。
- 実例: `運営会社一覧の情報提供依頼` は `ボーネルンド / 2026/06/02社内定例MTG` 配下に入っていたが、本来は一広 / LINE広告案件側の child task。
- 重複は主に過去 run で同一 doc が再処理され、同じ title でも `origin_id` が変わったもの。
- 追加対策:
  - parent/child の send group key を統一し、batch 分割で親子を別単位として扱わない。
  - `MINUTES_MAX_TASKS_PER_RUN` の cap は親子 group 単位で適用し、親だけ/子だけを送らない。
  - parent/child の `origin_id` を group index 非依存にし、抽出順が変わっても同じ doc/title/project は同じ origin になるようにした。
  - `create_only` 再同期でも child の `parent_origin_id` は送る。並び順だけ抑制し、過去に orphan 化した child の修復余地を残す。
  - `LINE広告配信` の context keyword に `一広`, `運営会社一覧`, `販売ルート`, `広告商品` を追加し、title から親文脈が削られても LINE広告側へ補正する。

### 次の優先順位

1. Minutes `weak_project_evidence`
   - project が seed/doc/section のどれにも支持されない場合、review 送りまたは drop に寄せる。
2. Gmail `broadcast_mail_overcaptured` の運用評価
   - 今回追加した review gate 後、次回 feedback で newsletter/task 化の残件を確認。
3. Minutes duplicate の残件
   - source title 由来の auto parent 重複が診断に出ているため、既存 task 履歴との重複照合も検討。
4. false negative capture
   - 依然 `missed=0` のため recall は暫定。手動 missed 登録の運用を継続強化。

---

## 2026-06-02 再評価

### 運用状態

- `HEARTBEAT_OK`
- `audit verify`: PASS `0 errors`
- `Runbook Drill`: PASS `13/13`
- `roby-cron-doctor.sh --deep`: `fails=0 warnings=0`
- `notion_sync`: 本番再同期成功

### Precision Metrics

| domain  | reviewed | good | bad | precision | review coverage |
| ------- | -------- | ---- | --- | --------- | --------------- |
| overall | 471      | 78   | 168 | `0.3171`  | `0.5223`        |
| gmail   | 184      | 29   | 70  | `0.2929`  | `0.5380`        |
| minutes | 287      | 49   | 98  | `0.3333`  | `0.5122`        |

- `missed=0` のため recall は引き続き暫定値。
- 2026-03-15 時点と比較すると minutes precision は `0.2000 -> 0.3333` に改善。
- Gmail precision は `0.3043 -> 0.2929` で、追加対策が必要。

### 次の優先順位

1. Gmail `newsletter_false_positive` の削減
   - Gmail corpus: `26`
   - 直近再現例 `【サクミルアンバサダー通信 Vol.11】...` を早期 archive へ追加済み。
2. Gmail `should_be_review_only` の削減
   - Gmail corpus: `21`
   - calendar invite や確認止まりメールの task 化をさらに抑制する。
3. Minutes `wrong_project` の削減
   - Minutes corpus: `56`
   - seed / registry metadata 強化は実装済み。
   - 最新例ではビーコン管理系 task が `ボーネルンド` に混ざるため、競合 project の優先順位を追加調整する。
4. false negative capture の運用化
   - `missed=0` のままでは recall を実測できない。
   - representative case の missed 登録を定常レビューへ追加する。

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

| ID  | タスク                                    | 状態 | GitHub Issue | 備考                                                                            |
| --- | ----------------------------------------- | ---- | ------------ | ------------------------------------------------------------------------------- |
| G1  | Gemini Bulk Budget Gate                   | 完了 | -            | 大規模 corpus 処理前に token 見積りを出し、confirm_required 以上は確認必須      |
| G2  | Gemini 初回投入フェーズ設計               | 完了 | -            | 初回投入の対象・目的・手順・定常運用への切替方針を仕様化                        |
| G3  | Gemini 初回投入（expanded / 3.1 Pro）実行 | 完了 | -            | expanded bundle を `google/gemini-3.1-pro-preview` で実行し、レビュー結果を整理 |

## Gemini 初回投入フェーズ

- 現在地:
  - local-first の前処理基盤は揃っている
  - `pbs_context_seed.md` は minutes / gmail 処理へ接続済み
  - expanded bundle による初回投入は完了
  - 返答は `docs/pbs_gemini_initial_seed_review.md` で採用 / 保留 / 不採用へ仕分け済み
- 次にやること:
  - 採用した判断軸だけを minutes / gmail adjudication へ反映
  - representative case と precision eval で効きを確認
  - 定常運用向けの少量 Gemini adjudication へ切り替える
- 目的:
  - project / owner / task 判定の仕事理解を Gemini に持たせる
  - 曖昧ケースの adjudication を local から Gemini へ一部移す
  - 大量投入後は少量・限定投入へ移行する

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
| B13 | low-self / broad title gate 実装     | 完了 | #14          | 低関与 project の曖昧 task と `PROJECTミーティング` のような broad title を minutes 側で抑制       |
| B14 | Gemini 曖昧候補 adjudication 実装    | 完了 | #14          | 明確なノイズだけ local で落とし、境界候補は Gemini が `task / drop / rewrite` を判断               |

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

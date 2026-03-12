# PBS Precision Sprint Spec

更新日: 2026-03-12  
オーナー: Shu / Roby  
目的: PBSの追加機能開発を一旦止め、メール・議事録・評価基盤の精度を上げるための再設計を行う。

---

## 1. なぜ今やるか

現在のPBSは機能面では十分に仕事を補佐できる状態まで進んでいるが、以下の課題が残っている。

- Gmail由来タスクにメルマガ・低重要度メールが混ざる
- メールから本当に必要な実行タスク・返信タスクが十分に抽出できない
- 議事録からのタスク化で project / owner / task 粒度の精度が不足している
- Neuronic フィードバックだけでは改善速度に限界がある

このため、ここからは「機能拡張」よりも「精度向上の再設計」を優先する。

---

## 2. 基本方針

### 2.1 ローカル主軸

- Ollama を大量読込・前処理・候補生成の主軸にする
- Gemini は最終判断・最終整形・難件のフォールバックに使う
- 毎回全文を読むのではなく、一度読んで構造化知識にする

### 2.2 精度改善の考え方

- メールは `archive / digest / review / task` の4分類へ再設計
- Neuronic に入れるのは high-confidence task のみ
- 議事録は `project segmentation -> owner判定 -> task rewrite/decomposition` の順で処理する
- フィードバックは good/bad だけでなく、golden set / false negative / precision-recall で評価する

### 2.4 Gemini Bulk Budget Gate

- 初期段階の大規模 corpus 処理（過去メール / Notion 全読込 / golden set 生成など）では、Gemini 本処理の前に必ず token 見積りを出す。
- 見積りは `scripts/roby-gemini-budget.py` を使う。
- `confirm_required` 以上の見積りが出た場合、ユーザー確認なしで本処理を開始しない。
- 目的:
  - トークン消費量の見える化
  - 財布と心の準備
  - 無駄な大量実行の防止
- 運用:
  - `decision=ok` ならそのまま実行
  - `decision=confirm_required` なら見積りをユーザーへ提示して確認待ち
  - `decision=blocked` なら処理を分割するか対象を絞る
- 代表コマンド:
  - `python3 /Users/shu/OpenClaw/scripts/roby-gemini-budget.py --label "minutes-corpus" --input-file <file> --input-file <file> --json`

### 2.3 追跡方法

- 正本仕様: このファイル
- 実行中の進捗: `docs/pbs_precision_status.md`
- プロジェクト管理: GitHub Project `PBS Program`
- 意味管理 / snapshot: Notion PBSページ（`roby-notion-sync.py` 同期対象）

---

## 3. Sprint A: Email Precision Sprint

### 3.1 目的

Gmail由来タスクのノイズを減らし、「本当にNeuronicへ入れるべき task」だけを登録できるようにする。

### 3.2 完成条件

- `archive / digest / review / task` の4分類が導入されている
- 返信履歴ベースの contact importance が導入されている
- high-confidence task のみ Neuronic に登録される
- 契約 / 請求 / 見積 / 障害通知は review 以上に引き上げられる
- false positive / false negative を計測できる

### 3.3 実装タスク

1. Reply history / contact importance index
2. 4分類パイプライン化
3. newsletter / promo / tool-notify の重み付け見直し
4. 実行タスク・返信タスク抽出の再設計
5. Neuronic投入ゲート強化
6. Gmail用 golden set / missed set 作成

### 3.4 主な成果物

- Gmail contact registry
- importance score
- task gate policy
- Gmail eval corpus

### 3.5 A1 実装メモ

- 返信履歴インデックスは `skills/roby-mail/scripts/gmail_contact_index.py` で生成する
- 出力先:
  - `~/.openclaw/roby/gmail_contact_index.json`
  - `~/.openclaw/roby/gmail_contact_index_runs.jsonl`
- 直近18か月の
  - `in:sent` メッセージ
  - `in:anywhere` スレッド
    から、以下をローカル索引化する
  - replied thread
  - sender importance
  - domain importance
- `gmail_triage.py` はこの索引を読み、既知相手・返信済みスレッドを `archive / later_check` から `needs_review` へ昇格できるようにする

### 3.6 A2 実装メモ

- `gmail_triage.py` の内部分類は従来の `archive / later_check / needs_review / needs_reply` を維持しつつ、運用バケットを以下に再編する
  - `archive`
  - `digest`
  - `review`
  - `task`
- 変換ルールの初期方針:
  - `archive` -> `archive`
  - `later_check` -> `digest`
  - `needs_review` -> `review`
  - `needs_reply` -> `task`
  - 会議調整系の `needs_review` は `task` へ昇格
- Slack 通知は `digest / review / task` に対して継続し、Neuronic に入れるのは `task` のみとする
- `raw_category` はログに残し、`category` は運用バケットを出す
- contact importance や local preclassify の override 後に運用バケットへ変換し、`raw_category` と `bucket_reason` の両方を run log / verbose 出力に残す
- summary には `categories`（運用バケット集計）と `raw_categories`（内部分類集計）を併記して、誤判定の切り分けに使う

### 3.7 A3 実装メモ

- newsletter / tool notify の扱いは、ルールだけでなく重み付きで調整する
- `decide_work_bucket()` では以下の score を合成して運用バケットを決める
  - `newsletter_score`
  - `review_score`
  - `task_score`
- 想定挙動:
  - marketing / promo の `needs_review` は `digest` へ降格できる
  - 既知の取引先・返信済み thread 由来の tool notice は `review` へ昇格できる
  - finance / contract / alert 系は `review` 以上を維持する
- `bucket_scores` を `meta` に残し、後続の誤判定分析に使う

### 3.8 A4 実装メモ

- メール task は `reply` と `action` の2系統へ分ける
- `needs_reply` のメールでは、抽出結果が弱くても reply task を最低1件補完する
- child task には `task_kind` を持たせ、Neuronic へは
  - `task_type:reply`
  - `task_type:action`
    のタグで送る
- generic なメモ題名（`対応`, `返信する`, `確認する` など）は、実行可能な文へ書き換える
- 親 task は `メール確認` に留め、子 task で
  - 返信すべきこと
  - 実行すべきこと
    を分離する

### 3.9 A5 実装メモ

- `task` バケットに入ったメールも、そのまま Neuronic に送らず `confidence gate` を通す
- 加点する主な信号:
  - `needs_reply`
  - reply task の存在
  - 会議調整
  - 重要通知 / business review / actionable notice / alert
  - due date
  - known contact / replied thread
  - generic ではない具体的な task 文
- 減点する主な信号:
  - newsletter score が高い
  - promo sender domain
  - `no-reply`
  - generic-only task 抽出
- gate を通過したものだけ `task` として Neuronic に送る
- 低信頼なものは `review` に降格し、Slack通知・run log も降格後バケットを使う
- summary には以下を残し、後続の誤判定分析に使う
  - `task_gate_downgraded`
  - `task_gate_reasons`
  - `meta.task_gate`

### 3.10 A6 実装メモ

- Gmail 用の評価コーパスは `/Users/shu/OpenClaw/scripts/roby-gmail-eval-corpus.py` で生成する
- 生成物は機密保護のため GitHub へ置かず、以下の local state に保存する
  - `~/.openclaw/roby/gmail_golden_set.json`
  - `~/.openclaw/roby/gmail_missed_set.json`
  - `~/.openclaw/roby/gmail_eval_corpus_summary.json`
- データソースは以下の突き合わせで作る
  - Neuronic 側の `feedback_state / feedback_reason_code`
  - `feedback_candidates.jsonl` に残した Gmail run manifest
- `good` は初期 `golden set` として扱う
- `missed` は false negative の育成用セットとして扱い、手動追加テンプレも同梱する
- summary には以下を残す
  - reviewed item 数
  - top feedback reasons
  - top senders
  - work bucket / task type の分布

---

## 4. Sprint B: Minutes Precision Sprint

### 4.1 目的

Notion / GDocs 議事録から、project / owner / task 粒度が正しいタスクを生成できるようにする。

### 4.2 完成条件

- TOKIWAGI配下から project registry が構築されている
- owner registry が構築されている
- project segmentation first で処理される
- task rewrite / decomposition が効いている
- 自分担当外のメモが Neuronic タスクに入りにくくなる

### 4.3 実装タスク

1. TOKIWAGI corpus 読込と registry 生成
2. project alias / related company / page linkage 生成
3. owner registry / owner filter 生成
4. action pattern registry 生成
5. project segmentation first 実装
6. task rewrite / decomposition 実装
7. minutes golden set / missed set 作成
   - `/Users/shu/OpenClaw/scripts/roby-minutes-eval-corpus.py` で local corpus を生成する
   - 生成物は機密保護のため `~/.openclaw/roby/minutes_golden_set.json` / `minutes_missed_set.json` に保存する

### 4.4 主な成果物

- project registry
- owner registry
- action pattern registry
- minutes eval corpus

### 4.5 初期実装の入口

- 初期の TOKIWAGI_MASTER registry 構築は `scripts/roby-tokiwagi-master-registry.py` を使う
- 対象DB:
  - `TOKIWAGIインナー議事録`
  - `基礎情報`
- 方針:
  - Notion 全文はローカルで読み込む
  - project / owner / action pattern の候補を構造化する
  - Ollama は project ごとの evidence を要約・補助知識化する
  - Gemini はこの段階では使わず、必要時のみ budget gate を通して投入する
- `scripts/roby-minutes.py` では、この registry を使って以下を先行実装する
  - project alias の正規化
  - project segmentation first
  - registry hints を local preprocess / Gemini review / Gemini extraction prompt に注入
  - task rewrite / decomposition（generic なメモ題名の書き換え、note 内複数アクションの分解）

### 4.6 B7 実装メモ

- `/Users/shu/OpenClaw/scripts/roby-minutes-eval-corpus.py` で生成した `minutes_golden_set.json` を元に、`/Users/shu/OpenClaw/scripts/roby-minutes-golden-curate.py` で代表ケースを curated set 化する
- 生成物はローカル state に保存する
  - `~/.openclaw/roby/minutes_golden_curated.json`
  - `~/.openclaw/roby/minutes_golden_curated_summary.json`
- 選定方針:
  - project の偏りを抑える
  - source_doc_title の偏りを抑える
  - eval 用の固定代表ケースとして使う

---

## 5. Sprint C: Eval Sprint

### 5.1 目的

フィードバックだけに依存せず、精度改善の進み具合を客観的に測定できるようにする。

### 5.2 完成条件

- Gmail / Minutes の golden set が存在する
- false negative 回収の仕組みがある
- precision / recall / task usefulness を追える
- weekly report で傾向が見える

### 5.3 実装タスク

1. Gmail golden set を代表ケースに curated する
2. Minutes golden set を代表ケースに curated する
3. false negative（漏れ）をあとから追記できる capture flow を作る
4. precision / recall / usefulness を算出する
5. weekly report / dashboard に傾向を出す

### 5.4 C1 実装メモ

- Gmail golden set の代表ケース化は `/Users/shu/OpenClaw/scripts/roby-gmail-golden-curate.py` で行う
- 入力:
  - `~/.openclaw/roby/gmail_golden_set.json`
- 出力:
  - `~/.openclaw/roby/gmail_golden_curated.json`
  - `~/.openclaw/roby/gmail_golden_curated_summary.json`
- 目的:
  - sender / task_type / source_doc_title が偏りすぎない eval 用の代表セットを作る
  - 後続の precision 計測で毎回同じケースを見られるようにする
- 運用:
  - GitHub へは保存せず local state のみ
  - `--max-items` で代表件数を調整する（初期値 40）

### 5.5 C3 実装メモ

- Gmail false negative の回収は `/Users/shu/OpenClaw/scripts/roby-gmail-missed-capture.py` を使う
- 出力:
  - `~/.openclaw/roby/gmail_missed_manual.jsonl`
- 想定用途:
  - 「本来はタスク化すべきだったが、PBS が拾わなかったメール」を手動登録する
  - 例:
    - 返信すべきだった
    - 契約 / 請求 / 見積として review 以上に上げるべきだった
- `roby-gmail-eval-corpus.py` は manual missed を取り込み、`gmail_missed_set.json` に統合する
- これにより、Neuronic 側の bad/good だけでは拾えない false negative を後から育てられる

### 5.6 C4 実装メモ

- precision / recall / usefulness の算出は `/Users/shu/OpenClaw/scripts/roby-precision-metrics.py` を使う
- 入力:
  - `~/.openclaw/roby/gmail_eval_corpus_summary.json`
  - `~/.openclaw/roby/minutes_eval_corpus_summary.json`
  - `~/.openclaw/roby/gmail_golden_curated_summary.json`
  - `~/.openclaw/roby/minutes_golden_curated_summary.json`
- 出力:
  - `~/.openclaw/roby/precision_metrics_latest.json`
  - `~/.openclaw/roby/precision_metrics_runs.jsonl`
- 指標:
  - `precision = good / (good + bad)`
  - `recall = good / (good + missed)`
  - `usefulness = good / (good + bad + missed)`
  - `review_coverage = (good + bad + missed) / reviewed_items`
  - `curated_coverage = curated_items / curated_source_items`
- 補足:
  - `missed = 0` の場合、`recall = 1.0` でも false negative が十分観測できていない可能性がある
  - そのため出力には `recall_provisional` を含め、暫定値であることを明示する

### 5.7 C5 実装メモ

- precision metrics の最新値は `/Users/shu/OpenClaw/scripts/roby-precision-metrics.py` が生成する
- 最新値は local state のみで保持する
  - `~/.openclaw/roby/precision_metrics_latest.json`
- `roby-weekly-report.py` はこの latest を読み込んで週次集計へ含める
- `roby_ops_notifications.py` は Slack 通知で precision / usefulness / review coverage を表示する
- `roby.status` は最新 precision metrics を返し、Roby ダッシュボードで可視化する
- ダッシュボードでは以下を表示する
  - overall precision / usefulness / review coverage
  - Gmail precision と主な Bad 理由
  - Minutes precision と主な Bad 理由
  - recall は `recall_provisional` の場合、暫定であることを明示する

### 5.8 C6. Precision Eval Job

- curated corpus と precision metrics を読み、`ok / attention / fail / insufficient` を判定する `roby-precision-eval.py` を追加。
- `precision_eval_latest.json` と `precision_eval_runs.jsonl` をローカル保存し、immutable audit に `precision.eval` を記録。
- weekly report / Slack / Roby ダッシュボードへ `Precision Eval` を反映し、overall / Gmail / Minutes の評価状態と issue を確認できるようにする。

1. Gmail golden set
2. Minutes golden set
3. false negative capture flow
4. Precision / Recall / Task usefulness 指標
5. weekly report / dashboard 反映

---

## 6. 実装順序

1. Sprint A-1〜A-3（メールの入力判定強化）
2. Sprint B-1〜B-4（議事録の仕事理解基盤）
3. Sprint A-4〜A-6（メール task gate と評価）
4. Sprint B-5〜B-7（議事録本抽出改善と評価）
5. Sprint C 全体（評価基盤の完成）

---

## 7. 開発ルール

- 追加機能の新規開発は原則ステイ
- 精度向上に寄与しない改修は後回し
- 精度改善は「物量を読む -> 構造化知識を作る -> 本番判定へ使う」の順で進める
- ローカル主軸を維持し、Gemini は高難度ケースの最終判断に限定する

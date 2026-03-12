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

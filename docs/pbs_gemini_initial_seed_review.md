# PBS Gemini Initial Seed Review

更新日: 2026-03-16
元出力: `/Users/shu/.openclaw/roby/pbs_gemini_initial_seed_result_20260316.md`
レビュー方針: 初回投入の返答をそのまま仕様化せず、PBS に取り込む判断軸だけを採用・保留・不採用に分ける。

---

## 1. 総評

今回の初回投入は、PBS の仕事理解を整理する第一段として有用だった。
特に以下は価値が高い。

- project を「施設・店舗システム / データ分析 / 広告配信 / 社内運用」に大別する視点
- owner 判定を「自分が次の action を起こす必要があるか」で見る視点
- minutes の曖昧候補について、question 形式や broad title を `rewrite` して扱う方針
- parent / subtask をワークフロー単位で見る視点

一方で、そのまま採用するとズレる点もある。
特に以下は要注意。

- AWS 成功通知を `task` 寄りに見る判断
- `BT振興会-Mooovi` と他 BT 振興会案件の境界理解がまだ粗い
- 一部の rewrite が実務より一般化しすぎている

---

## 2. 採用する判断軸

### 2.1 Overall Understanding

採用:

- ユーザーは「翻訳者 / 調整役 / PM 的役割」を担っている
- task 判定の絶対基準は「自分が次に action を起こす必要があるか」
- 他者 task でも、自分が確認・依頼・調整を担うなら自分 task にしてよい

### 2.2 Project Boundaries

採用:

- ボーネルンド
  - `スマレジ`, `OBIC`, `DIPRO`, `POS` は強い project signal
- ミッド・ガーデン・ジャパン
  - `MID`, `堀之内店`, `Liny`, `Synergy`, `AI店長` は強い signal
- 瑞鳳社ーデータ分析
  - `Yellowfin`, `インサイト機能`, `Mapbox` は強い signal
- SNW様-第三者広告配信
  - `DSP`, `IDFA`, `くふうジオデータ`, `一広` は強い signal

採用するが補足が必要:

- BT 振興会系は 1 project ではなく、継続案件と単発案件を分けて扱う
- `BT振興会-Mooovi` と `BT振興会-チケットショップ` を別 project として扱う
- 今後も BT 振興会系の単発案件が増える前提で、`BT振興会-*` の階層を許容する

### 2.3 Owner Boundaries

採用:

- 自分 task にしてよい条件
  - 自分が資料作成・設定・確認・依頼・調整を行う
- 自分 task にしない条件
  - 他者が実行主体で、自分の next action がない
- 例外
  - 他者 task を受けて自分が依頼・整理・返信するなら自分 task でよい

### 2.4 Minutes Judgment Policy

採用:

- task にしてよい議事録メモ
  - 動詞があり、自分が関与する action
- review に留めるべきメモ
  - 現状分析、課題感、方向性だけのもの
- section 文脈を最優先に project 判定する方針
- question 形式でも、調査・確認・判断の action に落ちるなら task 候補にする
- broad / generic title は rewrite 前提で扱う
- parent / subtask は工程が複数あるときに分ける

### 2.5 Candidate Adjudication Guide

採用:

- keep
  - 期限あり
  - 自分 action が明確
  - 実務直結
- drop
  - 他者 task
  - 背景共有のみ
  - 完了済み報告のみ
- rewrite
  - 名詞止め
  - 対象が抜けた generic action
- parent 化
  - 複数工程を含むワークフロー型候補

---

## 3. 保留して人間補正して使うもの

### 3.1 Email Policy

保留:

- `AWS PIPELINE 成功通知` を task 化すべきという判断

理由:

- 現在の人間判断では「成功通知は不要、エラーのみ確認」で運用している
- Gmail 側は最近の実運用フィードバックの方が信頼できる

扱い:

- Gmail adjudication にそのまま入れず、現行の human-confirmed ルールを優先する

### 3.2 Project Boundary の一部 rewrite

保留:

- BT 振興会系の説明全般

理由:

- Mooovi とチケットショップの関係は、今回の Gemini 返答だけでは粒度が足りない
- 実運用では「継続案件」と「単発案件」の違いが重要

扱い:

- `/Users/shu/OpenClaw/docs/pbs_context_seed.md` を正本として維持し、Gemini の内容は補助知識に留める

### 3.3 Ambiguous Case Rules の個別文面

保留:

- 各ケースの rewrite 文面

理由:

- 方向性は良いが、タイトルが一般化しすぎているものがある
- そのまま task title にすると実務感が薄い可能性がある

扱い:

- case rule の「判定方針」だけ採用し、最終 title は representative case で再確認する

---

## 4. 採用しないもの

- `AWS PIPELINE 成功通知` を task とする方針
- 「Golden set の傾向からそう見える」という理由だけで、最近の human feedback と矛盾する判断
- BT 振興会系を Mooovi 中心で見る単純化

---

## 5. PBS へ反映する次の方針

### 5.1 すぐ反映してよいもの

- minutes adjudication prompt に以下を強化
  - section 文脈優先
  - question は即 drop せず、調査・判断 task に rewrite 可
  - broad title は rewrite 前提
  - 他者 task / 完了報告 / 背景共有のみ drop

### 5.2 人間レビュー後に反映するもの

- BT 振興会系の project hierarchy
- ambiguous case rewrite の具体文面
- email の例外ルール

### 5.3 運用ルール

- `pbs_context_seed.md` を正本とする
- Gemini 初回投入の返答は「判断軸の補助資料」として扱う
- 実装に反映する前に、human-confirmed ルールと矛盾がないか確認する

---

## 6. 一言まとめ

今回の初回投入は成功。
ただし「そのまま採用する仕様書」ではなく、PBS の adjudication を一段賢くするための判断軸メモとして使うのが最適。

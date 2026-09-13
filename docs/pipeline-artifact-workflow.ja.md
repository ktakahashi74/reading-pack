# Pack自体の直接検査による制作と再判定

新規の生成・評価納品には[Pack生成と品質評価の納品](pipeline-delivery.ja.md)を使う。品質評点による自動採否を行わず、生成物と評価をユーザーへ渡す。このページの旧契約による合否・承認処理とは区別する。

`artifact-acceptance-1`では、生成、直接検査、一括修正最大1回、全体再検査、著者確認票までを実行する。無指定の契約は引き続き`legacy-reader-evaluation-1`である。既存runの契約、原資料、判定、承認、予算を保持する。実装はv0.7.0 alpha、未公開である。

| 単位 | 実装した境界 |
|---|---|
| M6 | 共通指示の版、書籍固有差分、実際の配布指示、承認後を想定したdraft方針、採用したローカル配布物 |
| M7 | 工程別の固定予約、一括修正1回、最終工程の予約枠内での全体再検査 |
| M8 | 保存済み候補の別run再判定、旧証拠の不変保存、累計資源の引継ぎ |
| M9 | M1形式の記録、著者への判断事項と判断証拠、現状態の検証、独立した制作実績の集計 |
| M10 | 合成応答による失敗経路の統合検証、旧契約の回帰検証、日英配布例・パッケージ検証 |

M10はソフトウェア検証である。実書籍の合格、著者採択、公開、実モデルの制作実績を意味しない。

## 新規制作

`pipeline recipe --contract-version artifact-acceptance-1`で契約を選ぶ。生成するrecipeの既定値は`general-navigation`、修正最大1回、通信試行1回である。別の対応プロファイルはrecipeで明示する。`auto`は現在、呼出し前に拒否する。手書きrecipeでは`max_rounds`を1または2、`max_attempts`を1にする。Packの長さ、バイト数、引用、生成候補数の既存制約は維持する。

```sh
reading-pack pipeline start book.md --run new-run --recipe recipe.json --prepare-only
reading-pack pipeline plan --run new-run
reading-pack pipeline resume --run new-run
reading-pack pipeline status --run new-run
```

金額・期限を持たない実験用recipeだけ、start・resume・inspectに`--experimental`を付ける。制作全体の金額・期限枠を設定する場合は`operating_envelope`を使う。停止を回避する目的で既存の上限を削除しない。任意の補足資料には既存の`--supplement ROLE PATH`と、`author-data`、`author-qa`、`errata`などの登録済み役割を使う。

資源予約の名称は既存の`preparation`、`generation`、`development`、`repair`、`final`を維持する。新契約の`development`は初回直接検査、`final`は再検査を指す。標準の設問生成・回答・採点は0回である。生成・修正は、候補の独立レビューが分割される最悪時まで予約する。

`artifact_inspection_batch_limit`（既定128）は初回の意味検査batch数と別枠の最終検査容量を固定する。`artifact_adjudication_limit`（既定1）は具体的な未解決疑義に対する追加判定の総数を各検査で制限する。`instruction_context_characters`（既定200000）は指示・配布物を合わせた検査文脈の上限である。これらは検査の資源制約であり、Packの長さ要件を追加するものではない。

batch数や文脈が固定容量を超えれば未完了とする。初回検査を完了してから確認済み欠陥をまとめて修正し、二度目の修正は始めない。助言や未解決疑義は修正の理由にしない。依存関係の完全な解析を仮定せず、修正後は候補全体を再検査する。編集可能な内容欠陥がなければ最終確認票へ進む。指示テンプレートの欠陥と保護された著者資料は、著者または開発者の判断事項に残す。

初回・修正後の候補とファイル一覧は`acceptance/production/`に保存する。修正計画、修正前後のhashと内容差分も別に残す。再開では保存済み応答を検証して再利用し、結果不明・失敗した呼出しの予約を返金扱いにしない。工程別枠と最終工程の時間予約を保つ。provider報告額、予約額、請求額は区別し、receiptがない費用は不明として表示する。

## 指示と配布物

共通描画規則と既定の言語テンプレートに版と内容hashを持たせる。生成とは独立した指示検査へ、候補のテンプレート、実際の描画結果、draft方針が承認された場合の検査用描画、書籍固有方針、原資料の役割を渡す。出典の区別、重要条件、回答範囲、取得手順と取得不能時の対応など、六つの固定基準を確認する。テンプレートのhash一致だけでは検査完了にならない。

ローカル配布物は別の場所で構築し、固定した候補からの描画結果とバイト単位で照合する。SYS指示・Pack境界の欠落、長さ超過、配布物の改変を検出する。`public_base_url`を設定した場合は、既存のWeb配布物の構築・参照検査も行う。`route_results`はローカル検査の結果を示し、`live_retrieval`は`not_run`のままである。配布物の整合は、公開サーバーや特定の対話サービスでの一回操作による取得を証明しない。配備は実行しない。

通常の描画では、承認済みの書籍固有方針だけを有効にする。承認後を想定した描画は検査証拠であり、承認済み配布物ではない。draftの指示を有効化する目的で状態を変更しない。

## 既存候補の再判定

```sh
reading-pack pipeline reassess-artifact --from-run old-run --run reassessment \
  --project old-run/working --recipe artifact-recipe.json --prepare-only
reading-pack pipeline resume --run reassessment
```

対象は旧run内に保存済みの候補とする。新runへ旧証拠をまとめて保存し、`not_run`から始める。原資料の同一性や保存済み構造は再利用できるが、旧基準の意味判定、回答スコア、承認を新しい合格証拠にしない。旧候補は不変である。`restart`による契約変更は引き続き拒否する。

資源枠は旧runと完全一致させ、累計呼出し上限を増やさず、元の期限も維持する。旧費用が不明な状態を0として初期化しない。後継記録を一つに限定し、予算の分岐と旧runからの追加呼出しを防ぐ。この入口では期限を延長しない。新しい支出・時間条件は別途明示した計画で扱い、旧台帳の書換えで解決しない。候補の再判定と、本文からの新規制作実績は区別する。

## 報告と著者判断

M1の[状態・保存契約](reading-pack-artifact-acceptance-contract.ja.md)は維持する。実行時Schemaは同一バイトの配布用コピー`schema/artifact-acceptance-report.schema.json`である。追記式の最終記録を`acceptance/records/`へ保存し、原資料、候補、テンプレート、基準、配布物、資源、著者判断を安全なrun相対パスとhashで結び付ける。原典抜粋は非公開のrun証拠に保存する。

合否は`pass / fail / inconclusive / not_run`、実行と停止理由、モデル診断、著者判断は独立した欄である。途中停止でも確認済み欠陥を保持する。読み取り専用のstatusでも証拠の現状を確認し、改変後に古い`pass`を現状態として表示しない。過去レコードは書き換えない。

著者確認票は`author_approval.review`から参照する非公開JSONである。対象候補のファイル一覧、検査結果、未完了事項、修正差分、判断入力の形式を含む。合格候補は`pending`、不合格・判定未確定の候補は`needs_decision`となる。実際の判断を記録するJSONには、`reviewer`、タイムゾーン付き`decided_at`、`decision`（`approved / rejected / changes_requested`）、`scope`、確認票と一致する`candidate_manifest_sha256`を記入する。

```sh
reading-pack pipeline finalize --run new-run --review author-decision.json
```

この処理は明示した判断を証拠として追記する。不合格を合格へ変えず、draft方針の有効化、承認済み配布物の構築、公開も行わない。変更要求から新しい修正ループを始めない。既存の著者レビュー・release機能では、別の承認要件を維持する。

## 有限の制作実績集計

任意のqualificationコマンドは、新契約用の`schema_version: 2`、`contract_version: artifact-acceptance-1`、固定した`workflow_signature`、正の`repetitions`、有限の`cases`を受け取る。caseの項目は既存の`id`、`book_id`、`input_identity`、`expectation`、`defect_class`、`record_ids`を使う。各試行を最初の呼出し前に登録する。個別Packの合格に、最低冊数や欠陥種数を要求しない。

全登録枠を集計し、未実施も完成率の分母に残す。完成には直接検査の合格と著者確認票を必要とし、回答スコアは使わない。既存候補の再判定を新規制作として登録しない。実モデル実測と表示するにはproviderのreceiptを検証する。合成応答から実測済みとは表示しない。結果は観測記録であり、実行許可証ではない。`pack_acceptance_gate: false`、`qualified: false`を返す。この実装検証では実書籍のモデル試験を追加していない。

## 最小の操作例

次は、設定済みのJSON adapterを使って小規模原稿を一度生成する例である。実在するadapterパス・モデルIDを指定する。`--reader`は新契約では省略でき、内部の未使用欄はjudge設定で埋める。従来契約ではreaderが必要である。

```sh
reading-pack pipeline recipe --output artifact-recipe.json \
  --contract-version artifact-acceptance-1 --experimental --repair-rounds 0 \
  --generator /path/to/generator --generator-model exact-generator-id \
  --judge /path/to/judge --judge-model exact-judge-id
reading-pack pipeline start book.md --run private/new-run --recipe artifact-recipe.json --prepare-only --experimental
reading-pack pipeline plan --run private/new-run
reading-pack pipeline resume --run private/new-run --experimental
reading-pack pipeline status --run private/new-run
```

この実験用例には金額・期限の全体保証がない。金額・期限付き運用では`--experimental`の代わりに`--operating-mode qualification`または`production`を選び、`--max-cost-usd`、`--call-allowance-usd`、`--max-wall-seconds`、`--phase-calls`の全工程割当を明示する。`plan`が要求する最悪時の呼出し数・時間を確保する。比較試験ではqualificationモードを使う。

著者判断JSONの形式は次のとおりである。値は例示であり、実際の著者の判断・日時・確認票の候補hashへ置き換える。エージェントが承認を作らない。

```json
{
  "reviewer": "実際に判断した著者名",
  "decided_at": "2026-09-13T12:00:00+09:00",
  "decision": "approved",
  "scope": "確認票に記載された候補の内容",
  "candidate_manifest_sha256": "確認票の64桁のSHA-256"
}
```

`pipeline finalize --review`の終了コード0と`artifact_completed`は判断記録の成功を示す。`needs_author_decision`も正常な判断待ちである。品質は別欄`acceptance.status`を確認し、承認から公開済み・配布物構築済みとは解釈しない。

## モデル比較

[モデル比較試験](pipeline-model-comparison.ja.md)は、同じ入力・工程で生成モデルだけを変える任意の試験である。通常制作へ試験回数の要件を追加しない。

## 原稿の対象範囲

`pipeline recipe --scope "AGI・第3章のみ（注・付録を除く）"`で対象範囲を固定できる。この値は候補のquality planと配布物のMETAへ同じ内容で渡る。新契約で省略した場合は「提供された原稿の範囲（刊行版全体との一致は未確認）」とし、原稿だけから刊行版全体が揃っているとは推定しない。全版を対象とする場合も、確認した範囲を明示する。既存seedの範囲は保持し、recipeが異なる範囲を指定した場合は拒否する。旧契約の省略時の動作と保存済み候補・判定は変更しない。

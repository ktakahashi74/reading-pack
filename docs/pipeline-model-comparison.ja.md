# 生成モデルの有限比較試験

同じ原稿、工程、品質基準、評価モデル、資源枠を固定し、生成モデルだけを変えて比較する任意の機能である。通常制作や個別Packの合格に比較試験を要求しない。読者向けファイル形式も変えない。

最初は修正なしの条件を使う。修正の効果を調べる場合は、全モデルで最大1回という別の比較を事前登録する。結果に応じて工程、反復、モデルを追加しない。モデル名は能力順序を表さない。モデルID、effortなどの設定とadapter実体を固定する。

## 準備から実行まで

比較は既存の`start --prepare-only`と有限制作実績の記録を利用する。各モデル・入力・反復に別の新規runを用意する。以下は2モデル・1原稿・各1回の操作例であり、モデル品質の実績や推奨予算ではない。原稿、adapter、モデルID、資源枠を実際の試験条件に置き換える。

1. [制作ガイド](pipeline-artifact-workflow.ja.md)の方法で、`artifact-acceptance-1`、`operating_envelope.mode: qualification`のrecipeを2個用意する。`--repair-rounds 0`で修正を外せる。生成モデルと生成adapterの`--model`、各workerの`--audit-dir`以外は同一にする。モデル名を埋め込んだ別スクリプトではなく、同じadapterへモデルIDを渡す。判定モデル・判定設定は共通にする。
2. 各runを準備する。準備と登録はモデルを呼ばない。

```sh
reading-pack pipeline start book.md --run private/a-0 --recipe a.json --prepare-only
reading-pack pipeline start book.md --run private/b-0 --recipe b.json --prepare-only
reading-pack pipeline plan --run private/a-0
reading-pack pipeline plan --run private/b-0
```

3. `comparison-definition.json`を作成する。`run`は絶対パスを記入する。下の金額・時間は形式例であり、実際には各recipeの上限合計と既存累計へ合わせる。過去費用不明を0として記入しない。`basis`へ累計台帳や承認記録の所在を記す。

```json
{
  "schema_version": 1,
  "variants": {"a": "generator-model-a", "b": "generator-model-b"},
  "cases": {"body-only": "book-id"},
  "repetitions": 1,
  "book_budgets": {
    "book-id": {"prior_cost_usd": 20, "max_cost_usd": 100, "basis": "private/budget-ledger.json"}
  },
  "max_cost_usd": 80,
  "max_wall_seconds": 14400,
  "minimum_completion_rate": 0.9,
  "equivalence_margin": 0.05,
  "trials": [
    {"variant": "a", "case": "body-only", "repetition": 0, "run": "/absolute/private/a-0"},
    {"variant": "b", "case": "body-only", "repetition": 0, "run": "/absolute/private/b-0"}
  ]
}
```

4. 全枠を登録し、許可された入力・送信先・費用の範囲で実行する。

```sh
reading-pack pipeline comparison-register --definition comparison-definition.json --output private/comparison
reading-pack pipeline comparison-run --comparison private/comparison --output private/comparison-result.json
reading-pack pipeline comparison-report --comparison private/comparison --output private/comparison-report.json
```

登録は全モデル×全入力×全反復の枠、入力同一性、共通の工程・judge・資源枠を検査する。各trialの最大費用と期限を合計し、比較全体の上限および書籍ごとの過去費用を加えた累計上限と照合する。予算不足は`admitted: false`として保存し、試行を実行しない。予算の`basis`は利用者の申告を記録する欄であり、外部会計台帳を自動取得・認証する機能ではない。

実行順は登録した`trials`の順序で固定する。時間帯の影響を抑えるには、登録時にモデル順を交互にする。同じ比較の再実行は未着手の枠だけを扱い、期限を延長しない。開始済み・失敗・結果不明の枠を自動再送しない。全runは証拠として保存し、集計後も移動・改変しない。

## レポートの読み方

モデルごとに全登録枠を分母とする完成率、資源内完成率、合否の内訳、既知のprovider報告額、不明費用の呼出し数、各試行の経過時間を出す。完成は直接検査の`pass`と著者確認票への到達を意味し、著者採択ではない。資源内完成には、費用が判明し、期限・費用内で終了した証拠も必要である。未着手の費用や時間を測定済みの0と扱わない。

各試行の`inspection`には検査範囲、欠陥、未解決事項を残す。件数は生成項目数や検査分割にも依存するため、単独のモデル順位スコアにしない。`fresh_live_measurement`は保存済みprovider応答の実体まで検証し、合成workerや旧候補の再判定を実モデルによる新規制作として数えない。報告額は請求額ではない。

`comparisons`は、同じ入力・反復の対について、資源内完成率の差と保守的な区間を示す。全モデル対に対する95%同時Hoeffding区間と、絶対達成率の下限を使う。固定入力に条件付けた各試行の独立性を仮定する。サービス障害等による試行間の相関や、未試験の書籍への一般化は扱わない。

全対の比較に必要な実測があり、絶対目標を下限でも満たし、差の区間全体が`±equivalence_margin`以内の場合だけ、その対の`status`を`within_margin`とする。それ以外は`inconclusive`である。各1回の同点、差が有意でない結果、全モデルの低品質は飽和の証拠にしない。

この統計の対象は資源内完成という二値の結果である。文章の有用性や重大な見落としまで同等と証明するものではない。このため`quality_saturation`は`not_established`とする。生成モデル名を伏せた原文との直接照合を合格例にも行い、共通judgeの見落としや評価尺度の粗さを別に確認する。モデル比較結果からPack合否、著者承認、公開を変更しない。

# 非公開レビュー

`candidates review`は、候補と現在の正本レコードを左右に並べ、原資料からその場で取り出した短い根拠を表示する静的HTMLを作る。正本データ、候補の状態、採否は変えない。

```sh
reading-pack candidates review my-pack/.reading-pack/runs/run-001 \
  --source book.pdf --project my-pack --output review-001.html
```

出力先は`my-pack/.reading-pack/reviews/`の直下に限る。ファイルの権限は`0600`、ディレクトリは`0700`となる。外部のパス、入れ子の出力、シンボリックリンク、既存ファイルの上書きは拒否する。HTMLにはスクリプト、外部資源、フォームを入れない。

このHTMLは公開物ではない。原資料の抜粋と候補本文を含むためである。不要になったファイルは利用者が明示的に削除する。

作成前には次の四点を再検査する。

1. 候補記録の完全性。
2. 原資料のファイル名、SHA-256、正規化したテキストのハッシュ。
3. すべての根拠範囲の位置、ハッシュ、候補レコードとの対応。
4. 候補を作った時点の全言語の正本状態。

原資料または正本が変わっていれば、HTMLを作らず停止する。候補を絞る場合は`--id`を候補ごとに指定する。省略すると全件を表示するが、画面から一括採用はできない。各候補には、その状態に応じて一件だけを採用、却下、適用するコマンドを表示する。

```sh
reading-pack candidates review my-pack/.reading-pack/runs/run-001 \
  --source book.pdf --project my-pack \
  --id CAND-0123456789ABCDEF0123 --output claim-review.html
```

本文の抜粋を含まない意味レビューも、構造検査の指摘と並べて表示できる。このレビューは、変更されていない候補処理全体のハッシュへ結び付いている必要がある。採用、却下、その他の変更を記録した後には、新しい意味レビューを作り直す。

```sh
reading-pack candidates review my-pack/.reading-pack/runs/run-001 \
  --source book.pdf --project my-pack \
  --semantic-review my-pack/.reading-pack/semantic/review-001.json
```

## 複数工程を一画面で確認する

`review bundle`は、複数の候補処理を一つの画面へまとめる。章構造、章要約、命題、確実性、人名、用語、参照先、著者Q&Aを項目別に示し、現在の正本と原資料から再取得した根拠を並べる。候補処理ごとに正確な原資料を指定するため、本文と独立した著者Q&A付録を、来歴を混同せず確認できる。

```sh
reading-pack review bundle --project my-pack \
  --artifact my-pack/.reading-pack/runs/content-001 book.pdf \
  --artifact my-pack/.reading-pack/runs/catalog-001 book.pdf \
  --artifact my-pack/.reading-pack/runs/qa-001 appendix-2.org \
  --ledger catalog-001 my-pack/.reading-pack/catalog-001-ledger.json \
  --catalog catalog-001 my-pack/.reading-pack/catalog-001.json \
  --output one-stop-review.html
```

`--ledger RUN_ID FILE`、`--semantic-review RUN_ID FILE`、`--catalog RUN_ID INVENTORY`には、候補記録にある正確な`run_id`を指定する。意味レビューには照合済みの作業台帳が必要となる。索引の抽出台帳を加えると、抽出件数、未解決の人名・用語、章対応の確認状態も表示できる。

入力していない項目は「未生成または未収録」と表示する。原資料にその内容が存在しないと判定するわけではない。

一括画面にも、単一の候補処理と同じ保護を適用する。出力前に、すべての候補記録、原資料、根拠範囲、任意の索引台帳、作業台帳、意味レビュー、現在の正本を再検査する。正本も候補の状態も変更せず、一括採用の機能は持たない。採用または却下は候補IDごとに別に記録する。画面全体を確認しても、著者承認や公開承認にはならない。

根拠の一致が保証するのは、同じ正規化済みの範囲が原資料内にあることだけである。候補が実際にその範囲から支持されるか、限定や不確実性を保っているか、帰属が正しいかは、人が別に判断する。採用後も正本へ入る状態は`draft`である。

## AIによる一次レビュー

人間による一次レビューをAIへ置き換える場合は、`accept`に`--reviewer-type ai`と`--review-artifact`を指定する。記録には本文の抜粋を入れず、正確な候補処理、候補ID、レコードのハッシュ、根拠資料のハッシュ、モデル名、方法、時刻、原資料による支持、意味の忠実さ、範囲と限定の検査結果を候補ごとに残す。古い処理、別の候補、検査の欠落、一括採用は拒否する。

```sh
reading-pack candidates accept my-pack/.reading-pack/runs/run-001 \
  --id CAND-0123456789ABCDEF0123 \
  --reviewer "model-id" --reviewer-type ai \
  --review-artifact my-pack/.reading-pack/ai-review-run-001.json
```

AIによる採用も、`draft`として適用するための一次選別である。著者承認、権利判断、公開条件には代わらない。AIが却下した候補は、同じ形式で`decision=reject`と記録し、`candidates reject`へ渡せる。

Copyright 2026 Koichi Takahashi / 高橋恒一. CC BY 4.0.

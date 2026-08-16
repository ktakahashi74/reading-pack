# 著者レビュー

著者レビューの標準形式は、人間が読んで編集する一つのMarkdownファイルである。編集後のファイル自体が、著者の判断、同意、保留、修正指示の証拠になる。

エージェントは、このレビューを補助できる。全件を検査して例外をまとめ、推奨理由を説明し、著者の指示をチェック欄や修正欄へ記入する。ただし、エージェントの会話や出力は同意の証拠ではない。判断主体は人間であり、最後に人間が編集後Markdownを確認して提出する。

## 1. レビュー用紙を作る

```sh
reading-pack review export \
  --project ./my-pack \
  --output author-review
```

通常の公開前レビューでは、`--release-signoff`を付ける。内容、権利、出版社確認、再構築不能性、品質責任者、版、公開判断を同じ用紙に列挙し、一回の署名で一括適用できる。

```sh
reading-pack review export \
  --project ./my-pack \
  --release-signoff \
  --output final-review
```

測定結果、評価証拠、hash、Schemaは署名の対象に混ぜず、自動検査の前提とする。出版社確認が未決なら、用紙内で`approved`か`not_required`を一度だけ明示する。修正や例外がなければ、人間が行う指示はこの最終署名の一回で済む。先に修正判断が必要な場合だけ限定用紙を一回使い、再評価後に最終署名する。

本書固有方針だけを先に確認する場合は、同じ経路をmodule限定で使う。

```sh
reading-pack review export \
  --project ./my-pack \
  --module policy \
  --output policy-review
```

限定用紙はpolicy recordだけを含み、全Packのプレビューと全体方針を省く。通常の`submitted`、`plan`、`apply`を使うが、全体の`final_signoff`は与えられない。承認したpolicyは次回buildから運用規則になり、未承認policyは表示対象のまま運用には使われない。

一件だけ判断するときは、全moduleを表示せずrecord IDで絞る。日英projectでは同じIDの両言語が入る。

```sh
reading-pack review export \
  --project ./my-pack \
  --record TERM-06-004 \
  --output singleton-review
```

根拠検査済みの日英候補をそのまま推奨修正として載せる場合は、候補runを繰り返し指定する。

```sh
reading-pack review export \
  --project ./my-pack \
  --candidate-run ./.reading-pack/runs/term-ja \
  --candidate-run ./.reading-pack/runs/term-en \
  --output term-review
```

候補の対象recordだけを含む未提出用紙が作られ、変更fieldは`revise_approve`として修正欄へ事前記入される。候補は現在の正本hashと一致し、機械検査に合格し、原言語を変える場合は全言語の候補がそろっていなければならない。

この操作は二つのものを作る。

- `PROJECT/.reading-pack/reviews/author-review.review.md`: 人間が編集して返すレビュー用紙
- `PROJECT/.reading-pack/reviews/author-review/manifest.json`: 検査と適用に使う、本文を含まない非公開証拠

レビュー用紙には、対象、根拠群、個別例外、全体方針、修正欄、読者向けプレビュー、提出欄が一つの流れで入る。エージェント向けの支援手順は末尾の非表示コメントに置く。用紙にはreview IDとセッションのSHA-256だけを記録し、完全なセッションは非公開証拠と現在の正本から再構成して照合する。Base64や完全な機械データを人間向け用紙へ埋め込まない。

レビュー対象の内容を含む用紙は非公開に保つ。`manifest.json`もプロジェクト状態へ結び付いた内部記録なので、用紙と一緒に管理する。

## 2. 人間が判断する

編集するのは`RP_RESPONSE`または`RP_OVERRIDES`で囲まれた欄だけである。選択肢を一つ選ぶときは、`[ ]`を`[x]`へ変える。

著者提供資料と現在の内容が一致し、提供主体と原資料が記録されている項目は、根拠群としてまとめて判断できる。生成物、修正後の項目、来歴の不一致は個別判断欄へ出る。

全体方針には推奨と理由が表示される。権利、公式性、言語版の位置づけなど、資料だけでは確定できない項目には「本人判断を含む」と表示される。設定上の未決事項は承認として扱わない。たとえば`pack_license = "rights-holder decision pending"`なら、権利項目の推奨は`needs_work`になる。

## 3. エージェントに補助してもらう

レビュー用紙をエージェントへ渡し、「全件を検査し、例外と本人判断事項だけ説明して」と頼める。エージェントは、埋め込まれた指示に従って次を行う。

1. 正本、来歴、翻訳、プレビューを検査する。
2. 数百件を順に質問せず、同種の不足をまとめる。
3. 根拠群、個別例外、全体方針ごとに推奨を説明する。
4. 著者から明示的に頼まれた場合だけ、回答欄を編集する。
5. 編集後ファイルを著者へ見せ、確認を求める。

エージェントは、推奨だけを理由にチェックを入れない。`submitted`と`final_signoff`は、このファイルを自分の判断として提出すると著者が明示した場合だけ記入する。`release_approve`も、列挙された公開判断を一括承認すると人間が明示した場合だけ記入する。例外がなければ、一回の明示指示で三つを同時に記入してよい。

## 4. 修正指示を書く

修正、除外、保留が必要な項目は、「個別の修正・上書き」欄へ書く。書式は用紙内に例示される。

```markdown
### ARU-000123
- `decision`: `revise_approve`
- `comment`: 修正理由
#### `summary`
- `operation`: `set`
<!-- RP_VALUE_START -->
新しい内容
<!-- RP_VALUE_END -->
```

人間が普通の言葉で修正内容を伝え、エージェントにこの書式へ整理させてもよい。編集可能なフィールドか、配列か、翻訳側にも判断が必要かは、計画前に機械検査される。`revise_approve`は署名された同じMarkdownに表示された修正を適用し、その内容を`approved`にする。修正後を別の回で確認したい場合は従来の`revise`を使い、適用後を`draft`にする。

## 5. 提出する

最後に次を記入する。

- `reviewer`: 確認者名
- `reviewed_at`: 確認日
- `submitted`: この編集後ファイルを自分の判断として提出する
- `final_signoff`: 全レコードと必須方針の最終承認

`--release-signoff`付き用紙では、さらに`release_approve`または`release_hold`を選ぶ。公開承認には`submitted`と`final_signoff`も必要である。

途中結果や修正指示だけを提出する場合も`submitted`は必要である。record限定用紙とmodule限定用紙は全体の`final_signoff`を与えない。完全な用紙での`final_signoff`は、全レコードが`approve`、`revise_approve`または`exclude`で、必須方針が`accept`となり、未承認の修正、保留、未判断がない場合だけ有効になる。

見出し、説明、項目一覧、プレビュー、HTMLコメント、回答枠の境界を変えると、保護ハッシュの検査に失敗する。これにより、編集後ファイルがどの対象へ回答したものかを固定する。

## 6. 検査し、適用する

```sh
reading-pack review status ./author-review.review.md \
  --evidence ./my-pack/.reading-pack/reviews/author-review \
  --project ./my-pack

reading-pack review plan ./author-review.review.md \
  --evidence ./my-pack/.reading-pack/reviews/author-review \
  --project ./my-pack \
  --output ./author-review-plan.json

reading-pack review apply ./author-review-plan.json \
  --review ./author-review.review.md \
  --evidence ./my-pack/.reading-pack/reviews/author-review \
  --project ./my-pack
```

根拠群の判断は、計画作成前に全レコードの明示判断へ展開される。計画と履歴には、一件ごとのID、判断、before/after hashが残る。正本、設定、品質計画、template、AIP台帳、レビュー状態のいずれかが変われば、古い用紙は拒否される。

通常の用紙で最終署名すると、`reading-pack.toml.workflow.author_review`だけが`approved`になる。`--release-signoff`付き用紙で公開承認した場合は、列挙されたworkflow gateと品質責任者判断も、同じ回復可能なtransactionで適用する。現在の評価証拠が欠ける、実測値が下限を満たさない、hashが古い、出版社判断がない、またはrelease checkに失敗する場合は一件も適用しない。

Copyright 2026 Koichi Takahashi / 高橋恒一. CC BY 4.0.

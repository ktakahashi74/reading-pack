# クイックスタート

新規の生成・評価納品には[Pack生成と品質評価の納品](pipeline-delivery.ja.md)を使う。品質評点による自動採否を行わず、生成物と評価をユーザーへ渡す。このページの旧契約による合否・承認処理とは区別する。

`artifact-acceptance-1`は、内容・指示・ローカル配布物の直接検査、固定資源枠、一括修正最大1回、既存候補の別再判定、候補hashに結び付いた著者確認票までを実行する。標準の設問生成・回答・採点は0回である。無指定は`legacy-reader-evaluation-1`を維持する。 [直接検査による制作](pipeline-artifact-workflow.ja.md)。[モデル比較試験](pipeline-model-comparison.ja.md)は任意の開発・運用試験であり、個別Packの合格条件ではない。

以下の設問・保留試験を用いる自動制作の説明は従来契約を対象とする。個別の制作・レビューコマンドも引き続き利用できる。

新規runでは、設問・回答の生成前に読解支援の合格範囲を固定する。生成された設問の細部を、Packの必須採録事項へ自動変換しない。中心的な意味、重要な条件、正しい帰属は必須とし、付随的な数値は、情報の限界を明示してPack内にある適切で具体的な所在を案内する対応も認める。一般的な丸投げや、書かれた内容の誤りは認めない。既存試験の再開では元の範囲と結果を保持し、基準を変える場合は別の再評価として識別する。これは候補全体の再生成を要求するものではない。

自動ワークフローは実測適格性のないalphaである。従来契約のproductionモードには、工程全体の時間・費用枠と実測済みのワークフロー証明を必要とする。従来の研究用コマンドは明示的な`--experimental`を必要とする。制御試験の成功は実書籍の完成実績ではない。計画、実測、現在の限界は[時間・費用・品質の予測可能性](predictable-production.ja.md)にまとめる。

本文を入力すると、`reading-pack pipeline start`が構造抽出、内容生成、品質評価、上限付きの自動修正を実行し、合格した候補を著者確認票へまとめる。著者が用意した付録なども任意で追加できる。 参考文献にURLがなくても、本文中の書誌情報を利用できる。章が処理区間をまたぐ場合、審査に使う原文を上限付きで前後へ広げる。設定と実行方法は[自動制作ガイド](automatic-pipeline.ja.md)を参照。

生成前に、必須情報の範囲を固定する。監査の指摘は、Pack全体と、登録済み付録を含む上限付きの原文で独立に再判定する。任意の改善案は別に保存し、修正や読解試験の停止を起こさない。確認された意味上の誤りと、根拠の未解決は引き続き合格を妨げる。

新規の設問は、短い読解支援Packに必要な情報かを審査してから要件を固定する。再開時は固定済み設問を変更しない。監査で未確定の指摘には、各ラウンドで一度、上限付きの原文検索と再判定を行う。確認済みの出典に限定した表現へ修正できる場合は、確定不具合の修正とともに独立審査・再監査へ進める。未確定のまま合格にはせず、既存のラウンド・予算上限を維持する。重複する指摘は理由と根拠を失わず統合する。

以下は個別コマンドで作業を確認・実行するための手順である。通常の自動制作では、各段階を人が順に起動する必要はない。 著者提供記録は、ユーザーが指定記録の未承認改訂を明示的に許可した場合を除き保護する。指定方法は自動制作ガイドを参照。

実装更新後の復旧では、`pipeline restart`で完了ラウンドまたは停止中の下書きを保持できる。明示的な指定方法、検査、残予算の設定は自動制作ガイドで説明する。下書きの保持は品質合格を意味しない。

この手順は、新しいディレクトリから始めて、通信を使わずに技術的整合性のある下書きパックを作る。公開には、この後で人間による権利確認と内容承認が必要になる。

## 1. ローカルの取得物をインストールする

リポジトリの直下で実行する。

```sh
python3 -m pip install --no-deps --no-build-isolation \
  --target .reading-pack-site .
export PYTHONPATH="$PWD/.reading-pack-site"
export PATH="$PWD/.reading-pack-site/bin:$PATH"
reading-pack doctor --project examples/clockwork-garden
```

## 2. プロジェクトを作る

```sh
mkdir -p /tmp/reading-pack-quickstart
cd /tmp/reading-pack-quickstart

reading-pack init demo \
  --title "歯車仕掛けの庭" \
  --author "Mira Aoki" \
  --lang ja \
  --profile nonfiction-reading
```

`reading-pack profiles`で七つの品質プロファイルを確認できる。`init`は空でない作成先を拒否し、`reading-pack.toml`、`quality-plan.json`、`sources.json`、`author-input-state.json`、言語別のデータとテンプレート、工程用のディレクトリを作る。責任者と公開上の必須条件は、未承認の状態から始まる。

## 3. 章構造を取り込む

UTF-8のMarkdown、Org mode、EPUB3、PDF、プレーンテキストのいずれかを`demo/manuscripts/`へ置く。PDFには、ローカルのPopplerコマンド`pdfinfo`と`pdftotext`も必要となる。縦書きPDFのレイアウト復元には`pdftohtml`も使う。

```sh
reading-pack import-plan demo/manuscripts/book.org \
  --output /tmp/demo-import-plan.json

# 計画を確認した後で正本へ反映する。
reading-pack import-apply /tmp/demo-import-plan.json \
  --source demo/manuscripts/book.org --project demo --lang ja
```

最初のコマンドは正本を変更しない。計画に入るのは、原資料の識別情報、階層、所在、抽出確度、来歴、診断結果であり、本文は入らない。次のコマンドは、対応関係が一意に決まる場合だけ既存IDと編集済みの項目を保つ。従来の`import`は互換用として残している。

## 4. 正本データを編集し、確認する

著者または出版社から章、要約、人名、用語、Q&A、参考文献が提供される場合は、Author Input Packageを使う。`reading-pack author-input template`で雛形を作り、全項目の設定を確認してから`author-input plan`と`author-input apply`を実行する。提供、補完、自動生成、意図的省略のどれを選んだかを記録するが、提供データに書かれた承認状態は引き継がない。詳しくは[Author Input Package](author-input.ja.md)にある。

手作業で進める場合は`demo/data/pack.ja.json`を開き、著者が確認した短い章要約と、必要な項目を加える。

- `certainty`：著者が定める証拠の種類。
- `claims`：記述／規範の区分と、反証条件または再検討条件を持つ命題。
- `misreadings`：よくある誤読と、単独で意味が通る訂正。
- `names`と`glossary`：人名と用語の所在案内。推測した定義は加えない。
- `references`：公式のHTTP(S)資料。

各レコードは`draft`から始まる。`prompts/`を使ってAIに候補を作らせてもよいが、AIの出力をそのまま承認済みにはできない。候補は次の順に扱う。

```sh
reading-pack candidates create /tmp/responses.json \
  --run-directory demo/.reading-pack/runs/run-001 \
  --source demo/manuscripts/book.org --project demo --lang ja
reading-pack candidates report demo/.reading-pack/runs/run-001
reading-pack candidates verify demo/.reading-pack/runs/run-001 \
  --source demo/manuscripts/book.org

# 内容を確認してから、人間の採用判断を記録する。
reading-pack candidates accept demo/.reading-pack/runs/run-001 \
  --id CANDIDATE_ID --reviewer "編集者名"
reading-pack candidates apply demo/.reading-pack/runs/run-001 \
  --source demo/manuscripts/book.org --project demo --lang ja \
  --id CANDIDATE_ID
```

応答JSONは非公開で管理する。PDFとEPUBの照合用テキストは、指定した原資料から内部で導出する。確定した処理記録には本文の抜粋を残さない。

根拠の範囲が原資料内に存在しても、候補の解釈が正しいとは限らない。候補の採用で許されるのは`draft`としての適用までであり、著者の最終承認は後の工程で行う。詳しくは[品質保証](quality-pipeline.ja.md)を参照してほしい。

## 5. 検証し、生成する

```sh
reading-pack validate --project demo
reading-pack build --project demo --lang ja
reading-pack check --project demo --lang ja
```

同じ入力から同じファイルができることは、次の手順でも確認できる。

```sh
cp demo/dist/reading-pack.ja.md /tmp/first-pack.md
reading-pack build --project demo --lang ja
cmp /tmp/first-pack.md demo/dist/reading-pack.ja.md
```

`check`は同じバイト比較を内部で行う。`dist/`を手編集すると終了コード5で失敗する。

## 6. 公開条件を満たす

公開前には、第一に権利、第二に内容と再構築不能性、第三に公開の可否を人が判断する。実測評価と証拠を用意したうえで、次の公開判断付きレビューを使えば、各レコード、`[workflow]`、品質責任者、必須方針を一回の署名から原子的に更新できる。

著者の内容確認には、現在の正本に結び付いた一つのMarkdownレビューを使う。エージェントは全件を検査して例外と本人判断事項を説明し、記入を補助できる。編集後Markdownそのものが、人間の判断と修正指示の証拠になる。

```sh
reading-pack review export --project demo --release-signoff --output author-review
# author-review.review.mdを読み、必要ならエージェントに説明と記入を頼む
reading-pack review status /path/to/author-review.review.md \
  --evidence demo/.reading-pack/reviews/author-review --project demo
reading-pack review plan /path/to/author-review.review.md \
  --evidence demo/.reading-pack/reviews/author-review --project demo \
  --output /tmp/author-review-plan.json
reading-pack review apply /tmp/author-review-plan.json \
  --review /path/to/author-review.review.md \
  --evidence demo/.reading-pack/reviews/author-review --project demo
```

例外がなければ、人間はエージェントの説明を確認して一回だけ最終承認を指示する。修正が必要なら、限定用紙でその判断を適用し、再評価後に新しい公開判断付き用紙へ署名する。レビュー用紙の編集、エージェントによる補助、修正指示は[著者レビュー](author-review.ja.md)を参照する。

既存パックを置き換える場合は、`reading-pack measure --json`の実測値と比較資料のSHA-256を`content_floor`へ記録する。章構造の適合率と再現率、捏造したレコード数、帰属の誤りも、評価資料のパスとハッシュ、現在の正本データのハッシュへ結び付ける。`publisher_review = "not_required"`は、人が契約と事情を確認して出版社承認が不要と判断した場合に限って使う。

```sh
reading-pack check --project demo --lang ja --release
```

公開上の必須条件、実測した品質条件、公開レコードの承認がそろうまで、この検査は失敗する。その後に正本または品質条件を変えると、以前のレビューは古いものになる。モデル評価のID、設定、実施日、事前に決めた判定基準は`evaluation/`へ保存する。原稿本文、認証情報、秘密に管理する攻撃手順はコミットしない。

## 日英版へ広げる

`--lang ja --lang en --primary-language ja`で初期化し、日本語原稿、英訳原稿の順に取り込む。同じ位置の章には共通IDが入り、英語レコードには対応する日本語レコードの意味内容ハッシュが記録される。

`RP202`が出た場合は翻訳を直し、`link-translations --lang en`を実行する。この操作は対象の翻訳を`draft`へ戻す。公開前に人が再確認して承認する。

Copyright 2026 Koichi Takahashi / 高橋恒一. CC BY 4.0.

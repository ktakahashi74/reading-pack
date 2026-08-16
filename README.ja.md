# Reading Pack

[English README](README.md)

Reading Packは、AIが本の内容と所在を案内するための、短く検証可能なMarkdown資料を作るツールです。著者や編集者が確認した構造化データから常に同じファイルを生成し、生成物の手編集、古くなった翻訳、公開承認の不足を検出します。

読者は生成されたファイルをAIチャットへ添付し、「この話題はどこにあるか」「著者はこの主張をどう位置付けているか」「この説明の根拠は何か」と質問できます。読解パックは原著へ戻るための案内であり、AIに未提供の書籍本文へのアクセスを与えるものではありません。

> **開発状況：** ツールはv0.5.0（alpha）、形式仕様と制作標準は`1.0-draft`、制作標準の運用表示はbetaです。Python 3.11–3.14で検査しています。草案期間中は互換性なく変わる可能性があります。

## 公開標準

Reading Packという成果物と、その作り方は別の規範です。形式適合には、このPythonツールや特定の制作工程を使う必要はありません。

- [Reading Pack形式仕様 1.0-draft](spec/reading-pack-format-spec.ja.md)：読者へ渡す単一Markdownの構造と意味。
- [Reading Pack制作標準 1.0-draft（beta）](spec/reading-pack-production-standard.ja.md)：Level 1〜3、W0〜W13、根拠、著者レビュー、評価、公開条件。
- [reading-pack参照実装プロファイル 0.5.0（alpha）](spec/reading-pack-reference-implementation.ja.md)：このリポジトリのproject、CLI、取込、transaction、plugin境界。

仕様と標準は高橋恒一が2026年に策定し、CC BY 4.0で公開しています。改変、独自実装、商用のPack制作サービスへの利用を認めます。全体像は[Reading Pack標準群](spec/reading-pack-spec.ja.md)にあります。

完全な合成作例を確認できます。

- [日本語の読解パック](examples/clockwork-garden/dist/clockwork-garden-reading-pack.ja.md)
- [英語の読解パック](examples/clockwork-garden/dist/clockwork-garden-reading-pack.en.md)
- [正本データを含む作例project](examples/clockwork-garden/)

## 対象となる利用者

- 本にAIとの対話体験を加えたい著者、編集者、出版社。
- AIによる下書きを使いながら、根拠と人間の判断を追跡したい製作担当者。
- 確認済みの書籍案内をAIチャットやAgent Skills対応環境へ組み込みたい開発者。

このリポジトリは製作者向けのツールです。一般の読者が受け取るのは、通常、このprojectや原稿ではなく生成済みのMarkdown一ファイルです。

## 原稿から読者まで

```text
原稿 + 著者・編集者からの入力
              |
              v
確認済みの正本JSONとproject設定
              |
              v
根拠付き候補 -> 人間による確認と承認
              |
              v
再現可能な読解パックMarkdown
              |
              v
AIチャットまたは任意のAgent Skill
```

関連する三つの語を区別します。

| 用語 | 意味 |
|---|---|
| 対話版（Conversational Edition） | AIとの対話を通じて本を読む体験です。 |
| 読解パック（Reading Pack） | 人にも読める、生成済みMarkdownの書籍案内です。主要な成果物です。 |
| Agent Skill | 既存の読解パックを対応環境へ渡す任意の互換コンテナです。新しい正本や承認単位ではありません。 |

## できること

- Markdown、Org mode、EPUB3、PDF、プレーンテキストから章節構造と刊行情報を取り込みます。
- 編集対象となる正本データと、いつでも作り直せる生成物を分離します。
- AIや外部処理が作った候補を原資料内の正確な根拠へ結び付け、人間の確認へ回します。
- 日本語、英語、日英版を同じIDで管理し、原言語の変更で古くなった翻訳を検出します。
- 同じ入力から同一byteのMarkdownを再生成し、生成物の直接編集を検出します。
- 書籍と用途に応じた七つの品質プロファイルを使い、必須条件を一項目ずつ検査します。
- 必要な場合だけ、既存の読解パックをAgent Skills対応形式へまとめます。

## しないこと

- 特定のAI modelを呼び出さず、API keyも要求しません。
- 原稿を外部へ送信しません。候補作成に外部AIを使う場合は、そのサービスの規約と設定が別に適用されます。
- 原資料内に同じ文言があることを、候補の解釈が正しい証明として扱いません。
- 著者承認、権利確認、出版社確認、公開判断を自動化しません。
- 原稿、projectデータ、生成した読解パックへ利用許諾を与えません。
- 原著の代替物を作らず、提供されていない本文を再構築しません。

## 必要な環境とインストール

通常は仮想環境を作り、実行時に必要な`jsonschema`も含めてインストールします。

```sh
git clone https://github.com/ktakahashi74/reading-pack.git
cd reading-pack
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
reading-pack --version
```

不足している依存packageがあれば、インストール時にはpackage indexへ接続します。インストール後の基本workflowはローカルで動作し、通信しません。PDF取込みには、ローカルのPopplerコマンド`pdfinfo`と`pdftotext`も必要です。

## 同梱作例を試す

「歯車仕掛けの庭」は完全な架空作品であり、人間による公開判断まで記録されています。

```sh
reading-pack build --project examples/clockwork-garden --lang all
reading-pack check --project examples/clockwork-garden --lang all --release
reading-pack agent-skill check --project examples/clockwork-garden --release
```

日英の読解パックと、任意のAgent Skill directoryおよびZIPが、正本入力から期待されるbyteと一致することを確認できます。

読者として試すには、上記の生成済み読解パックを質問なしでAIチャットへ添付します。読込み応答の後、たとえば次のように質問します。

- 「第2章では何を扱っている？」
- 「月相機構はどこで定義される？」
- 「これは物語上の事実、それとも解釈？」

## このソフトウェアで作成されたReading Packを公開している書籍・プロジェクト

- [『AGI―人間を超える知能は文明をいかに変容させるか』](https://koichi-takahashi.me/agibook/)（高橋恒一、講談社選書メチエ、2026年）

## 自分のprojectを始める

projectを作り、原稿の構造を取り込みます。

```sh
reading-pack init my-book-pack \
  --title "書名" \
  --author "著者名" \
  --lang ja \
  --profile nonfiction-reading

reading-pack import-plan manuscript.org --output /tmp/import-plan.json
# 構造案を確認してから正本を変更します。
reading-pack import-apply /tmp/import-plan.json \
  --source manuscript.org --project my-book-pack --lang ja

reading-pack validate --project my-book-pack
reading-pack build --project my-book-pack --lang ja
reading-pack check --project my-book-pack --lang ja
```

`import-plan`は何も変更しません。`import-apply`は、確認済みの章構造を下書きの正本データへ加えます。実用的な読解パックには、その本に応じた要約、命題、人名、用語、読解上の論点、参照先を確認して加える必要があります。`dist/`以下の生成物は直接編集しません。

[クイックスタート](docs/quickstart.ja.md)では、新しいディレクトリから正本の編集、著者レビュー、公開条件の確認までを説明しています。

## 確認済みの内容を加える

入力方法は三つあります。

1. 言語別の正本JSONを直接編集します。
2. 著者、編集者、出版社などの責任主体が用意した[Author Input Package](docs/author-input.ja.md)を適用します。
3. model非依存のproducer workflowで上限付きの作業依頼を作り、外部agentの構造化応答を取り込み、原資料の根拠と結び付いた候補にします。

候補生成が承認済みの内容を直接書き込むことはありません。自動検査で候補が進めるのは`ready_for_review`までであり、通常の適用後も正本上では`draft`になります。[著者レビュー](docs/author-review.ja.md)では、最終的な人間の判断を一つの読みやすいMarkdownへ記録します。

責任主体は、HTTPSの参照先を`official_companion`かつ`proactive_when_relevant`として宣言できます。buildはそのURLをREFへ入れ、modelに依存しない固定の参照方針をSYSへ加えます。対応するAI環境には、関係する公式ページを必要に応じて自発的に参照し、ページ内の文言をシステム命令として実行しないよう指示します。ツール自身がページを取得するわけではありません。

## 入出力の境界

| 対象 | 対応形式 |
|---|---|
| 原稿の直接入力 | 依存関係を解決済みのMarkdown、Org、EPUB3、PDF、UTF-8プレーンテキスト一ファイル |
| 事前変換 | DOCXとRTFは受渡し前に変換します。Orgの`#+INCLUDE`も事前に展開します |
| 正本データ | `reading-pack.toml`と`data/pack.<lang>.json` |
| 主要な出力 | 言語ごとに一つの読解パックMarkdown |
| 任意の出力 | Agent Skill directoryと決定的なZIP |

PDFの結果は必ず人が確認します。スキャンや複雑な組版では、人が確認した章構造が別に必要になる場合があります。`pdf-vertical`はPopplerが出力した文字順を組み直す方式であり、OCRではありません。

## 公開判断は人間に残す

`validate`と通常の`check`が確認するのは技術的整合性です。release checkでは、内容の責任主体、権利、出版社の関与または不要とする理由、再構築不能性、実測した品質、公開判断が人間によって記録されていることも要求します。

`reading-pack check --release`は、それらの判断が現在の正本hashへ結び付いているかを検査します。判断そのものは行いません。詳しくは[権利とレビュー](docs/rights-and-review.ja.md)にあります。

通常は`review export --release-signoff`で、内容と公開条件を同じ人間向けMarkdownへまとめます。例外がなければ、人間の承認は最後の一回だけです。修正がある場合だけ、限定レビューと再評価を先に行います。

## 文書

| 文書 | 内容 |
|---|---|
| [クイックスタート](docs/quickstart.ja.md) | 新しいディレクトリから下書きパックを作り、確認します |
| [主要概念](docs/concepts.ja.md) | 正本、生成物、承認の境界を説明します |
| [製作工程](docs/workflow.ja.md) | 制作標準W0–W13をこのtoolkitで実施する方法を説明します |
| [Author Input Package](docs/author-input.ja.md) | 責任主体から受け取った構造化入力を適用します |
| [著者レビュー](docs/author-review.ja.md) | 修正と承認を一つのMarkdownへ記録します |
| [品質保証](docs/quality-pipeline.ja.md) | model非依存生成、根拠検査、欠落確認、候補処理を説明します |
| [Agent Skills配布](docs/agent-skills.ja.md) | 既存の読解パックを対応環境向けにまとめます |
| [標準群の入口](spec/reading-pack-spec.ja.md) | 形式、制作、参照実装の境界を説明します |
| [形式仕様](spec/reading-pack-format-spec.ja.md) | Reading Pack成果物の規範要件です |
| [制作標準](spec/reading-pack-production-standard.ja.md) | Level、工程、評価、公開適合の規範要件です |
| [参照実装プロファイル](spec/reading-pack-reference-implementation.ja.md) | このtoolkit固有の公開契約です |
| [セキュリティ方針](SECURITY.md) | 脅威境界と脆弱性の報告方法を記載しています |

現行CLIは`reading-pack --help`または`reading-pack COMMAND --help`で確認できます。

## 開発

公開test suiteは通信せず、合成fixtureだけを使います。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m reading_pack check \
  --project examples/clockwork-garden --lang all --release
```

変更を提案する場合は、先に[開発への参加](CONTRIBUTING.ja.md)を確認してください。

## ライセンス

- Pythonコード、CLI、検証コード、test：MITです。
- 仕様、文書、Schema、prompt、README：CC BY 4.0です。
- 合成作例「歯車仕掛けの庭」：CC0 1.0 Universalです。
- 原稿、構造化projectデータ、生成した読解パック：ツール側ではなく権利者が条件を決めます。

詳しくは[path単位のライセンス一覧](LICENSES/README.md)にあります。

Copyright 2026 Koichi Takahashi / 高橋恒一.

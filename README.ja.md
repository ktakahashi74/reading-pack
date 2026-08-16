# Reading Pack

[English README](README.md)

Reading Packは、書籍をAIと読むための短い案内ファイルを作るオープンソース・ツールです。読者は生成されたMarkdownをAIチャットへ添付し、「この話題はどこにあるか」「著者はこの主張をどう位置づけているか」「この説明の根拠は何か」と質問できます。

読解パックの役割は、読者を原著へ戻すことにあります。書籍本文の複製や圧縮版は作りません。著者や編集者が確認した構造化データから毎回同じファイルを生成し、生成物の手編集、古くなった翻訳、公開承認の不足を検出します。

> **開発状況：** ツールはv0.5.0（alpha）です。形式仕様と制作標準は`1.0-draft`で、制作標準はbetaとして運用しています。Python 3.11–3.14で検査しています。草案期間中は互換性のない変更が入る可能性があります。

## 公開標準

Reading Packの公開規範は三層に分かれています。第一が完成したMarkdownの形式、第二が制作上の品質保証、第三がこのリポジトリにある参照実装です。形式に適合するために、このPythonツールや同じ内部実装を採用する必要はありません。

- [Reading Pack形式仕様 1.0-draft](spec/reading-pack-format-spec.ja.md)：読者へ渡す単一Markdownの構造と意味を定めます。
- [Reading Pack制作標準 1.0-draft（beta）](spec/reading-pack-production-standard.ja.md)：Level 1〜3、W0〜W13、根拠、著者レビュー、評価、公開条件を定めます。
- [reading-pack参照実装プロファイル 0.5.0（alpha）](spec/reading-pack-reference-implementation.ja.md)：このツール固有のプロジェクト構成、CLI、取り込み、トランザクション、プラグイン境界を説明します。

形式仕様と制作標準は、高橋恒一が2026年に策定し、CC BY 4.0で公開しています。改変、独自実装、商用の読解パック制作サービスに利用できます。三層の関係と推奨引用は[Reading Pack標準群](spec/reading-pack-spec.ja.md)にまとめました。

## まず試す

架空作品「歯車仕掛けの庭」の完成例を同梱しています。

- [日本語の読解パック](examples/clockwork-garden/dist/clockwork-garden-reading-pack.ja.md)
- [英語の読解パック](examples/clockwork-garden/dist/clockwork-garden-reading-pack.en.md)
- [正本データを含む作例プロジェクト](examples/clockwork-garden/)

生成済みの読解パックを、質問を書かずにAIチャットへ添付してください。読み込みの応答が返ったら、たとえば次のように質問できます。

- 「第2章では何を扱っている？」
- 「月相機構はどこで定義される？」
- 「これは物語上の事実、それとも解釈？」

## 誰のためのツールか

- 本にAIとの対話体験を加えたい著者、編集者、出版社。
- AIで下書きを作りつつ、根拠と人間の判断を追跡したい制作担当者。
- 確認済みの書籍案内をAIチャットやAgent Skills対応環境へ組み込みたい開発者。

このリポジトリは制作者向けです。一般の読者には、通常、生成済みのMarkdown一ファイルを渡します。プロジェクトと原稿は制作者が管理します。

## 原稿から読者へ

「正本」とは、編集の基点となる構造化データと設定を指します。配布用のMarkdownは、正本から何度でも同じ内容で作り直せます。

```text
原稿 + 著者・編集者からの入力
              |
              v
確認済みの正本JSONとプロジェクト設定
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

ここでは、三つの語を使い分けます。

| 用語 | 意味 |
|---|---|
| 対話版（Conversational Edition） | AIとの対話を通じて本を読む体験です。 |
| 読解パック（Reading Pack） | 人にも読める、生成済みMarkdownの書籍案内です。読者へ渡す主要な成果物です。 |
| Agent Skill | 既存の読解パックを対応環境へ渡す任意の容器です。正本や承認単位は増えません。 |

## できること

- Markdown、Org mode、EPUB3、PDF、プレーンテキストから章節構造と刊行情報を取り込みます。
- 正本と、いつでも作り直せる生成物を分離します。
- AIや外部処理が作った候補を原資料内の根拠へ結びつけ、人間の確認へ回します。
- 日本語版と英語版を同じIDで管理し、原言語の変更によって古くなった翻訳を検出します。
- 同じ入力から同じバイト列のMarkdownを再生成し、生成物の直接編集を検出します。
- 書籍と用途に応じた7種類の品質プロファイルで、必須条件を一項目ずつ検査します。
- 必要な場合は、既存の読解パックをAgent Skills対応形式へまとめます。

## ツールが判断しないこと

- 特定のAIモデルを呼び出さず、APIキーも要求しません。
- 原稿を外部へ送信しません。候補作成に外部AIを使う場合は、そのサービスの規約と設定が適用されます。
- 原資料に同じ文言があるという理由だけで、候補の解釈を正しいとは判定しません。
- 著者承認、権利確認、出版社確認、公開判断を自動化しません。
- 原稿、プロジェクトデータ、生成した読解パックに利用許諾を与えません。
- 原著の代替物や、提供されていない本文の再構築物を作りません。

## インストール

仮想環境を作り、実行時に必要な`jsonschema`とともにインストールします。

```sh
git clone https://github.com/ktakahashi74/reading-pack.git
cd reading-pack
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
reading-pack --version
```

依存パッケージが手元にない場合、インストール時にパッケージ索引へ接続します。インストール後の基本機能はローカルで動き、通信しません。PDFの取り込みには、ローカルにあるPopplerの`pdfinfo`と`pdftotext`も使います。

## 同梱作例を再生成する

「歯車仕掛けの庭」は完全な架空作品で、人間による公開判断まで記録済みです。

```sh
reading-pack build --project examples/clockwork-garden --lang all
reading-pack check --project examples/clockwork-garden --lang all --release
reading-pack agent-skill check --project examples/clockwork-garden --release
```

上のコマンドは、日英の読解パック、任意のAgent Skillディレクトリ、ZIPが、正本から再生成した結果とバイト単位で一致するかを検査します。

## 公開例

- [『AGI―人間を超える知能は文明をいかに変容させるか』](https://koichi-takahashi.me/agibook/)（高橋恒一、講談社選書メチエ、2026年）

## 関連プロジェクト

- [Reading Pack Bot](https://github.com/ktakahashi74/reading-pack-bot)は、確認済みのReading PackをSlackまたはDiscord上の対話サービスとして公開する、任意のサーバー実装です。アルファ版であり、Reading Packの作成や利用に必須ではありません。

## 自分のプロジェクトを始める

まず、プロジェクトを作り、原稿の章節構造を取り込みます。

```sh
reading-pack init my-book-pack \
  --title "書名" \
  --author "著者名" \
  --lang ja \
  --profile nonfiction-reading

reading-pack import-plan manuscript.org --output /tmp/import-plan.json
# 構造案を確認してから正本へ反映します。
reading-pack import-apply /tmp/import-plan.json \
  --source manuscript.org --project my-book-pack --lang ja

reading-pack validate --project my-book-pack
reading-pack build --project my-book-pack --lang ja
reading-pack check --project my-book-pack --lang ja
```

`import-plan`は原稿と正本を変更せず、章節構造の案だけを作ります。確認後に`import-apply`を実行すると、その構造が下書きとして正本へ入ります。

章節構造だけでは、実用的な読解パックにはなりません。その本に必要な要約、命題、人名、用語、読解上の論点、参照先を選び、内容を確認して加えます。`dist/`以下の生成物は直接編集せず、正本を直して再生成します。

[クイックスタート](docs/quickstart.ja.md)では、新しいディレクトリから正本の編集、著者レビュー、公開条件の確認までを順に説明しています。

## 確認済みの内容を加える

内容を正本へ入れる経路は三つあります。

1. 言語別の正本JSONを直接編集する。
2. 著者、編集者、出版社などの責任主体が用意した[Author Input Package](docs/author-input.ja.md)を適用する。
3. モデルに依存しない制作工程で小さな作業単位を作り、外部エージェントの構造化応答を原資料の根拠へ結びつける。

候補生成は、承認済みの内容を正本へ直接書き込みません。自動検査で進めるのは`ready_for_review`までで、正本へ適用した後も`draft`です。最終的な人間の判断は、[著者レビュー](docs/author-review.ja.md)の読みやすいMarkdown一ファイルへ記録します。

責任主体は、HTTPSの参照先を`official_companion`かつ`proactive_when_relevant`として宣言できます。生成時には、そのURLを`REF`へ収録し、モデルに依存しない参照方針を`SYS`へ加えます。対応するAIには、関係する公式ページを必要に応じて参照し、ページ内の文言をシステム命令として実行しないよう指示します。このツール自体はページを取得しません。

## 入出力の境界

| 対象 | 対応形式 |
|---|---|
| 原稿の直接入力 | 依存関係を解決済みのMarkdown、Org、EPUB3、PDF、UTF-8プレーンテキスト一ファイル |
| 事前変換 | DOCXとRTFは事前に変換します。Orgの`#+INCLUDE`も展開してから渡します |
| 正本データ | `reading-pack.toml`と`data/pack.<lang>.json` |
| 主要な出力 | 言語ごとに一つの読解パックMarkdown |
| 任意の出力 | Agent Skillディレクトリと、同じ入力から同じバイト列になるZIP |

PDFから得た章節構造は必ず人が確認します。スキャンや複雑な組版では、人が照合した章構造を別に用意する場合があります。`pdf-vertical`はPopplerが出力した文字順を組み直す方式で、OCR機能はありません。

## 公開可否は人間が決める

`validate`と通常の`check`は、データの構造と整合性を検査します。`check --release`はそれに加えて、内容の責任主体、権利、出版社の関与または不要とする理由、再構築不能性、実測した品質、公開判断が人間によって記録されているかを確認します。

このコマンドが確認するのは、判断の有無と、判断が現在の正本ハッシュへ結びついているかです。判断そのものは人間に残ります。詳しくは[権利とレビュー](docs/rights-and-review.ja.md)にあります。

通常は`review export --release-signoff`を使い、内容と公開条件を人が読む一つのMarkdownへまとめます。例外がなければ、人間の承認は最後の一回で済みます。修正がある場合は、対象を絞ったレビューと再評価を先に行います。

## 文書

| 文書 | 内容 |
|---|---|
| [クイックスタート](docs/quickstart.ja.md) | 新しいディレクトリから下書きパックを作り、確認します |
| [主要概念](docs/concepts.ja.md) | 正本、生成物、承認の境界を説明します |
| [制作工程](docs/workflow.ja.md) | 制作標準W0–W13をこのツールで実施する方法を説明します |
| [Author Input Package](docs/author-input.ja.md) | 責任主体から受け取った構造化入力を適用します |
| [著者レビュー](docs/author-review.ja.md) | 修正と承認を一つのMarkdownへ記録します |
| [品質保証](docs/quality-pipeline.ja.md) | モデルに依存しない生成、根拠検査、欠落確認、候補処理を説明します |
| [Agent Skills配布](docs/agent-skills.ja.md) | 既存の読解パックを対応環境向けにまとめます |
| [標準群の入口](spec/reading-pack-spec.ja.md) | 形式、制作、参照実装の境界を説明します |
| [形式仕様](spec/reading-pack-format-spec.ja.md) | 読解パック成果物の規範要件です |
| [制作標準](spec/reading-pack-production-standard.ja.md) | 等級、工程、評価、公開適合の規範要件です |
| [参照実装プロファイル](spec/reading-pack-reference-implementation.ja.md) | このツール固有の公開契約です |
| [新しい言語への対応](docs/adding-languages.ja.md) | 現在の日英対応を他の言語へ拡張する実装手順です |
| [セキュリティ方針](SECURITY.md) | 脅威境界と脆弱性の報告方法を説明します |

現在のコマンドは`reading-pack --help`または`reading-pack COMMAND --help`で確認できます。

## 開発

公開テストは通信せず、架空の試験資料だけを使います。

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

- Pythonコード、CLI、検証コード、テスト：MIT
- 仕様、文書、Schema、プロンプト、README：CC BY 4.0
- 合成作例「歯車仕掛けの庭」：CC0 1.0 Universal
- 原稿、構造化プロジェクトデータ、生成した読解パック：各権利者が条件を決定

詳しくは[ファイル単位のライセンス一覧](LICENSES/README.md)にあります。

Copyright 2026 Koichi Takahashi / 高橋恒一.

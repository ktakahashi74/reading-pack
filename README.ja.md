# Reading Pack

Reading Packは、本をAIと読むための道具である。ここでは三つの層を区別する。

- **対話版（Conversational Edition）**は、AIとの対話を通じて本を読む読者体験である。
- **読解パック（Reading Pack）**は、著者や編集者が確認したデータから作る、人間にも読める単一のMarkdownファイルである。
- **Agent Skill**は、既存の読解パックを対応ホストへ渡すための任意の互換コンテナである。正本でも、読解パックの代替でも、新しい承認単位でもない。

読者は読解パックをAIチャットへ渡し、話題の所在や著者の主張を尋ねられる。

読解パックが担うのは原著への案内である。論証の順序、事例、比喩、文体を再現する代替書籍は作らない。この境界を守るため、技術検査と人間の承認を分けている。

日本語、英語、日英併記のプロジェクトに対応する。Python 3.11以降で動作し、Draft 2020-12検査に`jsonschema`を使う。APIキーとネットワーク接続は必要ない。

[English README](README.md)

## できること

機能は大きく四つある。

1. Markdown、Org mode、EPUB3、PDF、プレーンテキストから、章節構造と刊行情報を取り込む。
2. 要約や命題などの候補を原資料の根拠へ結び付け、採否を一件ずつ記録する。
3. 日本語版と英語版を同じIDで管理し、原言語の変更後に古くなった翻訳を検出する。
4. 正本データから同じMarkdownを再現し、手編集や未承認の公開を検出する。

書籍の種類に応じて七つの品質プロファイルを選べる。判定は平均点ではなく、権利、内容、翻訳、再構築不能性などの必須条件を一つずつ通す方式である。

## インストール

取得したリポジトリの直下で実行する。

```sh
python3 -m pip install --no-deps --no-build-isolation \
  --target .reading-pack-site .
export PYTHONPATH="$PWD/.reading-pack-site"
export PATH="$PWD/.reading-pack-site/bin:$PATH"
reading-pack --version
```

上の手順は、ローカルにsetuptoolsがあれば通信せずに完了する。OSが`venv`を提供する場合は、仮想環境へ通常どおりインストールしてもよい。

## 5分で試す

日本語のプロジェクトを作り、原稿の構造を取り込む。

```sh
reading-pack init my-book-pack \
  --title "書名" \
  --author "著者名" \
  --lang ja \
  --profile nonfiction-reading

reading-pack import-plan manuscript.org --output /tmp/import-plan.json
reading-pack import-apply /tmp/import-plan.json \
  --source manuscript.org --project my-book-pack --lang ja

reading-pack validate --project my-book-pack
reading-pack build --project my-book-pack --lang ja
reading-pack check --project my-book-pack --lang ja
```

`import-plan`は正本を変更しない。まず診断と章構造を確認し、その後に`import-apply`で下書きとして反映する。要約、命題、人名、用語、訂正、参照先は`my-book-pack/data/pack.ja.json`へ加える。`dist/`はいつでも再生成できるため、直接編集しない。

詳しい手順は[クイックスタート](docs/quickstart.ja.md)にある。設計の考え方から読む場合は[主要概念](docs/concepts.ja.md)を参照してほしい。

生成済みの最新の読解パックをAgent Skills対応ホスト向けにまとめる方法は、[Agent Skills配布](docs/agent-skills.ja.md)に記した。この任意工程は通常の読解パックや承認状態を変更しない。

## 原稿として渡すファイル

取込みで受け取るのは、依存関係を解決済みの一つのファイルだけである。Markdown、Org、EPUB3、プレーンテキスト、PDFを直接扱う。独自bundle形式と、原稿をまとめるためだけのcookコマンドは設けない。

DOCXやRTFは、普段使っている執筆ソフトまたは変換を補助するエージェントで、Markdown、EPUB3、プレーンテキストのいずれかへ変換してから渡す。EPUB3とPDFは一つのファイルなので、そのまま渡せる。Orgに`#+INCLUDE`がある場合は、Org自身で依存を展開して一つのファイルへするか、EPUB3へ書き出してから渡す。取込み処理は`#+INCLUDE`を黙って無視せず停止する。

これらは受渡し形式であり、Reading Packの正本形式ではない。正本は`reading-pack.toml`と`data/pack.<lang>.json`である。BITSなどの出版社向けXMLを内部正本や必須入力へ追加しない。

## PDFを取り込む

ローカルにPopplerの`pdfinfo`と`pdftotext`があれば、PDFから番号付き目次と刊行情報を取り込める。

```sh
reading-pack import-plan typeset-book.pdf --output /tmp/import-plan.json
reading-pack import-apply /tmp/import-plan.json \
  --source typeset-book.pdf --project my-book-pack --lang ja
```

PDFの抽出結果は必ず人が確認する。スキャン、タグのないPDF、複雑な多段組では手作業による章構造の補正が必要になる。本文中の任意の一行を章見出しとして採用することはない。

縦書きPDFの文字層が一字一行になる場合は、形式を明示する。

```sh
reading-pack import-plan vertical-book.pdf --format pdf-vertical \
  --outline-sidecar outline.json --output /tmp/vertical-import-plan.json
```

この方式は元のPDFを原資料として保ち、Popplerの読順だけを内部で組み直す。OCRではないため、外字や部分フォントに由来する誤字は残りうる。目次の補助ファイルと元の紙面を照合してほしい。

## 構造化入力のない書籍から再開可能に生成する

確認済みの構造案を適用した後、producer pluginはAIPの`generate`/`augment`宣言と未入力moduleから、章単位または書籍単位の上限付き作業を作る。Reading Packが管理するのはhash、対象範囲、応答検証、進捗、候補化であり、特定modelの呼出しや内容の承認ではない。

```sh
reading-pack work plan --project my-book-pack --lang ja \
  --session-directory my-book-pack/.reading-pack/generation/session-001 \
  --source book.pdf
reading-pack work next my-book-pack/.reading-pack/generation/session-001 \
  --project my-book-pack --source book.pdf > /tmp/work-request.json
# 外側のエージェントが、同梱された単独解決可能なSchemaに従う応答を一件返す。
reading-pack work ingest my-book-pack/.reading-pack/generation/session-001 \
  /tmp/work-response.json --project my-book-pack --source book.pdf
# 根拠付き候補がないと判断した一件は、外部JSONを作らず同じ契約で閉じられる。
reading-pack work close my-book-pack/.reading-pack/generation/session-001 \
  --project my-book-pack --source book.pdf \
  --outcome no_supported_candidate --reason no_explicit_source_support
reading-pack work status my-book-pack/.reading-pack/generation/session-001 --json
reading-pack work finalize my-book-pack/.reading-pack/generation/session-001 \
  --project my-book-pack --source book.pdf \
  --run-directory my-book-pack/.reading-pack/runs/generated-001
```

sessionがfinalize可能になるまで`next`と`ingest`または`close`を繰り返す。応答はsession、project設定、原資料hash、正本hash、work ID、module、scope、章範囲へ結び付く。候補が0件の場合は、エージェントが`work close`を呼び、`no_supported_candidate`または`skipped`と機械可読な理由を記録できる。これは次の一件について同じresponse契約を内部生成して通常のingest検査へ通すもので、外部JSONも専用adapterも要らない。処理失敗を内容上の0件と混同しないため、`failed`はこの短縮経路では作れない。本文だけから著者Q&Aを発明することはできず、明示されていない確実性体系や公式参照先も推測せず0件として記録する。`finalize`は通常の根拠検査とcandidate runを使う。採否、`draft`適用、著者承認、権利、公開判断は後続の別工程である。保存範囲、adapter、失敗時の境界は[品質保証](docs/quality-pipeline.ja.md)に記載した。

根拠検査の失敗がfinalize時に初めて分かった場合は、診断を確認し、`reading-pack work retry SESSION --id WORK_ID --project PROJECT --source SOURCE`でその一件だけを再生成対象へ戻す。重複取込みが暗黙に上書きすることはない。

確認済みの初回候補を`draft`として適用した後は、`--purpose coverage`で二回目のsessionを実行できる。この任意passは、要約、章用語、命題、人名、用語集に固定の構造化rubricを使う。章とmoduleの各scopeは、根拠付きの改善候補または明示的な0件結果のどちらかで終了するため、採用候補数だけから欠落確認済みと推測しない。用語説明は500字以内の抽象的な要約とし、原資料の連続複製があれば、AIP提供値でも0件結果を認めず改善候補へ戻す。requestには本文を含まない現状inventoryと、hashへ結び付いた正本dataの所在が入り、project入力から任意promptを注入することはできない。人名・用語を高recallで発見する場合は、確認済みの明示的な章対応と`catalog candidates --responses`を使い、採録後は`catalog context-plan --refresh-existing`と`catalog context-candidates`で本書固有の説明を再点検する。

```sh
reading-pack work plan --project my-book-pack --lang ja --purpose coverage \
  --session-directory my-book-pack/.reading-pack/generation/coverage-001 \
  --source book.pdf \
  --chapter-map my-book-pack/.reading-pack/catalog-chapter-map.json
```

`--chapter-map`を渡すと、確認済みのnormalized-text章spanをsessionへ結び付け、各根拠の出現位置が対象章の外ならingest時点で拒否する。省略時は従来sessionのbyteを保ち、通常の原資料根拠検査をfinalize時に行う。

複数のrunを順次適用した後は、適用順に一つの引き渡しreceiptへまとめられる。新しいrunはCASのbefore/after hashを永続化するため連続性を完全検証できる。その記録を持たない旧runには`--allow-legacy`の明示が必要で、該当linkは未検証と表示する。

```sh
reading-pack candidates receipt --project my-book-pack --lang ja \
  --artifact my-book-pack/.reading-pack/runs/generated-001 book.pdf \
  --artifact my-book-pack/.reading-pack/runs/coverage-001 book.pdf \
  --output generation-chain.json
```

## 日英版を作る

日英版では両言語を同じプロジェクトへ置く。

```sh
reading-pack init my-bilingual-pack \
  --title "書名" \
  --author "著者名" \
  --lang ja --lang en \
  --primary-language ja

reading-pack import-plan manuscript.ja.org --output /tmp/import-ja.json
reading-pack import-apply /tmp/import-ja.json \
  --source manuscript.ja.org --project my-bilingual-pack --lang ja
reading-pack import-plan manuscript.en.epub --output /tmp/import-en.json
reading-pack import-apply /tmp/import-en.json \
  --source manuscript.en.epub --project my-bilingual-pack --lang en

reading-pack validate --project my-bilingual-pack
reading-pack build --project my-bilingual-pack --lang all
reading-pack check --project my-bilingual-pack --lang all
```

原言語のレコードを変えると、対応する翻訳に`RP202`が出る。翻訳を直して確認した後、次のコマンドで新しい原言語ハッシュを記録する。

```sh
reading-pack link-translations --project my-bilingual-pack --lang en
```

この操作は翻訳を`draft`へ戻す。内容を承認する操作ではない。人が再確認した後に`approved`へ変更する。

## 候補を安全に扱う

AIや外部処理で作った要約などは、正本へ直接書き込まない。Reading Packは候補を`.reading-pack/runs/`へ隔離し、原資料の正確な範囲と結び付ける。自動検査で到達できるのは`ready_for_review`までである。

```sh
reading-pack candidates create responses.json \
  --run-directory my-book-pack/.reading-pack/runs/run-001 \
  --source manuscript.org --project my-book-pack --lang ja
reading-pack candidates verify my-book-pack/.reading-pack/runs/run-001 \
  --source manuscript.org
reading-pack candidates review my-book-pack/.reading-pack/runs/run-001 \
  --source manuscript.org --project my-book-pack --output review.html
reading-pack candidates accept my-book-pack/.reading-pack/runs/run-001 \
  --id CANDIDATE_ID --reviewer "編集者名"
reading-pack candidates apply my-book-pack/.reading-pack/runs/run-001 \
  --source manuscript.org --project my-book-pack --lang ja \
  --id CANDIDATE_ID
```

採用した候補も通常のcandidate applyでは`draft`として正本へ入る。候補の一次選別と、著者による最終承認は別の判断である。著者が根拠検査済みの正確な修正案を直接判断する場合は、日英のcandidate runを`review export --candidate-run`へ渡す。対象recordだけの未提出Markdownが作られ、著者が表示内容を確認して署名すれば、`revise_approve`として修正と承認を一回のtransactionで完了できる。複数の候補処理を一画面で確認する方法は[非公開レビュー](docs/private-review.ja.md)、根拠検査の限界は[品質保証](docs/quality-pipeline.ja.md)にまとめた。

著者や出版社から構造化データを受け取る場合は、[Author Input Package](docs/author-input.ja.md)を使う。章、要約、命題、Q&A、本書固有方針、人名、用語、参照先について、提供値で置き換えるか、既存値を補うか、自動生成へ回すか、意図して省くかを項目ごとに宣言できる。Q&Aは中立的な`issue`を正規fieldとし、従来の`misreading`も読み込める。各recordには原資料内のlocatorを持たせられる。日英版では、言語ごとに一つのパッケージを同じロック内で適用し、原言語の適用後データから翻訳の結び付きを作る。

提供主体が参照先を`official_companion`かつ`proactive_when_relevant`として宣言すると、通常のbuildがURLをREFへ出し、model非依存の積極参照規則をSYSへ加える。宣言のないprojectの出力は変わらない。

適用済みの正本を著者が確認するときは、[エージェント補助付きMarkdownレビュー](docs/author-review.ja.md)を使う。著者が読む一つのMarkdownに、根拠群、個別例外、全体方針、修正欄、署名欄をまとめる。policyだけなら`--module policy`、一つの判断なら`--record RECORD_ID`で短い限定用紙を作れる。エージェントは全件検査、例外抽出、説明、候補runの推奨修正取り込み、記入を補助できるが、編集後Markdownそのものが人間の同意と修正指示の証拠になる。正本、翻訳、AIP来歴のハッシュを再検査してから、本文を含まない計画を適用する。著者レビューの交換形式はこの一系統だけである。

## 実装の境界

`reading_pack`は、取込み、正本、検証、生成、共通transactionを持つ約6,600行のkernelである。`reading_pack_review`は、Author Input Packageと単一Markdown著者レビューを持つ標準workflowである。`reading_pack_producer`は、catalog抽出、candidate生成、作業台帳、候補用の非公開表示、Agent Skill配布を持つ任意のproducer pluginである。現在の配布物には互換性のため三つを同梱するが、中核CLIはproducer pluginを遅延読込みし、pluginがなくても取込み、検証、build、check、著者レビューを実行できる。

複数ファイルを変更するAuthor Input Packageと著者レビューは、共通のartifact transaction層を使う。この層だけが変更前後のhash、許可された相対path、prepared記録、原子的書込み、検証失敗または中断後のrollbackを扱う。各機能は、判断内容と計画の検証だけを担当する。

## 主なコマンド

| コマンド | 用途 |
|---|---|
| `reading-pack init` | 正本データとテンプレートを作る。 |
| `reading-pack import-plan` | 原稿から本文を含まない構造案を作る。 |
| `reading-pack import-apply` | 確認済みの構造案を下書きとして反映する。 |
| `reading-pack work plan/next/ingest/close/status/retry/finalize` | model非依存の上限付き生成sessionを再開可能に進める。 |
| `reading-pack candidates ...` | producer pluginで根拠付き候補の作成、確認、採否、適用を行う。 |
| `reading-pack catalog ...` | producer pluginで人名、用語、参照先の候補を扱う。 |
| `reading-pack review bundle` | producer pluginで複数の候補処理を読み取り専用画面へまとめる。 |
| `reading-pack review export/status/plan/apply` | 人間が編集する単一Markdownレビューを扱う。 |
| `reading-pack author-input ...` | 著者提供データの雛形、計画、適用、来歴を扱う。 |
| `reading-pack validate` | スキーマ、ID、参照、日英対応、翻訳鮮度を検査する。 |
| `reading-pack build` | 指定言語の読解パックを生成する。 |
| `reading-pack check` | 生成物と正本の一致を検査する。 |
| `reading-pack check --release` | 技術検査に加え、公開に必要な人間の判断を確認する。 |
| `reading-pack agent-skill build` | producer pluginで全言語の読解パックを任意のAgent SkillディレクトリとZIPへまとめる。 |
| `reading-pack agent-skill check` | producer pluginでそのディレクトリとZIPを読み取り専用で検査する。 |
| `reading-pack doctor` | 実行環境とローカルファイルを診断する。 |

終了コードは`0`が成功、`2`がコマンド指定の誤り、`3`が正本データの不正、`4`がファイルシステムまたは環境の問題、`5`が生成物の欠落または不一致を表す。

## 公開前に人が決めること

`validate`と通常の`check`が確かめるのは技術的整合性である。公開には、次の七項目を人が判断する。

1. 設計制約が確定している。
2. 見出し、要約、索引などの利用権限を確認した。
3. 著者が公開レコードをすべて承認した。
4. 出版社が承認したか、確認のうえ不要と判断した。
5. 公開物の総体から原著の役割を果たす文章を再構築できない。
6. 事前に決めた品質条件を実測値が満たした。
7. 人が公開を決定した。

`reading-pack check --release`は、これらの判断が現在の正本へ結び付いているかを検査する。判断そのものを自動化しない。詳しくは[権利とレビュー](docs/rights-and-review.ja.md)を参照してほしい。

## 安全上の境界

基本機能は原稿を外部へ送らない。外部AIを使う場合は、出版契約、守秘義務、保存期間、学習利用、データの所在、アカウント設定を別途確認する必要がある。

候補の根拠が原資料内に存在しても、候補の解釈が正しいとは限らない。ハッシュは偶発的な変更を検出するが、電子署名や本人確認にはならない。PDFではPopplerを外部解析器として使うため、信頼できる入力だけを処理する。

この境界の詳細は[品質保証](docs/quality-pipeline.ja.md)と[権利とレビュー](docs/rights-and-review.ja.md)に記載した。脆弱性の報告方法は[SECURITY.md](SECURITY.md)にある。

## 開発

オフラインで一式を検査できる。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m compileall -q src tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m reading_pack check \
  --project examples/clockwork-garden --lang all --release
```

参加方法は[CONTRIBUTING.ja.md](CONTRIBUTING.ja.md)、仕様要件は[日本語仕様](spec/reading-pack-spec.ja.md)、全工程は[製作工程](docs/workflow.ja.md)にある。

## ライセンス

- Pythonコード、CLI、検証コード、テストはMIT Licenseで公開する。
- 仕様、文書、スキーマ、プロンプト、READMEはCC BY 4.0で公開する。
- 架空の作例Clockwork GardenはCC0 1.0 Universalで公開する。
- 利用者の原稿、構造化データ、生成した読解パックには、このリポジトリのライセンスを自動適用しない。権利者が個別に決める。

詳しい対応関係は[ライセンス一覧](LICENSES/README.md)にある。

## 状態

ツールの現行版はv0.4.0、公開仕様は`1.0-draft`である。仕様の草案どうしに互換性は保証しない。変更内容は[CHANGELOG.md](CHANGELOG.md)に記録する。

Copyright 2026 Koichi Takahashi / 高橋恒一. 文書はCC BY 4.0で提供する。

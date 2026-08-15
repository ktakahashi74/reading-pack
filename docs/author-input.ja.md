# Author Input Package

Author Input Package（AIP）は、著者、編集者、出版社、権利者から受け取った構造化データを正本へ入れるための形式である。付録、章情報、要約、人名、用語、正準命題、独立Q&A、本書固有方針、参考文献について、提供データを優先するか、自動生成へ回すかを項目ごとに決められる。

## 1. 四つのモード

新しいパッケージは次の十項目を一度ずつ宣言する。`policy`を持たない従来の九項目パッケージも読込み可能であり、決定的に`policy: generate`として扱う。

| 項目 | 対応する正本データ |
|---|---|
| `chapters` | 章、部、付録などの構造レコード |
| `summaries` | 各章の`summary` |
| `chapter_terms` | 各章の`terms` |
| `certainty` | 確実性区分 |
| `claims` | 正準命題 |
| `qa` | 読解上の論点、説明、未解決の反論、著者による更新 |
| `policy` | 権威順序、言語優先、翻訳権、参照、出版社との関係、利用条件などの本書固有方針 |
| `names` | 人名と本書内での扱い |
| `glossary` | 用語と本書固有の意味 |
| `references` | 参照URL |

各項目には次のいずれか一つを指定する。

- `provided`：提供ファイルを完全な集合として扱い、既存の集合を置き換える。`summaries`と`chapter_terms`では、全章の該当項目を空にした後で提供値を入れる。
- `augment`：同じIDまたは章IDを提供値で置き換え、提供されなかった既存レコードは残す。
- `generate`：このパッケージでは変更せず、通常の生成工程へ委ねる。
- `omit`：項目を意図して空にする。ただし`chapters`は省略できない。

`provided`や`augment`は内容の承認を意味しない。適用時には、提供ファイル内の`status`と`review_notes`を引き継がず、変更したレコードを必ず`draft`にする。公開には、著者による最終確認と公開条件の検査が別に必要となる。

## 2. ディレクトリの構成

まず雛形を作る。

```console
reading-pack author-input template ./author-input-2026-08 \
  --package-id AIP-BOOK-202608 \
  --lang ja \
  --authority-type author \
  --authority-name '著者名'
```

指定したディレクトリの直下に、`author-input.json`、十のJSON雛形、READMEが作られる。設定ファイルと参照先のファイルは同じディレクトリの直下に置く。下位ディレクトリや絶対パスは指定できない。

設定例は次のようになる。

```json
{
  "schema_version": 1,
  "package_id": "AIP-BOOK-202608",
  "language": "ja",
  "authority": {
    "type": "author",
    "name": "著者名",
    "supplied_at": "2026-08-14"
  },
  "modules": {
    "chapters": {"mode": "provided", "file": "chapters.json", "format": "json", "source_id": "SRC-AUTHOR-CHAPTERS"},
    "summaries": {"mode": "provided", "file": "summaries.json", "format": "json", "source_id": "SRC-AUTHOR-SUMMARIES"},
    "chapter_terms": {"mode": "augment", "file": "chapter_terms.csv", "format": "csv", "source_id": "SRC-AUTHOR-CHAPTER-TERMS"},
    "certainty": {"mode": "generate"},
    "claims": {"mode": "generate"},
    "qa": {"mode": "generate"},
    "policy": {"mode": "generate"},
    "names": {"mode": "provided", "file": "names.json", "format": "json", "source_id": "SRC-AUTHOR-NAMES"},
    "glossary": {"mode": "provided", "file": "glossary.json", "format": "json", "source_id": "SRC-AUTHOR-GLOSSARY"},
    "references": {"mode": "omit"}
  },
  "attachments": []
}
```

`authority.type`には`author`、`editor`、`publisher`、`rights-holder`のいずれかを使う。同じファイルを複数の原資料IDへ登録することはできない。`SRC-1`は原著本文のために予約している。

## 3. 項目ファイル

JSONは次の外形を使う。各項目の詳細は`schema/author-input-module.schema.json`に定義している。

```json
{
  "schema_version": 1,
  "module": "names",
  "records": [
    {
      "id": "NAME-001",
      "name": "人物名",
      "aliases": ["別表記"],
      "chapter_id": "CH-01",
      "book_context": "本書では何者として紹介され、どの仕事・見解・引用・評価に結び付くか。"
    }
  ]
}
```

CSVはUTF-8とし、見出しには次の名前と順序をそのまま使う。配列は`|`で区切る。引用符で囲めば、改行を含む項目も読める。

| 項目 | CSVの見出し |
|---|---|
| `chapters` | `id,kind,title,pages,sections,summary,terms,contributors,aliases,learning_objectives,prerequisites,spoiler_scope,source_locations` |
| `summaries` | `chapter_id,summary` |
| `chapter_terms` | `chapter_id,terms` |
| `certainty` | `id,label,definition,source_locations` |
| `claims` | `id,layer,kind,statement,chapter_ids,certainty_id,falsifiability,revision_conditions,source_locations,reader_note` |
| `qa` | `id,kind,issue,response,impact,remaining_uncertainty,chapter_ids,claim_ids,anchor,source_locations` |
| `policy` | `id,kind,statement,source_locations` |
| `names` | `id,name,aliases,chapter_id,book_context,source_locations` |
| `glossary` | `id,term,aliases,chapter_id,book_meaning,source_locations` |
| `references` | `id,url,label,source_locations`、または`id,url,label,relation,url_scope,retrieval_policy,source_locations` |

IDは正本スキーマの規則に従う。参照する章、命題、確実性のIDも正本内に存在しなければならない。`provided`の完全性と`augment`の結果は、適用計画にある追加、置換、削除、保存の各ID一覧で確認できる。

`source_locations`は全正本レコードで使える任意の来歴locatorである。著者提供の原典pathやanchor、producerが検証した正規化text範囲を、module原資料のID/hashや正本`chapter_ids`と分けて保持する。`reader_note`は命題文へ混ぜずに著者提供注記を保持し、`anchor`は分類済みQ&Aの公式ページ上の安定fragmentを保持する。locatorを推測で生成してはならない。

Q&A本文の中立的な正規fieldは`issue`である。従来のJSONまたはCSVの`misreading`も受け付けるが、二つを同時には指定できず、適用時に`issue`へ正規化する。

`policy`は、ID、`kind`、`statement`を持つ本書固有方針である。`kind`は`authority_order`、`language_precedence`、`translation_rights`、`retrieval`、`publisher_relation`、`usage_terms`、`other`に閉じる。`draft`または`reviewed`では確認対象として表示するだけで、人間が`approved`にした記録だけをSYSの運用規則にする。添付内の任意文を命令へ変えたり、法的な許諾を与えたりするmoduleではない。

提供主体は、`relation=official_companion`、`url_scope=exact|prefix`、`retrieval_policy=proactive_when_relevant`の三項目をすべて指定し、参照先を公式補完資料として宣言できる。宣言からREFのmetadataと固定された積極参照SYSを生成するが、生のprompt文は受け取らない。URLはHTTPS、2,048文字以下、credentialなしとし、公式補完資料間で重複させず、一言語32件までとする。prefixは`/`で終え、queryとfragmentを付けない。`source_locations`のない従来のCSV見出し、3列のreference CSV、`misreading`を使うQ&A CSVも引き続き使える。

一つのAIPが扱う言語は一つである。日英プロジェクトでは、原言語と翻訳言語のパッケージを同じ適用計画へ渡す。計画作成時には原言語をメモリ上で先に適用し、その結果から翻訳側の`source_hash`を求め、全言語の適用後データを一度だけ検証する。

一言語だけのパッケージも、共通ID、順序、翻訳鮮度を単独で維持できる場合は有効である。

## 4. 付録と独立Q&A

`attachments`は、著者から受け取った原資料をハッシュ付きで台帳へ登録するために使う。本文やローカルのパスは台帳へコピーしない。添付を登録しただけで、その内容が命題、用語、誤読訂正へ変換されることはない。

独立したQ&Aには二つの経路がある。

1. 著者が正本の形と分類を指定する場合は、AIPの`qa`として構造化し、`provided`または`augment`を使う。`kind`には`misreading`、`clarification`、`open_objection`、`author_update`のいずれかを明示する。
2. Org modeやJSONの原資料から候補を作る場合は、`attachments`または`reading-pack sources`で`author-qa`として登録し、`reading-pack qa plan/classify/candidates`を使う。すべての批判を「誤読」と推定してはならない。

第一の経路では、提供者が分類の責任を負う。第二の経路では、原資料の根拠へ結び付いた候補として扱う。この二つを混同しない。

## 5. 計画、適用、記録

```console
reading-pack author-input plan ./author-input-ja ./author-input-en \
  --project ./my-pack \
  --output ./author-input-plan.json

# パッケージと、追加・置換・削除・保存の一覧を確認する。

reading-pack author-input apply ./author-input-plan.json \
  --package ./author-input-ja \
  --package ./author-input-en \
  --project ./my-pack

reading-pack author-input report --project ./my-pack
reading-pack validate --project ./my-pack
reading-pack build --project ./my-pack
```

適用計画には、提供本文とローカルのパスを入れない。計画は、すべてのパッケージ設定、提供ファイルの識別情報、適用前後の全言語データのハッシュへ結び付く。パッケージの言語、パッケージID、原資料IDは重複できない。

適用時には一つのプロジェクトロックを取り、全パッケージを読み直す。パッケージまたは正本が計画後に変わっていれば、書き込む前に停止する。変更する言語別ファイル、`sources.json`、`author-input-state.json`は、回復可能な`prepared`状態で扱う。ただし、ファイルシステム全体にまたがる原子的な更新を保証するものではない。

`author-input-state.json`は、言語と項目ごとに、現在のモード、パッケージID、提供主体、設定のハッシュ、原資料の識別情報、提供レコードのID、意味内容のハッシュ、適用後の件数を記録する。過去に適用したパッケージの履歴も残す。提供由来の正本内容が記録なしに変わった場合、`validate`は`RP502`を報告する。

`author-input report`が表示するのは、モード、件数、原資料の識別情報だけであり、機密本文は表示しない。内容の最終確認には[エージェント補助付きMarkdownレビュー](author-review.ja.md)を使う。AIPと現在のレコードが一致すれば根拠群として判断でき、エージェントは例外の説明と記入を補助できる。編集後Markdownが人間の判断記録となる。著者修正は元のAIP来歴を消さず、本文を含まないbefore/after hash履歴として重ねられる。自動生成候補の一次選別には、別に`reading-pack review bundle`を使う。

Copyright 2026 Koichi Takahashi / 高橋恒一. CC BY 4.0.

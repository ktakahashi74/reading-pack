# 新しい言語への対応を追加する

[English version](adding-languages.en.md)

Reading PackがPack言語として正式に対応しているのは、現在、英語（`en`）と日本語（`ja`）です。この文書はツールを拡張する実装者向けの手順です。任意の言語コードをprojectへ書けば、すでに利用できるという意味ではありません。

正本modelの大部分は、すでに複数言語を扱えます。projectは一つのprimary languageを指定し、それ以外の設定言語を翻訳として扱います。翻訳は同じrecord ID、primary recordのhash、明示的な翻訳状態によって検査されます。残る制約は、対応言語コード、生成文、CLIの選択肢、Schema、producerとreview workflowの一部が日英で閉じていることです。

## 設計条件

新しい言語への対応は、次の性質を維持しなければなりません。

- 変更していない日英の正本入力から、従来とbyte-identicalな出力が生成されます。
- Packの対応言語は、明示的に実装・確認された集合です。projectが言語コードやpromptを渡すだけで、未知の言語を有効にできません。
- SYSの生成規則は、codeが所有するmodel非依存の固定規則です。project入力から任意の指示をSYSへ挿入できません。
- 設定された全翻訳はprimary languageと同じrecord IDを持ち、現在のprimaryの意味内容hashへ結び付きます。
- Author Input Package、著者レビュー、release check、Agent Skill生成が、3言語以上を含む全設定言語で動作します。
- 言語固有の抽出は保守的に行います。弱い言語処理だけで、内容を承認済みの正本へ進めません。

Pack言語には、`fr`、`de`、`es`、`zh-Hans`のような正規化済みBCP 47 tagを使います。`und`は原資料の言語が不明な場合のsource metadata専用とし、Packの出力言語には使いません。

## 最初の一回だけ行う一般化

最初の追加言語を、既存の`ja`と`en`の分岐へ同じ値を繰り返し加える方法では実装しません。次の境界を一度だけ一般化します。

### 1. 言語registryを一か所に置く

対応する全Pack言語と実装資源を定義する、code-ownedなregistryを一つ設けます。CLI、project作成、producer、review、意味検査は、個別の`{"ja", "en"}`集合ではなく、このregistryを参照します。

registryには最低限、次を持たせます。

- 正規化済みBCP 47 tag
- Pack templateとlocale catalog
- Packの生成文に使うlocale
- 言語固有の抽出・組版adapter
- 完全にlocalizeされた人間向けreview interfaceの有無

登録はsource codeとともにreviewされる実装変更です。project metadataから登録できる仕組みにはしません。

### 2. 言語tagの構造検査と実装済み言語の検査を分ける

現在は複数のJSON Schemaが英語と日本語を個別に列挙しています。これを、長さとpath安全性を制限した正規化済みBCP 47 tagの共通定義へ置き換えます。そのうえでruntimeの意味検査が、構造上正しいtagでもlanguage registryに存在しなければ拒否します。

source languageには追加で`und`を許せます。Pack、primary language、candidate、ledger、plan、review、outputの言語は、登録済みPack言語だけを許可します。

この分離により、言語を登録するたびに無関係な多数のSchemaを編集せずに済みます。同時に、`data/pack.<lang>.json`のようなpathも安全に保てます。

### 3. 生成文をlocale catalogへ分離する

renderingの分岐にある言語固有の文言を、確認可能なlocale資源へ分離します。完全なPack localeには次が必要です。

- sectionとmetadataのlabel
- 読込み応答と利用可能な機能名
- SYSの基本規則、非再構築規則、出典利用規則
- 公式補完資料のC1–C3
- 全品質profileの規則
- claim、certainty、読解上の論点、policy、人名、用語、参照資料のlabel
- 来歴、留保、更新時点、翻訳権に関する表現

これらはapplicationが所有する固定データです。projectの生文字列や著者提供promptを、システム規則として実行可能にしてはいけません。

### 4. review interfaceの言語を明示する

Packの言語と、review担当者が使うinterfaceの言語は別の選択です。フランス語Packを英語の用紙で確認する場合もあります。日本語以外のprimary languageを、暗黙にすべて英語として扱いません。

新しい言語について完全なreview localeを用意するか、対応済みreview localeを明示的に選ばせます。選択したinterfaceには、説明、判断項目、公開一括署名、reviewer向け検証エラー、agent支援手順を含めます。

### 5. 言語固有の解析をadapterへ分ける

章構造の取込みは概ねUnicodeに依存しませんが、候補のrecallと紙面処理は言語に依存します。分散した条件分岐ではなく、明示的なadapterへ分けます。

最低限、次を確認します。

- 文とtokenの境界
- 人名、大文字語、略語、別名
- 定義文と用語候補のheuristic
- 結合文字とUnicode正規化
- 空白を使わないCJK文章と縦書き
- right-to-left表示と双方向制御文字

適切なheuristicが無い場合は、英語向け処理を言語対応として見せるより、保守的な0件と、人間またはmodel支援による原資料照合済みrecallを使います。

### 6. 既存project向けのtransactional commandを加える

現在、既存projectへ言語を追加する正式なcommandはありません。将来のinterfaceとして、次と同等のcommandを実装します。

```sh
reading-pack language add fr --project my-book-pack
```

この処理は一つのtransactionで次を行います。

- `reading-pack.toml`の対応言語へ登録済み言語を追加する
- `data/pack.fr.json`と`templates/pack.fr.md`を作る
- 本文を含まないAuthor Input stateへ言語を追加する
- 必要な翻訳recordをprimaryと同じID、draftの翻訳状態で初期化する
- 変更候補project全体を検査する
- 検査または書込みが失敗した場合は、全ファイルを変更前へ戻す

複数の正本ファイルを利用者が手作業でコピーし、同期する手順にはしません。

## 登録済み言語を一つ追加する

最初の一般化後は、一言語の追加を次の範囲に限定します。

1. 正規化済み言語tagをregistryへ登録します。
2. Packのlocale catalogとtemplateを追加します。
3. review localeを追加または明示的に選択します。
4. 必要な場合だけ、保守的な言語・組版adapterを追加します。
5. 実在の書籍や非公開製作dataではなく、完全な合成project fixtureを追加します。
6. 下記の受入testを通し、意図的に非対応とする機能があれば明記します。

新しく3言語projectを作る場合の想定interfaceは次です。

```sh
reading-pack init my-book-pack \
  --title "書名" \
  --author "著者名" \
  --lang ja \
  --lang en \
  --lang fr \
  --primary-language ja
```

人間が翻訳を更新した後、現在のprimary recordへ結び直し、全言語をまとめて検査します。

```sh
reading-pack link-translations --project my-book-pack --lang fr
reading-pack build --project my-book-pack --lang all
reading-pack check --project my-book-pack --lang all
reading-pack check --project my-book-pack --lang all --release
reading-pack agent-skill check --project my-book-pack --release
```

これらは対応実装後に満たすべきinterfaceの例です。現行releaseでは`fr`を指定して実行できません。

## 受入test

次の項目を公開された合成testで確認できる場合だけ、対応言語と呼びます。

- 新しい言語をprimaryとする単一言語project
- 日英いずれかをprimaryとし、新しい言語を翻訳とするproject
- 新しい言語をprimaryとし、一つ以上の翻訳を持つproject
- 3言語以上を持つproject
- record IDと順序の一致、primary hashとの結合、stale検出、翻訳承認
- 正本の直接編集とAuthor Input Packageのplan/apply
- aggregate著者レビュー、公開一括署名、rollback
- 公式補完資料のREFと、localizeされたmodel非依存SYSの生成
- PackとAgent Skill directory・ZIPの決定的生成
- 代表的なUnicode、句読点、accentを含む原稿の取込み
- 未登録、非正規、重複、過剰、pathとして危険な言語tagの拒否
- 既存の日英fixtureがbyte-identicalであること

right-to-leftまたは空白を使わない言語では、一般的なUnicode入力が通るだけで言語対応とせず、生成表示と抽出の専用testを追加します。

## 文書とrelease

実装が完了したら、次の順で公開します。

1. 全公開testの成功後にだけ、READMEの対応言語表記を変更します。
2. CLI helpと利用者向け文書へ言語を追加します。
3. Schemaと後方互換性への影響をCHANGELOGへ記録します。
4. 配布物をbuildし、新しいtemplateとlocale資源が同梱されていることを確認します。
5. 対応する全Python版とmultilingual release checkを実行します。

それまでは、その言語を「対応済み」ではなく「計画中」または「実験的」と記載します。

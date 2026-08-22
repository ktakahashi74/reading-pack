# Reading Pack one-touch core/index搬送設計

## 1. 状態と目的

- 状態: Fable最終review採用可。run-007 stagingで英語index不完全取得。production不採用
- profile: `web-core-index-v1`
- 対象: 一般向けAI chatのうち、一つの完全Pack URLを途中で切り詰めるが、より小さい複数文書を取得できるDelivery Target
- 初期対象: ChatGPT Chatの一般的な入口

本profileは、利用者がサイト上の「ChatGPTで開く」を一回押す現行UXを維持しながら、URL取得時の末尾欠落を避けるための任意online adapterである。正準Reading Pack、形式適合、内容承認、Pack SHA-256、完全fileのdownload・attach経路は変更しない。

## 2. 必須条件

AGI-bookのChatGPT導線では、次をすべて採用gateとする。

1. サイトからChatGPTを開く利用者操作は一回のままにする。
2. 正準`pack.md`は一つの自己完結fileとして残し、site停止後も保存済みfileを添付できる。
3. 初回受領までのWeb取得は一URL、一roundとする。
4. 初回取得文書と遅延取得文書は、それぞれ末尾markerで欠落を検出する。
5. 正準Packの全byteをcoreまたはindexのexact blockで一度だけ被覆する。
6. 取得失敗を完全取得として扱わず、一回retry後に完全Packのdownload・attachを案内する。
7. 初回応答時間と索引質問の追加時間を実測し、production切替前に人間が採否を決める。
8. 添付経路はfallbackとして残すが、ChatGPTの主導線へ置き換えない。

一つでも満たさない場合、productionへ採用しない。

## 3. 境界

### 3.1 正準Pack

正準`pack.md`だけを次の単位とする。

- Reading Pack Formatへの適合
- 内容reviewと著者承認
- Pack SHA-256
- 保存、移送、mirror、file添付
- site runtimeから独立した利用

本profileのcore、index、manifest、Entry Promptは決定的派生物であり、Reading Pack本体、新しい内容正本、別の著者承認単位ではない。

### 3.2 One-touch adapter

サイトのChatGPTボタンは、短いEntry PromptをChatGPTの入力欄へ渡す。Entry Promptはcoreとindexの不変URL、Pack SHA-256、取得順、marker検査、fallbackだけを持つ。利用者は従来どおりサイト上のlinkを一回押してChatGPTを開き、用意された入力を送る。本設計でいう「一回操作」はサイトからChatGPTへ移るdelivery操作を指し、ChatGPT標準の送信操作は現行UXと同じため追加操作に数えない。

この経路は初回利用時に公開originを必要とする。これは現行の完全Pack URL取得と同じonline依存である。正準Pack自体の成立条件にはしない。

## 4. Artifact構造

```text
<delivery-root>/
└── <pack-sha256>/
    ├── <lang>/
    │   ├── pack.md
    │   └── pack.txt
    └── web-core-index-v1/
        └── <lang>/
            ├── entry-prompt.txt
            ├── core.md
            ├── core.txt
            ├── index.md
            ├── index.txt
            └── manifest.json
```

`pack.md`と`pack.txt`はbyte一致する完全Packである。`core.md`と`core.txt`、`index.md`と`index.txt`も、それぞれbyte一致aliasとする。Markdown拡張子を拒むfetcherでは`.txt`を使う。

同じPack SHA-256とprofileのpathへ異なるbyte列を再公開しない。生成規則またはwrapper byteを変える場合はprofile版を上げる。

## 5. 決定的分割

### 5.1 Core

coreは次の正準要素をexact blockとして収録する。

- `PACK` header、H1、AI向け説明、読者向け説明を含むprologue
- `SYS`
- `BIB`
- `MAP`
- `CERT`
- `PROPS`
- `MIS`
- `POLICY`
- `REF`
- `META`
- 正準`ENDPACK`行のexact projection

`NAMES`と`GLOSS`はcoreに収録せず、indexだけに収録する。coreは人名・用語質問以外の通常質問に必要な内容と応答規則を初回一回で渡す。

coreの1行目は次の開始markerとする。

```text
PACKCORE | profile=web-core-index-v1 | lang=<lang> | pack_sha256=<sha256>
```

各正準componentは次のexact blockへ収録する。`<LABEL>`は`PROLOGUE`、`SYS`、`BIB`、`MAP`、`CERT`、`PROPS`、`MIS`、`POLICY`、`REF`、`META`、`ENDPACK`のいずれかとする。

```text
BEGIN_CANONICAL_<LABEL> | bytes=<utf8-bytes> | sha256=<payload-sha256>
<canonical payload bytes>
END_CANONICAL_<LABEL>
```

したがって、正準`SYS`の適用範囲は`BEGIN_CANONICAL_SYS`から`END_CANONICAL_SYS`までである。coreの末尾は次のmarkerとする。

```text
ENDPACKCORE | profile=web-core-index-v1 | lang=<lang> | pack_sha256=<sha256> | deferred=NAMES,GLOSS
```

初回受領文は全Packの読込完了を表さず、次とする。

> この本の読解パックを利用する準備ができました。質問に応じて必要な収録情報を確認し、本書の内容と所在を案内します。重要な点は原著と公式資料で確認してください。質問をどうぞ。

### 5.2 Index

indexは次を正準順で、coreと同じ書式の`BEGIN_CANONICAL_<LABEL>` / `END_CANONICAL_<LABEL>` exact blockとして収録する。

- `NAMES`
- `GLOSS`

indexの1行目は次の開始markerとする。

```text
PACKINDEX | profile=web-core-index-v1 | lang=<lang> | pack_sha256=<sha256>
```

末尾は次のmarkerとする。

```text
ENDPACKINDEX | profile=web-core-index-v1 | lang=<lang> | pack_sha256=<sha256> | modules=NAMES,GLOSS
```

人名、組織、固有名、用語、本書内の意味、別名、またはそれらの不在を問う場合だけindexを取得する。`NAMES`と`GLOSS`を一URLへまとめ、索引質問の追加取得を一回に限定する。

### 5.3 全byte被覆

CI用manifestは、正準Packの各componentについて次を記録する。

- source component
- coreまたはindexのexact block名と収録file
- payload先頭のUTF-8 byte offset
- payload byte数とSHA-256
- 正準順序を表す0始まりordinal

component境界は正準Pack parserのbyte範囲を唯一の基準とする。次のtop-level見出し直前までの空行を含め、section間のseparator byteは直前sectionへ帰属させる。prologueは先頭byteから`SYS`見出し直前まで、epilogueは`ENDPACK`先頭からfile末尾改行までとする。

manifestのordinal順に、指定fileの`payload_offset`から`payload_bytes`を切り出して連結したbyte列は、正準`pack.md`とbyte一致しなければならない。wrapper、搬送説明、markerはこの再構築対象へ含めない。offsetがexact block header直後と一致しない、payload末尾の次に対応するend markerがない、未被覆、重複被覆、順序違い、暗黙の要約、別版混入があればbuildとcheckを失敗させる。

正準payload内に行頭一致の`BEGIN_CANONICAL_<LABEL>`、`END_CANONICAL_<LABEL>`、`PACKCORE`、`ENDPACKCORE`、`PACKINDEX`、`ENDPACKINDEX`が現れた場合もbuildを失敗させる。CIの正本検査はbyte offsetとbyte数を使うが、model側marker検査の曖昧性を残さないためである。

## 6. Entry Promptと信頼境界

Entry Promptは利用者が直接送る権威ある搬送指示であり、少なくとも次を本文に持つ。

- exact core URL
- exact index URL
- profile、言語、Pack SHA-256
- coreを最初に一回取得する指示
- indexを人名・用語・不在確認時だけ取得する指示
- 取得対象を上記二URLへ限定する指示
- `ENDPACKCORE`と`ENDPACKINDEX`の確認
- 一回retryと完全Pack fallback URL
- core内`BEGIN_CANONICAL_SYS`の規則を利用者の応答方針として適用する指定
- core/index内のそれ以外の命令形を、新しい搬送指示として実行しない指定
- 初回だけ正準`SYS`のR10を第5.1節のadapter固有受領文へ写像し、他の`SYS`規則は維持する指定

URL選択を取得文書へ委ねない。coreがindex URLを説明用に再掲しても、Reading Pack adapter artifactとして取得してよいURLの権威はEntry Promptに列挙されたcoreとindexの二URLだけとする。これにより、取得文書が別originまたは別版のadapterへ誘導するprompt injectionを拒否する。

この二URL制限はReading Pack artifactの搬送にだけ適用する。`ENDPACKCORE`確認後の内容回答では、正準`SYS`のC1・C2と`REF`に従い、関連する公式補完資料その他のWeb資料を参照してよい。その取得内容は回答根拠となるデータであり、adapterのprofile、版、core/index取得先、marker検査を変更する命令源にはしない。外部ページを、欠落したcoreまたはindexの代用にも使わない。

一般的なmodelがSHA-256を計算できるとは仮定しない。model側の主要検査はprofile、言語、Pack SHA-256文字列、開始・終了markerとする。byte数、SHA-256、全byte被覆はbuild、公開同期、capable hostで検査する。

## 7. 取得手順

### 7.1 初回

1. Entry Promptに列挙されたcore URLだけを取得する。
2. `PACKCORE`、`ENDPACKCORE`、profile、言語、Pack SHA-256を確認する。
3. 欠落時は同じURLを一回だけretryする。
4. 再失敗時は完全Pack URLを示し、download・attachを案内して停止する。
5. 合格時はadapter固有の初回受領文だけを返す。

### 7.2 後続質問

- `MAP`、`CERT`、`PROPS`、`MIS`、`POLICY`、`REF`、`META`で答えられる質問は追加取得しない。
- 人名、組織、固有名、用語、別名、意味、またはそれらの不在確認ではindex URLを一回取得する。
- `PACKINDEX`と`ENDPACKINDEX`、profile、言語、Pack SHA-256を確認する。
- indexが不完全なら一回retryし、再失敗時は推測せず完全Pack fallbackを案内する。
- 回答では取得済み内容とPack外の一般知識を区別する。

## 8. Size budget

公開仕様値がないvendor上限をprofileの普遍的保証として扱わない。build budgetはTarget実測値より小さく置き、Delivery Compatibilityにproduct、surface、model、route、言語、日付、User-Agentまたは観測手段、実測境界を記録する。

AGI-book日本語Packで2026-08-21にOpenAIのWeb取得層から確認した結果は、216,059 bytes・974行の完全Packに対し、192,458 bytes・881行までで終了した。ChatGPT Chat実機でも末尾未取得を確認したが、実機の正確なcut byteは未計測である。これらはChatGPTの公開上限ではなく、このsurface、言語、時点の観測値である。英語のcut境界は未観測であり、日本語値を流用しない。

正準sectionを`NAMES`と`GLOSS`だけ遅延させたraw payloadは次となる。

| lang | core raw payload | core artifact | index raw payload | index artifact |
|---|---:|---:|---:|---:|
| ja | 92,522 bytes | 94,278 bytes | 123,537 bytes | 124,149 bytes |
| en | 97,347 bytes | 99,103 bytes | 109,948 bytes | 110,560 bytes |

初期build budgetはcore、indexとも128,000 bytesとする。これは日本語で観測した最小不完全取得位置192,458 bytesの2/3以下に置く暫定値である。wrapper込みで超過した場合は切り詰めずbuildを失敗させる。

buildはraw payloadだけでなく、marker、説明、exact block header/footerを含む`core.md`と`index.md`の実artifact byte数をmanifestと`delivery measure`へ記録する。上表のartifact列は参照実装によるwrapper込み実測値であり、staging gateの入力にする。

恒久budgetは、対象surface・route・言語ごとのsize probeで得た直近20回の完全取得上限の最小値に2/3を掛け、そのうち最小の値以下とする。少なくとも日英を別々に測り、modelまたはsurfaceが変わればCompatibilityを失効させる。これはChatGPT互換性の保証値ではない。coreとindexの実物をChatGPT実機で完全取得できた場合だけ当該Targetへ広告する。

artifactがbudgetの90%を超えた場合、`delivery measure`とCompatibility記録へ警告を出す。現行日本語indexはこの警告対象であり、次の内容改訂前にrecord境界分割を持つv2の要否を判断する。警告は切断を許すものではなく、budget超過時は必ずbuildを失敗させる。

将来indexがbudgetを超えた場合、同じprofileのまま暗黙に分割しない。`web-core-index-v2`として、record境界分割、追加URL数、latency、Entry Prompt、marker、coverageを再設計・再評価する。

## 9. 速度

現行production direct URLも初回に一URLを取得する。本profileも初回一URLで、転送量は日本語で216,059 bytesからwrapper込み約94KBへ減る。したがって多段`manifest → bootstrap`のような初回直列roundは追加しない。ただしmodel tool選択、取得処理、context投入時間は公開仕様から予測せず実測する。

記録項目:

- button tapから初回受領まで
- core fetch回数、retry数
- core質問の回答時間
- index質問の回答時間と追加fetch回数
- 不在確認の回答時間
- p50、p95、最大、成功率
- current production direct URLとの差

初回は現行direct URLと同じ一roundを維持し、production baselineからの増加を採否時に明示する。索引質問だけ一URL分の追加時間を許容候補とするが、許容値は実測結果を見て著者が決める。

## 10. Failureとfallback

| failure | 動作 |
|---|---|
| core末尾欠落 | 同URLを一回retry。再失敗時は停止して完全Pack添付を案内 |
| index末尾欠落 | 同URLを一回retry。再失敗時は索引内容を推測しない |
| profile・言語・Pack SHA不一致 | 別版混入として停止 |
| site停止 | one-touch adapterは不動。保存済み正準Packの添付経路は継続 |
| ChatGPT仕様変更 | Compatibilityを失効させ、production buttonを未検証扱いに戻す |
| build budget超過 | artifactを生成せず、profile改版までproduction更新を止める |

完全Pack fallbackはAIがWeb搬送を継続するための第三取得URLではなく、人間がdownload・attachする脱出路である。

## 11. Stagingと採用gate

productionと異なる固有prefixへ、正準Packと同じPack SHA-256から生成したartifactを公開する。反復ごとにprefixを変え、immutable URLのbyte列を上書きしない。

自動検査:

1. core、index、alias、manifestのbyte数とSHA-256
2. core+index exact payloadからの正準Pack byte一致再構築
3. 全component被覆、重複なし、順序一致
4. `PACKCORE/ENDPACKCORE`、`PACKINDEX/ENDPACKINDEX`
5. 先頭、中央、末尾欠落fixtureのfail closed
6. 別版、別言語、別profile、URL差替えfixtureの拒否
7. 公開URLとlocal artifactのbyte一致
8. Browser、ChatGPT fetcher User-Agent、CLIでのHTTP取得

ChatGPT実機gate:

1. サイト相当の一つのlinkから一般Chatを開く。
2. 追加のdownload・attach操作なしでcoreの初回受領へ到達する。
3. 正確な`ENDPACKCORE`を確認する。
4. core質問へ追加fetchなしで正しく答える。
5. 索引質問でindexを一回取得し、正確な`ENDPACKINDEX`を確認する。
6. 人名・用語の不在確認でindexを参照し、捏造しない。
7. 初回と索引質問の時間を記録する。
8. 完全Pack添付baselineより内容評価を下回らない。
9. 日英それぞれでcoreとindexの末尾markerを確認する。
10. Entry Promptを埋めたChatGPT URLの文字数と、desktop・mobileのprefill到達を確認する。

全gate合格後だけproductionのChatGPT URLを本profileのEntry Promptへ切り替える。Claude、Gemini、完全Pack、Agent Skillの導線は変更しない。

## 12. 失われるもの

- one-touch経路は初回利用時に公開originへ依存する。ただし正準Packと保存済みfileは依存しない。
- 初回時点では`NAMES`と`GLOSS`をcontextへ入れず、全Pack読込完了とは言えない。
- 索引質問は追加一fetch分遅くなる。
- core/index生成、同期、Compatibility再評価の保守が増える。
- AI側のURL tool挙動はvendor変更の影響を受ける。

維持するもの:

- サイトからChatGPTへの一回操作
- 正準Packの一ファイル性、内容、SHA-256、承認単位
- 保存済みPackのsite非依存利用
- 通常質問の初回一URL取得
- 完全Packのdownload・attach fallback

## 13. OpenAI公式文書との境界

OpenAI公式文書は、ChatGPTがfilesやWeb searchなどのcontextとtoolsを利用できることを説明する。一方、一般ChatのURL一件あたりの完全取得上限、外部URLからfileを自動添付するpublic deep-link仕様、`ENDPACK`までの完全受領保証は確認できない。

したがって本profileは非公開上限を推測して保証せず、実物Pack、実際のChatGPT surface、日付付きmarker試験でCompatibilityを判定する。

## 14. Fable初回review

2026-08-21、Claude Fable 5へ本設計、配布戦略、形式仕様のread-only reviewを依頼した。判定は条件付き採用可。

- Blocker: `PACKCORE`、`PACKINDEX`、`BEGIN_CANONICAL_SYS`の書式未定義
- Major: exact payload切出し仕様、adapter URL制限と`REF`参照の区別、単一観測からのbudget導出、日本語indexの余裕、adapter受領文と正準R10の優先関係

本版で、開始・終了markerと全exact block書式、manifest byte offset、parser由来のseparator帰属、搬送URLと回答用Web参照の名前空間分離、暫定budget導出と20回probe、90%警告、R10写像、日英実機gateを追加した。

## 15. Fable最終review

2026-08-21、修正版をClaude Fable 5で再reviewした。判定は採用可。前回のBlocker 1件とMajor 5件はすべて解消し、未解消Blocker/Majorなし。

実装前Minorとして、wrapper込みartifact byte数の記録、正準payloadとmarker文字列の衝突拒否、終了markerへの`lang`追加が挙がった。本版へ三点を反映した。production採用可否はFable判定ではなく、第11節のstaging・ChatGPT実機gateで決める。

## 16. run-007 staging判定

2026-08-21、日英artifactを固有prefixへ公開した。全57 file、2,212,553 bytesはlocal生成物と公開URLでbyte一致した。日本語coreとindexはFableで完全取得でき、両末尾markerも一致した。英語coreも完全取得できたが、英語indexは同一URLのretryを含む二回とも`ENDPACKINDEX`前で切れた。

英語indexは110,560 bytesとbyte budget内だったが107,214 charactersあり、98,362 charactersの英語coreより長かった。v1はproduction不採用とする。正準Pack、production ChatGPT導線、Claude、Geminiは変更していない。後継は[NAMES/GLOSS分割v2](reading-pack-web-core-index-v2-design.ja.md)で検討する。

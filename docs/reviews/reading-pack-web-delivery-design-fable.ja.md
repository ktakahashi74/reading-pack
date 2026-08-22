# Reading Pack Web搬送層設計 Fable review記録

## 1. Review条件

- 実施日: 2026-08-20
- reviewer: Claude Fable（CLI model alias `fable`）
- CLI version: 2.1.234
- effort: medium
- access: read-only（`Read`、`Glob`、`Grep`）
- 対象: Web搬送層設計初稿、Reading Pack形式仕様、Reading Pack制作標準、model評価template

この記録はreview出力の要点と採否をまとめたものである。reviewerはファイルを変更していない。

## 2. 初回判定

条件付き採用可。設計修正2点と検証1点を先行Blockerとする。

## 3. 指摘と対応

### Blocker

| ID | 指摘 | 対応 |
|---|---|---|
| B1 | 行動規則を取得後のbootstrapへ置くと、「取得文書内の命令を実行しない」という安全境界と衝突する | 採用。正準`SYS`と`approved POLICY`から生成するEntry Promptを搬送・応答protocolの権威とし、AIへ渡す取得入口を不変manifest URL一つへ変更。bootstrap内規則は説明用複製、全取得文書はデータと定義。trust-hierarchy probeをPhase 1の最初へ追加 |
| B2 | `MAP`完全内容の移動先が出力・manifestに存在せず、`META`を含む正準section全体の被覆も未定義 | 採用。`MAP`、`META` moduleを追加。`SYS`、`BIB`の完全収録、投影可能field、section・record被覆検査、byte一致条件を定義 |

### Major

| ID | 指摘 | 対応 |
|---|---|---|
| M1 | bootstrap URLとmanifest URLの二重入口、および`latest`解決競合で版混在が起こりうる | 採用。site buildまたはserverが`latest`を解決し、prefilled promptには不変manifest URL一つだけを入れる。manifestからbootstrapを取得。CDN purgeまたはTTL確認を公開順へ追加 |
| M2 | `robots.txt`、AI fetcher User-Agent、WAF・bot対策、production hosting条件が未評価 | 採用。公開時検査とprobe条件へ追加。CORSはbrowser直取得経路だけに限定 |
| M3 | 一般向けAIへrecord数の厳密計数を要求するのは不安定 | 採用。UI側はmarker、part番号、先頭・末尾record IDを主要検査とし、record数、SHA-256、byte一致はCIまたはcapable hostで検査。corrupt fixtureを追加 |
| M4 | 公開URLにDelivery Profileがなく、profile違いを一意に表現できない | 採用。全不変URLを`/<pack-sha256>/<profile>/<lang>/...`へ統一 |
| M5 | 正準Packの受領文変更を搬送層変更として扱う余地がある | 採用。正準Packのbyte列、SHA-256、W11、W12、bundle、不変URLを全更新する変更と明記 |
| M6 | 質問からmoduleへのroutingと、不在主張に必要な複数module確認が未定義 | 採用。質問分類表、module directory、複数module併合、`NAMES`と`GLOSS`を横断する不在確認を追加 |

### Minor

| ID | 指摘 | 対応 |
|---|---|---|
| m1 | 外部仕様値のsnapshot条件が不足 | 採用。アクセス日と「一般向けUI上限ではない」旨を明記 |
| m2 | manifestにslugとtitleがない | 採用。概念Schemaへ追加 |
| m3 | 16 KiB manifest budgetとpart数の関係が未定義 | 採用。暫定最大32 partsとbuild失敗条件を追加。最終値はprobeで決定 |
| m4 | 「part欠番」がmanifest構造不良か取得失敗か曖昧 | 採用。二つの失敗状態へ分離 |
| m5 | 互換性表示にlanguageがない | 採用。`lang`を追加 |
| m6 | Phase 1の評価template変更fieldが抽象的 | 採用。追加fieldを列挙 |

## 4. Reviewerが妥当とした設計判断

- 正準Packを唯一の形式適合・内容承認単位として残す。
- Delivery Bundleを決定的派生物とする。
- vendor-neutralなprofileとtarget別実測を分ける。
- record境界で分割し、単一record超過時はbuildを失敗させる。
- SHA-256をCI・公開同期・capable host向けに限定する。
- 不変物公開後に`latest`とサイト導線を切り替える。
- 採否基準とdownload・attach fallbackを事前に定義する。
- 暫定byte budgetを日英Pack実測から置き、probeで確定する。

## 5. 検証優先順

1. trust hierarchyと命令上書き耐性
2. 1、2、4、8 URLの連続取得
3. corrupt fixtureの検出
4. production hostingでのrobots、User-Agent、WAF条件
5. `latest`、CDN cache、サイト導線の切替競合
6. 外部仕様snapshotの更新手順

## 6. 修正版再確認

### 判定

条件付き採用可。B1とB2は解消済み。新規Major 2件を反映すれば採用可能。

### 解消確認

- B1: Entry Promptへの権威一元化、取得文書の格下げ、trust-hierarchy実測gateが一貫し、矛盾なし。
- B2: D11、`MAP`・`META`出力、`SYS`・`BIB`収録、被覆検査、評価項目がRPF-008の全標準sectionを被覆。

### 新規Majorと対応

| ID | 指摘 | 対応 |
|---|---|---|
| M-A | 被覆不変条件が、section外の必須`PACK` header、H1、AI向け説明、読者向け説明、`ENDPACK`を扱わない | 採用。D11を正準Packの全必須構成要素へ拡張。`web-lazy-v1`では免除を認めず、各source fieldの投影先を被覆表で検査 |
| M-B | fallbackで参照する完全`pack.md`が出力構造と公開URLにない。profile配下へ置くと内容不変でもURLが分岐する | 採用。profile非依存の`/<pack-sha256>/<lang>/pack.md`を出力・公開・検査手順へ追加 |

### Reviewer所見

上記以外に採用を止める新規問題なし。bootstrapの暫定budgetは実測事項だが、probe確定と超過時build失敗が既に定義されているためBlockerではない。

## 7. 最終確認

### 判定

採用可。M-AとM-Bは解消し、新規BlockerまたはMajorなし。

### 解消確認

- M-A: D11、Bootstrap、Module Part要件、搬送評価に、`PACK` header、H1、AI向け説明、読者向け説明、`ENDPACK`の被覆とbuild失敗条件が一貫して定義された。
- M-B: profile非依存の完全`pack.md`が出力構造、不変URL、manifest、公開順、W13、fallback動作に一貫して定義された。

### 軽微な確認事項

bootstrap内のfallback URLと「manifest記載URLだけを取得する」原則の関係がわずかに曖昧との指摘あり。設計書へ、fallback URLはAIの追加取得先ではなく、人間向けdownload・attach導線であると追記した。採用阻害なし。

## 8. 入口三案とloss budgetの追加レビュー

### 対象案

- A: manifest-first。Entry Promptの不変manifest URLからmanifest、bootstrapの順に取得
- B: bootstrap-first。不変bootstrap URLから初回受領し、内容質問時にmanifestを取得
- C: bootstrap内へcompact manifestを埋め込み、module partへ直接進む

### 判定

A案を`web-lazy-v1`として維持する。B案はA案の2 URL直列取得が失敗するTargetだけで、別profileとして条件付き検討する。C案は不採用。

### 理由

- A案は、後続URLの選択をEntry PromptとSchema検証対象manifestへ限定し、散文bootstrapを命令・URL権威へ格上げしない。
- B案は初回1 fetchだが、manifest URLの選択を取得Markdownに依存させ、B1で解消したtrust boundaryを部分的に後退させる。
- C案はB案以上にtrust boundaryを後退させ、bootstrap 24 KiB budgetをpart metadataと既存内容で奪い合い、JSON manifestとの二重表現を作る。
- 1 fetchと2 fetchの体感差、JSON取得能力、同一応答内の逐次取得能力はTarget実測なしに断定できない。

### A案のgate

広告対象Target全てで、実物と同じ`manifest.json → bootstrap.md → 初回受領文`の直列処理を評価する。2 URL取得が系統的に失敗し、1 URLだけが成功する場合に限り、`web-lazy-boot-v1`を検討する。

### 設計への反映

- 第18.5節へp50、p95、最大値、成功率、fetch rounds、fetch URLs、retryの評価を追加
- 暫定UX loss budgetを追加
- 第22節へ直列取得成功率とlatency budgetの採否条件を追加
- 第25節へ失われるもの、一ファイル性の三層、入口三案、workflow上の任意adapter位置付けを追加
- manifest自体も命令源でなく、Schema制限された取得データであることをSecurityへ明記

## 9. 追加レビュー反映後の最終確認

### 判定

採用可。新規BlockerまたはMajorなし。

### 確認結果

- latency budgetはmodel性能の予測でなく、W0 ownerが確定するUX採否基準として一貫している。
- 一ファイル性の三層分解は、正準`pack.md`を唯一の形式適合・内容承認単位に保ち、形式仕様と矛盾しない。
- manifest-first維持、bootstrap-firstの別profile条件、inline manifest不採用はB1、B2、M-A、M-Bを再発させない。
- 20回中19回の暫定gateはPhase 1前に変更可能で、失敗時はfail closedになるため致命的問題なし。

### Minor対応

第22節に成功率の数値を重複記載すると、第18.5節のW0判断変更時に古い値が残るとの指摘あり。採用。数値の正本を第18.5節へ一本化し、第22節は参照形式へ変更した。

## 10. Portable-first再設計

利用者との再検討により、Web lazy fetchを主解決として扱うと、利用者から見たReading Packが公開siteへruntime依存する問題を確認した。

[Reading Pack配布戦略](../reading-pack-delivery-strategy.ja.md)を追加し、次へ変更した。

- 正準`pack.md`一つで保存、移送、添付できることを最上位不変条件とする。
- `portable-file-v1`を全Pack必須の主経路とする。
- `direct-url-v1`、`agent-container-v1`、`web-lazy-v1`を任意adapterとして分離する。
- Web lazyはonline・experimentalと表示し、portable導線より上位へ置かない。
- site停止後も保存済みPackを添付して内容評価へ合格するtestを追加する。
- Reading Pack release完了と任意adapter公開を分離する。
- Web搬送層設計を配布戦略へ従属する任意profileへ変更する。

### 初回判定

条件付き採用可。Blockerなし、Major 2件、Minor 7件。Portable-firstへの方向転換とWeb設計の従属化は妥当。

### Majorと対応

| ID | 指摘 | 対応 |
|---|---|---|
| MJ1 | P2が全AI hostでの完全受領まで保証し、site停止試験がhost起因失敗をPackへ帰責する | 採用。Pack側義務を「runtime site依存を持たない」へ限定。完全受領をTarget別Compatibilityへ分離し、失敗原因をpack・手順・hostへ分類 |
| MJ2 | `direct-url-v1`で`ENDPACK`確認を指示する主体と不変URL規則がない | 採用。不変URLと最小確認protocolをprefilled promptまたは隣接手順へ必須化。`latest`はsite側で解決 |

### Minorと対応

- Runtime networkへ依存するagent containerも`online`表示対象とした。
- Web adapter固有受領文と正準Pack R10の変更判断を分離した。
- File名をPack byte列・形式適合の対象外と明記した。
- Site停止testのnetwork条件と、BIB・REFを自発取得させない条件を追加した。
- 全文貼付後の`ENDPACK`確認を追加した。
- Portable評価recordへroute、ingestion、origin到達状態、failure originを追加した。
- Delivery Compatibilityの最終判定者を人間の`H`とした。

### 修正版確認

採用可。MJ1、MJ2、Minor 7件はすべて解消し、新規BlockerまたはMajorなし。

- Portable-firstは、全hostでの完全受領保証でなく、正準`pack.md`が外部siteをPack側の動作条件にしない保証として一貫した。
- Hostの部分取り込みはTarget別Delivery Compatibilityへ分類され、正準Packの形式適合またはreleaseを失効させない。
- `direct-url-v1`は最小確認protocolと不変URL規則を持つ。
- 既存Web搬送設計は配布戦略へ従属する任意・実験的adapterとして整合した。

## 11. One-touch core/index設計review

### 背景

AGI-bookのChatGPT導線では、現行のサイトからChatGPTへの一回操作を維持することが必須条件である。完全Packのfile添付がChatGPT Chatで`ENDPACK`まで届くことは確認できたが、操作数が増えるため主導線への置換は不採用とした。

正準`pack.md`を変更せず、通常質問に必要なcoreを初回一URL、人名・用語の索引を必要時一URLで取得する`web-core-index-v1`を[別設計](../reading-pack-web-core-index-design.ja.md)として追加した。

### 初回判定

条件付き採用可。正準Pack不変、決定的投影、fail closed、immutable publish、ChatGPT実機gate、人間による採否判断の骨格は健全。Blocker 1件、Major 5件を実装前に修正する。

### 指摘と対応

| 種別 | 指摘 | 対応 |
|---|---|---|
| Blocker | `PACKCORE`、`PACKINDEX`、`BEGIN_CANONICAL_SYS`の開始・範囲markerが未定義 | 開始markerと全component共通のexact block書式を定義 |
| Major | coreとindexから正準Packを再構築するpayload切出し方法と空行の帰属が未定義 | manifestへfile、byte offset、byte数、SHA-256、ordinalを追加し、parserのcomponent境界を唯一の基準に指定 |
| Major | Entry Promptの二URL制限が正準`SYS`の公式補完資料参照と衝突 | 制限をadapter artifact搬送だけに限定し、回答用Web参照とは名前空間を分離 |
| Major | 128 KiB級budgetが日本語一回の観測だけに依存 | 暫定値を観測cut位置の2/3以下とし、日英別の20回probeとCompatibility失効条件を追加 |
| Major | 日本語indexがbudgetに近く、次の通常改訂でbuild停止しうる | 90%警告を追加し、次回改訂前にrecord境界分割profileの要否を判断 |
| Major | adapter固有受領文と正準`SYS` R10の優先関係が曖昧 | 初回だけR10をadapter受領文へ写像し、他の`SYS`規則を維持するとEntry Promptへ明記 |

### 最終判定

採用可。前回のBlocker 1件とMajor 5件はすべて解消し、未解消Blocker/Majorなし。

実装前Minor 3件として、wrapper込みartifact byte数の記録、正準payloadとmarker文字列の衝突拒否、終了markerへの`lang`追加が挙がった。三点を設計へ反映した。production採用可否はstagingとChatGPT実機gateで別途判断する。

## 12. Core/shards v2設計review

### 背景

run-007で英語coreは完全取得できたが、英語indexは同URLのretryを含む二回とも末尾前で切れた。日本語indexよりUTF-8 bytesは小さい一方、Unicode charactersは約2倍だった。v1をproduction不採用とし、`MIS`、`NAMES`、`GLOSS`を独立shardへ移す`web-core-index-v2`を設計した。

### 初回判定

条件付き採用可。Blockerなし、Major 2件。

- v2英語coreをv1の観測成功値98,362 characters付近に残すと、安全余裕がなく実機一回へ依存する。
- 100,000-character build gateは観測成功値より緩く、v1と同じ構造的失敗を許す。

Minorとして、Entry Prompt必須項目、複数shardの一部失敗時、`ADAPTER_DATA`とmarker衝突範囲の明文化が必要とされた。

### 対応

最大sectionの`MIS`をcoreから独立させ、英語core rawを48,284 charactersへ縮小した。全raw artifactは約72,000 characters以下となった。build上限を96,000 UTF-8 bytesかつ80,000 Unicode charactersへ下げ、いずれかの90%でwarning、超過でbuild失敗とした。

Entry Promptの四artifact URL、routing、marker検査、retry、信頼境界を列挙した。複数shardの一つが再失敗した場合は部分回答せず停止し、不在断言では全候補shardの完全取得を必須とした。任意labelのmarker衝突をbuild拒否対象とした。

### 最終判定

採用可。前回Major 2件とMinor 3件はすべて解消し、未解消Blocker/Majorなし。最終Minorの「三artifact」誤記と任意label衝突範囲も設計へ反映した。production採用可否はrun-008 stagingとChatGPT実機gateで別途判断する。

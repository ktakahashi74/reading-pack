# Reading Pack配布戦略

## 1. 決定

- 状態: reading-pack 0.6.0実装・run-008 ChatGPT Chat日本語実機合格・Target別production経路採用
- 決定日: 2026-08-20
- 対象: Reading Packの公開、一般向けAIチャットへの受渡し、任意の配布adapter

Reading Packの配布はportable-firstとする。正準`pack.md`一つが必要な収録情報と規則を自己完結して持ち、サイト、manifest、module server、特定vendorのtoolをPack側の動作条件にしないことを最上位要件とする。AI hostが添付または貼付を完全受領できるかは、Target別に実測する。

Portable-firstは、vendor別の一回操作を放棄する決定ではない。公開projectが一回操作を必須UXとして定めたTargetでは、それをadapter採用のhard gateにする。AGI-bookのChatGPT導線では、現行の一回操作を維持し、download・attachを主導線へ置き換えない。

参照実装とAGI-bookのstaging・実機結果は[2026-08-21評価記録](reviews/reading-pack-delivery-staging-20260821.ja.md)に記録する。`direct-url-v1`と`web-lazy-v1`はChatGPT向けproduction導線として不採用。ChatGPTは`web-core-index-v2`、Claude Chatは合格済みの完全Pack直接取得、Geminiは完全Pack download・attachを使う。`portable-file-v1`は全Target共通の保存・fallback経路として残す。

Web adapterはReading Packの標準動作または成立条件としない。特定のDelivery Targetで完全性、操作数、速度に実測合格した場合だけ提供する任意のonline adapterとする。初回一URLと質問別shardで一回操作を維持する案は、[one-touch core/shards搬送設計](reading-pack-web-core-index-v2-design.ja.md)で別profileとして扱う。

## 2. 問題

一般向けAIチャットでは、次の三条件をvendor-neutralに同時達成できない。

1. 利用者操作一回
2. 大きなReading Packの完全受領
3. 外部siteへのruntime非依存

一般に、次の交換になる。

| 選択 | 得るもの | 失うもの |
|---|---|---|
| 一回操作 + 完全受領 | Web lazy fetchまたはvendor固有連携 | site・tool依存 |
| 完全file受渡し + site非依存 | file download・attach | 操作回数。完全受領はhostの添付処理に依存 |
| 一回操作 + site非依存 | prompt内へ収まる小型内容 | 完全な収録情報 |

Reading Packは保存、移送、監査、再利用を重視するため、完全file受渡しとPack側のsite非依存を必須にする。ただし操作一回を一律に最適化目標へ降格しない。公開projectが必須UXに定めたTargetでは、操作一回と完全性の両方をadapterの採用gateとする。Hostがfileを全文contextへ入れるか、retrievalで部分取得するかは形式適合から保証せず、W11でDelivery Compatibilityを実測する。

## 3. 不変条件

### P1. 正準成果物は一ファイル

形式適合、内容承認、Pack SHA-256、license、保存、添付の単位は正準`pack.md`一つである。

### P2. Pack側のruntime site依存を禁止

正準`pack.md`は、公開site、元domain、manifest、CDN、bot serviceの停止によって失効する規則または内容参照を持ってはならない。利用者は保存済み`pack.md`を、公開originを経由せずAI hostへ渡せる。

これは全AI hostでの完全受領または回答品質を保証しない。添付fileを全文contextへ入れるか、retrievalで部分取得するか、添付上限を適用するかはhostの性質である。完全受領は`Target × portable-file-v1`のDelivery Compatibilityとして記録する。

### P3. 正準Packにonline adapter依存を入れない

`SYS`、`POLICY`、初回受領文、内容recordは、Web manifest、module URL、特定domainの取得を動作条件にしてはならない。Adapter用規則は正準Packの外へ置く。

### P4. Adapterは派生物

すべてのDelivery Adapterは正準Packから決定的に生成し、手編集しない。Adapterの欠落、停止、非互換はPackの形式適合、内容承認、利用可能性を失効させない。

Profile名はadapter byte契約の版である。同じPack SHA-256とprofileの不変URLを異なるbyte列で上書きしない。Prompt、wrapper、manifest、投影規則を変える場合はprofileを上げる。

### P5. Adapter依存を利用者へ明示

Runtimeにnetwork、domain、vendor toolを必要とする経路を「Reading Packそのもの」「offline」「portable」と表示しない。

### P6. 完全Packへの脱出路

すべてのonline adapterは、同じPack SHA-256の完全`pack.md`をdownload・attachする経路を提示する。

## 4. 配布mode

### 4.1 `portable-file-v1` — 必須

成果物:

```text
<basename>.<lang>.md
```

利用手順:

1. `pack.md`を保存する。
2. AI chatへfileとして添付する。file inputがない場合だけ、`ENDPACK`まで全文貼付し、送信後に末尾`ENDPACK`が受領されたことを確認する。
3. 初回受領文を確認して質問する。

公開する全Reading Packで必須。siteは取得場所の一つにすぎず、runtime componentではない。

### 4.2 `direct-url-v1` — 任意

AIへ不変な完全`pack.md` URLを一回取得させる。導線はURLだけでなく、末尾`ENDPACK`、Pack版、言語を確認し、不足時は一回再取得後にdownload・attachを案内する最小protocolをprefilled promptまたは隣接手順として伴う。

途中切り詰めを検出した場合、再取得を一回だけ試し、再失敗時はdownload・attachへ移る。URL取得成功をfile添付成功と同一視しない。

`latest`はsite buildまたはserverが導線生成時に解決する。AIへ渡すのはPack SHA-256を含む不変URL一つだけとし、AI自身に`latest`を解決させない。

### 4.3 `agent-container-v1` — 任意

Agent Skill、project knowledge、vendor固有containerなどへ正準Packを格納する。導入時にdownloadまたはinstallを必要としてよいが、導入後の質問ごとにReading Pack公開siteへ依存しない形を優先する。

ContainerはPackそのものや新しい内容承認単位ではない。`online`表示義務はcontainerの名称や形式でなく、質問時に公開originまたは外部serviceへ接続するruntime network依存の有無で決める。

### 4.4 `web-lazy-v1` — 任意・実験的

Entry Prompt、manifest、bootstrap、module partsを使うonline adapter。完全Pack URL取得が切り詰められ、かつ同一応答内の複数URL取得が安定するTargetだけで利用する。

Runtimeに公開originを必要とする。したがってportable経路の代替ではなく、online convenience pathとする。

### 4.5 `web-core-index-v1` — 任意・実験的

一回の完全Pack URL取得が末尾で切れる一方、coreとindexを個別には完全取得できるTarget向けのonline adapter。初回は`SYS`、章、主張、論点、方針、参照、版情報を含むcore一URLだけを取得し、人名・用語質問でだけ`NAMES`と`GLOSS`を含むindex一URLを追加取得する。

利用者操作は一回、初回取得も一URLに保つ。正準Packは変更せず、coreとindexは決定的派生物とする。run-007で英語indexが二回とも末尾前で切れたためproduction不採用。記録は[one-touch core/index v1設計](reading-pack-web-core-index-design.ja.md)に残す。

### 4.6 `web-core-index-v2` — 任意・ChatGPT production採用

v1の英語切断を受け、UTF-8 byte数とUnicode character数を同時に制限する。初回coreから大きい`MIS`を外し、`MIS`、`NAMES`、`GLOSS`を質問別shardへ分ける。core、mis、names、glossのexact payloadをmanifest順に連結すると、正準Packとbyte一致する。

サイトからChatGPTへの一回操作と初回一URLは維持する。通常の反証、人名、用語質問は追加一URL、横断質問と不在確認は必要な遅延モジュールを並列取得する。`MIS`、`NAMES`、`GLOSS`は搬送上の遅延モジュールであり、本書の付録ではない。設計と採否条件は[one-touch core/遅延モジュール v2設計](reading-pack-web-core-index-v2-design.ja.md)に従う。

### 4.7 AGI-bookのTarget別経路

| Target | production主導線 | 理由 |
|---|---|---|
| ChatGPT Chat | サイトがEntry Promptを先読みしてinline prefillし、ChatGPTはcore一URLから開始 | 完全Pack直接取得は末尾欠落。run-008日本語実機は3秒以内でcoreと三遅延モジュールに完全合格 |
| Claude Sonnet 5 Chat | 完全Pack URLを直接取得 | 利用者実機で完全Pack末尾まで合格。一ファイル完結をそのまま活用 |
| Gemini | 完全Packをdownloadしてfile添付 | Gemini 3.1 Proの直接経路は不合格、3.7 Flashのfile経路は合格。完全性を優先 |

ChatGPTのサイトbuttonは一回操作を維持する。ChatGPT自身へEntry Prompt URLを取得させる二段方式はrun-009で安全制約により失敗したため使わない。ClaudeとGeminiへChatGPT用adapterを一律適用しない。

本書の付録・公式補完資料は、coreに残る`REF`と`SYS C1`を起点に回答時取得する。これは`MIS`、`NAMES`、`GLOSS`の遅延取得とは別経路である。

## 5. 公開UI

### 5.1 Portable導線

正準Packの保存・移送用actionを次とする。

> Reading Packをダウンロード（.md）

隣接して、短い手順を表示する。

> ダウンロードしたファイルをAIチャットへ添付し、読み込み後に質問してください。

「ChatGPTで読む」「Claudeで読む」などのvendor別導線を出す場合も、portable-file経路を隠さない。公開projectがvendor別導線の一回操作を必須にしている場合、未確認のdownload・attach置換でそのUXを失わせない。

### 5.2 Online adapter表示

`direct-url-v1`、`web-lazy-v1`、`web-core-index-v2`のいずれかを広告する場合、少なくとも次を表示する。

- `online`または`experimental`
- 対応確認日
- 対象product・surface・model・route
- 取得失敗時のdownload・attachリンク

単に「対応」と表示せず、日付付きDelivery Compatibilityとして表示する。

### 5.3 操作数の扱い

操作数はproject・Target別のW0制約とする。完全取得できない一回操作を合格扱いせず、同時に、必須とされた一回操作をdownload・attachへ無断で置き換えない。両方を満たすadapterが無ければproductionを変更せず、実験経路をstagingに留める。

Browserから一操作でdownloadとAI chat起動を開始できる場合でも、file添付が完了したとは表示しない。添付経路では利用者によるattach確認を残す。

## 6. Workflow

### W0 設計制約

公開前に次を決める。

- 必須の`portable-file-v1`公開先
- mirrorまたはrelease assetの有無
- 広告する任意adapter
- Adapterが失敗した場合のUI
- Delivery Compatibilityの更新条件

### W10 組立

正準Packを先に完成させる。任意adapterは正準Packのfreshnessとbyte一致を確認した後に生成する。

### W11 評価

内容評価は`portable-file-v1`を基準にする。任意adapterは同じ内容評価に加えて搬送完全性、latency、runtime依存を評価する。Delivery CompatibilityはW11の拡張として扱い、automationが証拠を集計しても、最終判定は人間の`H`が行う。

### W12 著者レビュー

著者が承認する内容単位は正準Packとする。Adapterだけの変更で内容reviewを再要求しない。ただしAdapterが内容を投影、要約、routingする場合、その意味差分はDelivery reviewで確認する。

### W13 公開

正準`pack.md`を先に公開・検証する。任意adapterの失敗を理由に正準Pack公開を取り消さない。Adapterだけを非公開またはfallbackへ戻せるようにする。

## 7. 評価と採否

### 7.1 Site停止試験

次を必須testとする。

1. 公開済み`pack.md`をlocalへ保存する。
2. Test環境からReading Pack公開originだけをhostsまたはnetwork policyで到達不能にする。AI hostへの接続は維持し、BIB・REFのURLを自発取得させない。
3. 保存済みfileをAI hostへ添付する。
4. 初回受領、所在、全件列挙、不在確認、規範／記述、資料外質問を評価する。

結果を`pack起因`、`手順起因`、`host起因`へ分類する。正準Packがsite取得を要求した場合だけPack側のportable-first不適合とする。添付の部分取り込み、context上限、retrieval漏れなどのhost起因失敗は、当該`Target × portable-file-v1`のDelivery Compatibility不合格として記録し、正準Packの形式適合またはreleaseを失効させない。

### 7.2 Adapter採用条件

任意adapterは次をすべて満たす場合だけ広告する。

1. `portable-file-v1`が公開済みである。
2. Adapterが正準Packと同じPack SHA-256へ結び付く。
3. 不完全取得を完全取得として扱わない。
4. 内容評価がportable-file baselineを下回らない。
5. Target別のlatency・成功率budgetを満たす。
6. Adapter停止時にdownload・attachへ戻れる。

### 7.3 Release完了条件

Reading Pack releaseは正準`pack.md`の生成、検査、承認、公開で完了できる。任意adapterは別の公開状態として記録する。

「Web adapter未対応」と「Reading Pack未公開」を同義にしない。

## 8. 可用性と長期保存

- Pack SHA-256を含むfile名またはrelease metadataを提供する。File名はPack byte列および形式適合の対象外であり、mirrorのbyte一致はfile内容に対して判定する。
- project site以外のpublisher site、release asset、repository、archiveへのmirrorを許可する。
- mirrorは同じbyte列とSHA-256を配布し、独自のPack版を作らない。
- Adapter URLの長期可用性をPack適合条件にしない。
- 公開終了時も、正準Packの取得先またはarchive情報を可能な範囲で残す。

複数mirrorは取得可能性を高めるが、runtime site非依存そのものではない。portable-firstの根拠は、利用者が一度取得した単一fileだけで動作する点に置く。

## 9. 現行と変更後

| 対象 | 現行 | 本戦略 |
|---|---|---|
| 正準成果物 | 一つのMarkdown | 変更なし |
| 基本利用 | 添付または全文貼付 | `portable-file-v1`として明文化 |
| 完全Pack URL | 実測外。途中欠落の危険 | `direct-url-v1`としてTarget別評価 |
| Web lazy | 実験prototype実装・staging不採用 | 任意・実験的adapter |
| ChatGPT | 完全Pack直URLで末尾欠落 | core一URL + 質問時の遅延モジュール。one-touch維持 |
| Claude Chat | 完全Pack直URL | 合格Targetでは維持 |
| Gemini | product・model依存 | 完全Pack download・attach |
| release完了 | Pack公開 | 変更なし。Adapterは別状態 |
| site停止後 | 保存済みPackなら受渡し可能 | Pack起因とhost起因を分ける必須test |

## 10. 実装順

### Phase 1: Portable経路の固定 — 実装済み

- `route=portable-file`、`ingestion=attached|pasted`、`origin_reachable=false`、`failure_origin=pack|procedure|host`を持つ評価record追加
- site停止test追加
- 公開UI文言とdownload導線の確認
- 完全PackのSHA-256表示・mirror方針確認

### Phase 2: Direct URL評価 — ChatGPT不合格

- `direct-url-v1`の末尾marker probe
- ChatGPT、Claude、GeminiのTarget別評価
- 合格Targetだけonline導線を有効化

### Phase 3: Container評価 — 継続課題

- Agent Skill、project knowledge、file input adapterの比較
- 導入後のsite非依存性確認

### Phase 4: Web lazy prototype — 実装・staging評価済み、production不採用

- `direct-url-v1`が失敗するTargetだけを候補にする
- Web搬送層設計のPhase 1 probeを実施
- portable導線より上位へ表示しない

### Phase 5: Core + 遅延モジュール — 実装・ChatGPT production採用

- `web-core-index-v2`を決定的生成し、正準Packとの全byte再構築を検査
- run-008で日本語ChatGPT Chat実機合格、日英Fable 5合格
- run-009の二段Entry Prompt取得は失敗証拠として固定し、productionではinline prefillを採用
- 完全Pack download・attachをfallbackとして維持

## 11. 推奨判断

Reading Packの根本価値を、一つの自己完結したfileとして保存・移送・添付できる点に置く。一般向けAI製品のURL取得制限を解決するために、この性質をonline serviceへ置き換えない。

完全Pack URL取得、Agent Container、Web lazy fetch、core + 遅延モジュールは、いずれも正準Packの外側に置く配布adapterである。Targetごとに合格した最小経路を使い、Packの成立条件にはしない。

## 12. Review

本戦略と[Web搬送層設計](reading-pack-web-delivery-design.ja.md)の整合についてFable reviewを実施した。Major 2件とMinor 7件を反映し、修正版の最終判定は採用可。実装採用は各Phaseの実測gateを満たすことを条件とする。

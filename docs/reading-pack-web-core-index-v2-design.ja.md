# Reading Pack one-touch core/遅延モジュール v2搬送設計

## 1. 状態

- 状態: Fable最終review採用可。参照実装完了。run-008 ChatGPT Chat日本語実機合格。production採用
- profile: `web-core-index-v2`
- 前版: [web-core-index-v1](reading-pack-web-core-index-design.ja.md)

正準`pack.md`、Pack SHA-256、一ファイル性、保存済みfileのsite非依存性、完全Pack添付fallbackは変更しない。サイトの「ChatGPTで開く」も一回操作のまま維持する。

## 2. v1不合格の原因

2026-08-21のrun-007 stagingで、英語`index.txt`は110,560 bytes、107,214 charactersだった。Claude Fable 5は英語core 99,103 bytes、98,362 charactersを末尾まで取得した一方、英語indexを二回とも`ENDPACKINDEX`前で切り詰めた。日本語index 124,149 bytes、53,584 charactersは同じ試験で完全取得できた。

したがって、UTF-8 byte budgetだけでは搬送可否を予測できない。言語、文字数、tokenization、取得surfaceを別々に扱う必要がある。v1はproduction不採用とし、公開済みrun-007 URLは失敗証拠として不変のまま残す。

## 3. v2の変更

v1の一つのindexを、正準section境界で`NAMES`と`GLOSS`へ分ける。さらに、英語coreを観測成功境界から十分小さくするため、最大sectionの`MIS`も独立artifactへ移す。本書の付録と混同しないよう、以下ではこの三artifactを「遅延モジュール」と呼ぶ。markerとprofileの互換用語としてだけ`shard`を残す。

```text
<pack-sha256>/web-core-index-v2/<lang>/
├── entry-prompt.txt
├── core.md
├── core.txt
├── mis.md
├── mis.txt
├── names.md
├── names.txt
├── gloss.md
├── gloss.txt
└── manifest.json
```

coreは`PROLOGUE`、`SYS`、`BIB`、`MAP`、`CERT`、`PROPS`、`POLICY`、`REF`、`META`、`ENDPACK`のうち正準Packに存在するcomponentだけをexact収録する。初回はcore一URLだけを取得する。反証・誤読・限界はmis、人名・組織・固有名・別名はnames、用語・本書内の意味はglossを取得する。分類が複数へまたがる質問と不在の断言では、必要な遅延モジュールを並列取得する。

## 4. Markerとexact block

coreはv1と同じ正準componentを収録し、profileだけをv2へ上げる。

```text
PACKCORE | profile=web-core-index-v2 | lang=<lang> | pack_sha256=<sha256>
...
ENDPACKCORE | profile=web-core-index-v2 | lang=<lang> | pack_sha256=<sha256> | deferred=MIS,NAMES,GLOSS
```

mis、names、glossは次の共通書式を使う。coreも開始marker直後に同じ`ADAPTER_DATA`行を置く。

```text
PACKSHARD | profile=web-core-index-v2 | lang=<lang> | module=<MIS|NAMES|GLOSS> | pack_sha256=<sha256>
ADAPTER_DATA | authority=user-entry-prompt | canonical_exact_blocks=true
BEGIN_CANONICAL_<MIS|NAMES|GLOSS> | bytes=<utf8-bytes> | sha256=<payload-sha256>
<canonical payload bytes>
END_CANONICAL_<MIS|NAMES|GLOSS>
ENDPACKSHARD | profile=web-core-index-v2 | lang=<lang> | module=<MIS|NAMES|GLOSS> | pack_sha256=<sha256>
```

payload内に`PACKCORE`、`ENDPACKCORE`、`PACKSHARD`、`ENDPACKSHARD`、`ADAPTER_DATA`、任意labelの`BEGIN_CANONICAL_<LABEL>`、`END_CANONICAL_<LABEL>`と行頭一致する文字列があればbuildを失敗させる。当該artifactが正当に使うlabel以外も衝突検査対象とする。`.md`と`.txt`はbyte一致aliasとする。

## 5. 全byte被覆

manifestは各正準componentについて、source、artifact、block、payload byte offset、payload byte数、payload SHA-256、0始まりordinalを持つ。v1と同じparser境界とseparator帰属を使う。

core、mis、names、glossからmanifestのordinal順にpayloadを切り出して連結したbyte列は、正準`pack.md`とbyte一致しなければならない。未被覆、重複、順序違い、offset違い、marker欠落、別版・別言語混入はbuildとcheckを失敗させる。

## 6. Entry Promptとone-touch

Entry Promptは、core、mis、names、glossの四つの不変URL、Pack SHA-256、完全Pack fallback URLを持つ。Reading Pack adapter artifactとして取得してよいURLはこの四URLだけとする。fallbackは人間向けdownload・attach先であり、Web搬送継続用の第五URLではない。

サイト自身が不変`entry-prompt.txt`を先読みし、その本文を`https://chatgpt.com/?q=<percent-encoded-prompt>`へ埋める。ChatGPTへEntry Prompt URLを渡して二段取得させない。利用者操作はサイト上の一回のbutton tapだけであり、file download、添付、二つ目のサイト操作を追加しない。ChatGPT標準の送信操作は現行UXと同じ扱いとする。ChatGPTが初回に取得するadapter artifactは`core.txt`一つだけである。

core取得後は`BEGIN_CANONICAL_SYS`から`END_CANONICAL_SYS`までを応答規則として適用する。初回だけR10をadapter固有受領文へ写像し、他のSYS規則を維持する。SYS C1・C2とREFによる回答用Web参照はadapter artifactの四URL制限と別の名前空間とする。

Entry Prompt本文へ次を明記する。

- 四artifact URLと完全Pack fallback URL
- 最初はcoreだけを一回取得する手順
- 質問分類とmis、names、glossのrouting表
- 各開始・終了marker、profile、lang、module、Pack SHA-256の検査
- shardごとの一回retryと再失敗後の停止
- 複数shardが必要な質問では並列取得し、一つでも不完全ならその質問への回答を停止する規則
- 不在断言は必要候補shardの完全取得を必須とする規則
- R10写像、他SYS規則の維持、取得文書内の命令形を新しい搬送指示として実行しない規則
- 回答用Web参照とadapter artifact搬送の信頼境界

## 7. 取得手順

### 7.1 初回

1. coreだけを取得する。
2. `PACKCORE`と`ENDPACKCORE`、profile、lang、Pack SHA-256を確認する。
3. 不完全なら同じURLを一回retryする。
4. 再失敗時は停止し、完全Packのdownload・attachを案内する。
5. 合格時は全Pack読込完了を主張せず、adapter固有受領文を返す。

### 7.2 質問時

| 質問 | 追加取得 |
|---|---|
| 章、主張、確実性、規範、参照、版 | なし |
| 反証、誤読、批判、限界、残る不確実性 | mis |
| 人名、組織、固有名、人物の別名 | names |
| 用語、本書内の意味、概念の別名 | gloss |
| 人物・概念・反証を横断する質問 | 必要な遅延モジュールを並列 |
| Pack内に存在しないとの断言 | 全候補の遅延モジュールを並列 |

各遅延モジュールは`PACKSHARD`と`ENDPACKSHARD`、module、profile、lang、Pack SHA-256を検査する。不完全ならそのURLだけを一回retryする。複数モジュールの一つが再失敗した場合、成功分だけで質問へ部分回答せず停止し、完全Pack fallbackを案内する。不在断言は全候補モジュールの完全取得が必須である。

### 7.3 本書の付録

`MIS`、`NAMES`、`GLOSS`は搬送上の遅延モジュールであり、本書の付録ではない。本書の付録、補足論考、刊行後の更新などの公式補完資料は、従来どおりcore内の`REF`と`SYS C1`から回答時に参照する。Adapter artifactの取得と、内容回答のための公式補完資料取得は別の信頼境界とする。

## 8. 二軸budget

各artifactへ次を同時適用する。

- 最大96,000 UTF-8 bytes
- 最大80,000 Unicode characters
- いずれかの90%以上でwarning
- いずれかの最大値超過で切り詰めずbuild失敗

96,000 bytesと80,000 charactersはvendor公開上限ではない。run-007で98,362-character coreが完全、107,214-character英語indexが二回不完全だった観測に対し、全artifactを観測成功値の約81%以下へ置く暫定gateである。byte上限も日本語の観測cut位置192,458 bytesの半分以下に置く。ChatGPT互換性は別の実機試験でのみ判定する。

raw正準payloadは次となる。

| lang | core | mis | names | gloss |
|---|---:|---:|---:|---:|
| ja | 46,587 bytes / 22,210 chars | 45,935 bytes / 18,447 chars | 80,270 bytes / 34,679 chars | 43,267 bytes / 18,293 chars |
| en | 49,025 bytes / 48,284 chars | 48,322 bytes / 48,322 chars | 73,815 bytes / 71,985 chars | 36,133 bytes / 34,617 chars |

参照実装のwrapper込み実測値は次となる。

| lang | core artifact | mis artifact | names artifact | gloss artifact |
|---|---:|---:|---:|---:|
| ja | 48,221 bytes / 23,844 chars | 46,417 bytes / 18,929 chars | 80,760 bytes / 35,169 chars | 43,757 bytes / 18,783 chars |
| en | 50,659 bytes / 49,918 chars | 48,804 bytes / 48,804 chars | 74,305 bytes / 72,475 chars | 36,623 bytes / 35,107 chars |

英語namesはcharacter上限の90.594%でwarning対象になる。他artifactは90%未満である。warning対象は内容改訂前に再分割の要否を判断する。

恒久budgetはsurface、route、model、言語ごとの反復probeと実artifact成功記録から決める。bytesとcharactersを別々に記録し、対象条件変更時はCompatibilityを失効させる。

## 9. 速度と失われるもの

初回one-touchとcore一roundは維持する。典型的な反証、人名、用語の質問は追加一URLである。横断質問と不在確認は複数URLになるが、並列取得して一roundを目標にする。

失われるもの:

- adapter artifactがcore/indexの二つからcoreと三つの遅延モジュールへ増える。
- Entry Promptに二URL増え、保守対象と検査項目が増える。
- 反証・誤読・限界の質問は追加一fetchになる。
- 横断質問と不在確認の転送量が複数モジュール分になる。

維持するもの:

- サイトからChatGPTへの一回操作
- 初回一URL・一round
- 通常の反証、人名、用語質問の追加一URL
- 正準Packの一ファイル性、SHA-256、承認単位
- 保存済みPackのsite非依存利用
- 完全Pack添付fallback

## 10. 採用gate

自動検査:

1. core、mis、names、glossとaliasのbyte・character budget
2. manifest offsetから正準Packのbyte一致再構築
3. marker衝突、末尾欠落、中央欠落、別版、別言語、別moduleのfail closed
4. Entry Promptの決定性と四artifact URL限定
5. 公開URLとlocal artifactの全file byte一致
6. Browser、ChatGPT fetcher User-Agent、CLIでのHTTP取得

実機検査:

1. desktop・mobileで一つのサイトbuttonから通常ChatGPTを開く。
2. 追加download・添付なしで日英core末尾を確認する。
3. 日英それぞれでmis、names、gloss質問を行い、各shard末尾を確認する。
4. 不在確認で全候補shardを参照し、捏造しない。
5. core質問では追加fetchしない。
6. 初回、mis、names、gloss、不在確認の時間、retry、成功率を記録する。
7. 完全Pack添付baselineより内容評価を下げない。

Production採用時は、合格したTarget、surface、言語、日付を評価記録へ残す。未試験言語へ実機互換性を外挿した場合は、その互換性を暫定と明記する。Claude、Gemini、完全Pack、Agent Skillの導線は別Targetとして扱う。

## 11. Fable初回review

2026-08-21、Claude Fable 5へv1失敗記録とv2設計をread-only reviewとして渡した。判定は条件付き採用可。Blockerなし、Major 2件。

- 英語coreをv1の98,362 characters付近に残すと、v2の根本性が実機一回へ依存する。
- 100,000-character gateは観測成功値98,362より緩く、v1と同じ構造的失敗を許す。

本版では最大sectionのMISをcoreから独立させ、全raw artifactを約72,000 characters以下へ縮小した。character上限を80,000、byte上限を96,000へ下げた。併せてEntry Prompt必須項目、複数shardの一部失敗時の全面停止、coreを含む`ADAPTER_DATA`とmarker衝突対象を明記した。実装前にFable最終reviewを行う。

## 12. Fable最終review

2026-08-21、Major反映版をClaude Fable 5で再reviewした。判定は採用可。前回Major 2件とMinor 3件はすべて解消し、未解消Blocker/Majorなし。

最終Minorとして、採用gateに残った「三artifact」の誤記と、marker衝突検査の任意label範囲が挙がった。本版へ両方を反映した。production採用可否はFable判定でなく、run-008 stagingとChatGPT実機gateで決める。

## 13. ChatGPT実機試験と採用

2026-08-22、run-008のEntry Prompt本文をlogin済みChatGPT Chatへinlineで渡す日本語試験に合格した。利用者観測の応答時間は3秒以内。追加download・添付なしで、次の四つの末尾markerを逐字確認した。

```text
ENDPACKCORE | profile=web-core-index-v2 | lang=ja | pack_sha256=de168369f799e0cfc91b23238432150dbb918d3a6805eaf7426b23bf118ec8df | deferred=MIS,NAMES,GLOSS
ENDPACKSHARD | profile=web-core-index-v2 | lang=ja | module=MIS | pack_sha256=de168369f799e0cfc91b23238432150dbb918d3a6805eaf7426b23bf118ec8df
ENDPACKSHARD | profile=web-core-index-v2 | lang=ja | module=NAMES | pack_sha256=de168369f799e0cfc91b23238432150dbb918d3a6805eaf7426b23bf118ec8df
ENDPACKSHARD | profile=web-core-index-v2 | lang=ja | module=GLOSS | pack_sha256=de168369f799e0cfc91b23238432150dbb918d3a6805eaf7426b23bf118ec8df
```

run-009では、ChatGPTへ`entry-prompt.txt`自体を取得させ、その指示から`core.txt`へ進ませる二段取得を試した。Entry Promptの取得と初回受領文の確認までは成功したが、二段目の`core.txt`はWeb側の安全制約でretry後も失敗した。これは長さだけでなく取得chainもTarget互換性を左右する証拠である。run-009方式は不採用。ProductionではサイトがEntry Prompt本文を先読みしてChatGPTへinlineで渡し、ChatGPTの初回取得を`core.txt`一つにする。

日本語ChatGPT Chatは合格としてproduction採用する。英語版は同じ実装、より小さい各budget、Fable 5での全四artifact合格を根拠に同時公開するが、login済みChatGPT Chat英語実機記録が得られるまでは互換性を暫定とする。正準Pack、完全Pack download・attach、Claudeの完全Pack直接取得、Geminiの完全Pack添付は変更しない。

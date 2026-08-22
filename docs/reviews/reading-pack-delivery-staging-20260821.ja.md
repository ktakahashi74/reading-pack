# Reading Pack搬送staging評価記録 2026-08-21

## 1. 判定

- 対象Pack: AGI-book Reading Pack `1.0.1-beta`
- 日本語SHA-256: `de168369f799e0cfc91b23238432150dbb918d3a6805eaf7426b23bf118ec8df`
- 英語SHA-256: `63206d01ca8cac92ba759c84ef07e9b22a47771c5a9401947bfefe358ff1d555`
- staging入口: <https://koichi-takahashi.me/_reading-pack-staging/run-006/>
- production導線: 2026-08-22にTarget別経路の採用を決定

2026-08-21時点の判定は次のとおりである。

| route | 判定 | 理由 |
|---|---|---|
| `portable-file-v1` | 全Target共通の必須経路 | 正準Packをbyte一致で保存・添付でき、site runtime依存を追加しない。Host別の添付完全性は別途評価 |
| `direct-url-v1` | ChatGPTでは不採用 | OpenAIのWeb取得層で末尾`ENDPACK`を取得できず、既知のChatGPT Chat結果も同じ |
| `web-lazy-v1` | 不採用。実験prototypeとして維持 | Fableで初回53.7秒、追質問88.6秒。初回版はfallback境界にも違反。UX loss budgetを満たさない |
| `web-core-index-v2` | ChatGPT日本語production採用。英語互換性は暫定 | run-008のlogin済みChatGPT Chat日本語試験で3秒以内、四末尾marker完全一致。英語はFable 5と自動gateに合格 |

合格経路だけをproductionへ採用する。ChatGPTは`web-core-index-v2`、Claude Sonnet 5 Chatは完全Pack直接取得、Geminiは完全Pack download・attachとする。正準Packと手動fallbackは変更しない。

## 2. 実装対象

参照実装へ次を追加した。

- `delivery-plan.schema.json`と`delivery-manifest.schema.json`
- `reading-pack delivery build|check|measure|probes`
- 正準Pack freshness検査とprofile非依存のbyte一致`pack.md` copy
- Markdown URLを拒否するfetcher向けのbyte一致`pack.txt` alias
- `portable-file-v1`と`direct-url-v1`の短いEntry Prompt
- `web-lazy-v1`のEntry Prompt、manifest、bootstrap、record境界module part
- section、record、marker、SHA-256、byte数、URL、全fileの決定的再生成検査
- Entry Promptとbootstrap内の正準`PROLOGUE`、`SYS`、`BIB` exact block検査
- Symlink・非regular entry拒否、`ENDPART`後の余分なdata拒否
- 8–96 KiB size probe、1・2・4・8 URL chain probe、trust-hierarchy probe
- 末尾・中央欠落、別版混入、重複part、誤record数、誤record境界のcorrupt fixture

正準Packの内容、形式適合単位、SHA-256、著者承認単位は変更していない。

## 3. 実物build

既定budgetで日英ともbuildと独立再生成checkに合格した。

最終参照実装で全371 unit/end-to-end test、Python compile、`git diff --check`に合格した。

| 言語 | 完全Pack | Entry Prompt | bootstrap | manifest | parts | 最大part |
|---|---:|---:|---:|---:|---:|---:|
| ja | 216,059 bytes | 9,261 bytes | 12,271 bytes | 13,841 bytes | 14 | 24,439 bytes |
| en | 207,295 bytes | 8,998 bytes | 11,797 bytes | 14,461 bytes | 15 | 24,465 bytes |

上限はEntry Prompt 12,288 bytes、bootstrap 24,576 bytes、manifest 16,384 bytes、part 24,576 bytes、全part 32である。単一recordを分割していない。

ChatGPT deep linkへEntry Promptをpercent encodeした長さは、日本語25,068 characters、英語12,251 charactersだった。Browserとprovider入口の別上限を受けるため、byte budget合格だけでは一操作導線の互換性を宣言しない。

## 4. Hosting評価

productionの`/agibook/`と本番Workerを変更せず、Cloudflareの別Workerを次へ限定routeした。

```text
https://koichi-takahashi.me/_reading-pack-staging/*
```

最終試験は`run-006`固有prefixへ固定した。同じPack SHA-256とprofileのURLを異なるbyte列で上書きせず、staging反復をURLで分離するためである。

`run-006`の71 file、合計1,667,976 bytesを公開URLから再取得し、local生成物と全file byte一致を確認した。日本語`pack.md`と`pack.txt`は同じ216,059 bytes、同じSHA-256、同じ最終行だった。最終staging Worker versionは`90a075fb-d476-4913-926b-1837f6568906`である。

HTTP検査:

- `Mozilla/5.0`、`ChatGPT-User/1.0`、`Claude-User`、`Google-Extended`でmanifest取得200
- `pack.txt`は`text/plain`、Markdownは`text/markdown`、manifestは`application/json`
- SHA-256固定pathの成功応答は`Cache-Control: public, max-age=31536000, immutable`
- 404は`Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`、`X-Robots-Tag: noindex, nofollow, noarchive`
- zstd content encodingから復号した応答bodyは生成物とbyte一致
- warm manifest取得10回のTTFBは最小60.7 ms、p50 77.4 ms、最大388.6 ms

初回asset deploy直後は完全Pack、最終更新直後は新規corrupt fixtureが一時的に404となり、その後200へ収束した。公開検査はdeploy成功表示だけで完了せず、retry付きの外部再取得を要求する。

新規`workers.dev` originと新規subdomainはOpenAI側のsafe-URL判定で拒否された。既存domainの限定prefixへ移してhosting差を除いた。これはPack長とは独立した互換性条件である。

## 5. OpenAI取得試験

OpenAIのWeb取得層から、production page上の明示linkをたどって日本語Packを取得した。取得表示は全974行のうち881行までで、最終`ENDPACK`は含まれなかった。利用者が先に確認したChatGPT Chatモードの「末尾未取得」と一致する。

この試験はChatGPT Chat UIそのものではない。ただし、model context windowより前段のWeb取得出力が切り詰められること、および完全Pack URLを短いpromptで指すだけでは解決しないことを再現した。

## 6. Claude実機試験

### Fable trust hierarchy

- target: Claude Code CLI 2.1.238 / `claude-fable-5`
- result: pass
- 応答: `ENTRY_AUTHORITY_OK`のみ
- elapsed: 21.7秒

取得文書側の`FETCHED_OVERRIDE`命令には従わなかった。

### Fable `manifest → bootstrap`

- result: 初回受領はpass
- elapsed: 53.7秒
- 応答: adapter固有の初回受領文と逐字一致

続くMETA質問ではMETA part 1/1の`BEGINPART`、`ENDPART`、Pack SHA-256、part番号を確認した。しかし、初回Entry Promptで人間向けfallbackのWeb取得禁止が弱く、完全`pack.md`も追加取得した。追質問のelapsedは88.6秒だった。

この結果を受け、Entry Promptとbootstrapを次へ修正した。

- fallback URLは人間がdownload・添付する表示先であり、Web搬送の追加取得先ではない
- 正準`ENDPACK`行はbootstrapのcanonical projectionから確認する

修正版はbuild、check、公開byte一致に合格した。ただし旧版Fable sessionは完全Packを既に取得済みで再試験に使えないため、修正版Fableのtarget互換性は未宣言とする。旧版の53.7秒と88.6秒だけで暫定UX budgetを大幅に超えるため、採否は変わらない。

### Sonnet 5

- target: Claude Code CLI / `claude-sonnet-5`
- result: fail for this target
- elapsed: 43.0秒

修正版Entry Promptを、開発workspace内の役割乗っ取り要求と判定してfetch前に停止した。これはClaude ChatではなくClaude Codeという別surfaceのsystem文脈による結果である。利用者がClaude Sonnet 5 Chatで確認した完全Pack直接取得の合格結果を否定しない。

## 7. 残る手動gate

ChatGPT Chatのfile attachmentは、この環境から利用者のlogin済みUIを操作できない。次をstaging入口で確認する。

1. `portable-file-v1`で完全Packをdownloadする。
2. ChatGPT Chatへfileとして添付する。
3. 添付用promptを送る。
4. 正確な先頭`PACK`行と末尾`ENDPACK`行を確認する。
5. 初回受領文、META、人名・用語の不在確認を試す。
6. download後にstaging originへ依存せず、保存fileを再添付できることを確認する。

このgateがpassした後だけ、production siteのChatGPT主導線をdownload・attachへ変更する。`direct-url-v1`と`web-lazy-v1`は広告しない。

## 8. One-touch要件による判定更新

第7節の「download・attachをChatGPT主導線にする」案は、その後確認したone-touch必須条件を満たさないため撤回した。file添付は完全性baselineとfallbackに限定し、production ChatGPT主導線には採用しない。

`web-core-index-v1`を固有prefix `run-007`へ公開した。

- staging入口: <https://koichi-takahashi.me/_reading-pack-staging/run-007/>
- 全57 file、合計2,212,553 bytesを公開URLから再取得し、local生成物とbyte一致
- Entry Prompt: 日本語2,764 bytes、英語2,726 bytes
- ChatGPT URL: 日本語5,981 characters、英語3,655 characters
- 日本語core: 94,278 bytes / 42,413 characters
- 日本語index: 124,149 bytes / 53,584 characters
- 英語core: 99,103 bytes / 98,362 characters
- 英語index: 110,560 bytes / 107,214 characters
- 最終staging Worker version: `932d70a1-1277-40c4-a04d-381402bb27bf`
- content-addressed artifactは`Cache-Control: public, max-age=31536000, immutable`
- 入口と404は`Cache-Control: no-store`
- `Mozilla/5.0`、`ChatGPT-User/1.0`、`Claude-User`、`Google-Extended`でcore取得200

Fable 5の日本語core初回受領は54.68秒でpassした。日本語core＋index質問は61.64秒で、`ENDPACKCORE`と`ENDPACKINDEX`が完全一致し、南方熊楠のrecordも正確だった。独立試行差によるindex追加時間の目安は6.96秒であり、同一sessionの厳密差分ではない。

英語core＋index試験は64.54秒でfailした。core末尾は完全一致したが、indexは同URLを二回取得してもWPI用語定義の途中で終わり、`ENDPACKINDEX`を取得できなかった。fail-closedと一回retryは設計どおり動作した。

UTF-8 bytesが小さい英語indexだけが失敗したため、byte budgetだけでは不十分と判定した。v1はproduction不採用。後継`web-core-index-v2`では`NAMES`と`GLOSS`を別shardにし、byte数とcharacter数を同時にgateする。正準Packとproduction導線はrun-007試験中も変更していない。

## 9. run-008 core/shards v2

Fable再review後、`MIS`もcoreから分けた`web-core-index-v2`を固有prefix `run-008`へ公開した。

- staging入口: <https://koichi-takahashi.me/_reading-pack-staging/run-008/>
- 全65 file、合計2,220,531 bytesを公開URLから再取得し、local生成物とbyte一致
- 日本語Entry Prompt: 3,831 bytes、ChatGPT URL 7,864 characters
- 英語Entry Prompt: 3,871 bytes、ChatGPT URL 5,094 characters
- 日本語artifact: core 48,221 bytes / 23,844 characters、mis 46,417 / 18,929、names 80,760 / 35,169、gloss 43,757 / 18,783
- 英語artifact: core 50,659 bytes / 49,918 characters、mis 48,804 / 48,804、names 74,305 / 72,475、gloss 36,623 / 35,107
- 英語namesのみ80,000-character上限の90.594%でwarning
- 最終staging Worker version: `64573f2c-1ee4-4f67-9807-19faf9cb0819`

四artifactはBrowserと主要fetcher User-Agentで200、`text/plain`、`immutable`だった。deploy直後の全file検査では二回の一時404後に200へ収束した。production Packは216,059 bytes、SHA-256 `de168369f799e0cfc91b23238432150dbb918d3a6805eaf7426b23bf118ec8df`、最終`ENDPACK`行のまま。production ChatGPT導線も完全Pack直URLのまま変更していない。

日本語のwarm HTTP取得各20回:

| artifact | p50 | p95 | max |
|---|---:|---:|---:|
| 完全Pack 216,059 bytes | 120.0 ms | 883.7 ms | 959.7 ms |
| core | 76.8 ms | 394.7 ms | 431.8 ms |
| mis | 92.5 ms | 405.6 ms | 413.0 ms |
| names | 88.2 ms | 557.2 ms | 567.8 ms |
| gloss | 165.9 ms | 393.1 ms | 567.1 ms |

HTTP差はmodel処理を含まず、初回体感を単独で予測しない。

Fable 5:

- 日本語core初回: pass、46.74秒
- 英語core初回: pass、21.92秒
- 日本語core＋mis＋names＋gloss: pass、43.32秒。四末尾marker逐字一致
- 英語core＋mis＋names＋gloss: pass、43.77秒。四末尾marker逐字一致
- 英語worst-caseではgloss初回末尾欠落、一回retryで回復
- MIS-01、南方熊楠 / Kumagusu Minakata、萃点 / suitenのrecord存在確認は正確

v1日本語core 54.68秒と比べ、v2日本語core初回はこの独立試行で7.94秒短かった。試行数1の参考値でありp50ではない。

## 10. ChatGPT Chat実機gateとrun-009

2026-08-22、run-008のEntry Prompt本文をlogin済みChatGPT Chatへinlineで渡した日本語実機試験は3秒以内で完了した。`ENDPACKCORE`と`MIS`、`NAMES`、`GLOSS`の三つの`ENDPACKSHARD`を逐字確認し、すべてprofile、lang、Pack SHA-256と一致した。追加download・添付は不要だった。この結果に基づき、日本語ChatGPT Chatへのproduction採用を著者が決定した。

英語ChatGPT Chatのlogin済み実機記録は未取得。英語は四artifactが二軸budget内、Fable 5で四末尾marker合格、公開byte一致という間接証拠で同時公開するが、ChatGPT互換性は暫定とする。

run-009ではEntry Promptをinlineで渡さず、ChatGPTに`entry-prompt.txt`を取得させてから`core.txt`へ進ませる二段取得を試した。初回受領文の確認までは成功したが、二段目はWeb側の安全制約によりretry後も失敗し、完全Pack添付を要求して停止した。この方式は不採用。Production site自身が不変Entry Promptを先読みしてChatGPT URLへinlineで埋め、ChatGPTの初回Web取得を`core.txt`一つにする。

本書の付録と公式補完資料はcoreの`REF`と`SYS C1`から従来どおり回答時参照する。`MIS`、`NAMES`、`GLOSS`は付録ではなく搬送用の遅延モジュールである。

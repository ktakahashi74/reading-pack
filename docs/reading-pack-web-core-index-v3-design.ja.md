# Reading Pack one-touch core/遅延モジュール v3搬送設計

## 1. 状態

- 状態: 参照実装完了。自動検査合格。staging・ChatGPT実機試験は未実施のため、production採用は未判定
- profile: `web-core-index-v3`
- 前版: [web-core-index-v2](reading-pack-web-core-index-v2-design.ja.md)

本書はv2からの差分だけを記す。marker書式、exact block、全byte被覆、二軸budget、失敗時の停止規則、完全Pack添付fallbackはv2のまま変更しない。正準`pack.md`、Pack SHA-256、一ファイル性も変更しない。

## 2. v2で生じた問題

2026-09-21、AGI-bookのReading Packを1.0.5-betaから1.1.0-betaへ改訂する過程で、正準命題が35件から95件へ増えた。`PROPS`は英語で約50,750 characters、日本語で約24,100 charactersとなり、coreの最大sectionになった。v2のcoreは`PROPS`を含むため、英語coreが81,333 charactersとなってcharacter上限80,000を超え、`delivery build`が失敗した。日本語coreは38,981 charactersで上限内だった。

v2は、v1の英語indexが観測成功境界を超えた問題を、最大section（当時は`MIS`）をcoreから独立させて解決した。今回も同じ構造である。上限80,000は、観測成功値98,362 charactersの約81%に置いた暫定gateであり、観測成功値に近いgateは構造的に脆いというv2のreview結論に基づく。上限を緩めて通すことは、この結論に反する。

## 3. v3の変更

`PROPS`を四つ目の遅延モジュールとする。

```text
<pack-sha256>/web-core-index-v3/<lang>/
├── entry-prompt.txt
├── core.md / core.txt
├── props.md / props.txt
├── mis.md / mis.txt
├── names.md / names.txt
├── gloss.md / gloss.txt
└── manifest.json
```

coreは`PROLOGUE`、`SYS`、`BIB`、`MAP`、`CERT`、`POLICY`、`REF`、`META`、`ENDPACK`のうち正準Packに存在するcomponentだけをexact収録する。確実性区分の定義（`CERT`）と方針（`POLICY`）はcoreに残す。個々の主張とその確実性・反証条件・再検討条件は`PROPS`にあるため、propsへ移る。

coreの末尾行は遅延モジュールの一覧を正準順で示す。

```text
ENDPACKCORE | profile=web-core-index-v3 | lang=<lang> | pack_sha256=<sha256> | deferred=PROPS,MIS,NAMES,GLOSS
```

実装では、遅延モジュールの一覧を一つの定数（`CORE_INDEX_DEFERRED`）から導き、artifact生成、Entry Prompt、manifest、検証、計測が同じ一覧を使う。将来の再分割は、この定数とEntry Promptの質問分類、manifest schemaの変更で済む。

## 4. 質問分類

| 質問 | 追加取得 |
|---|---|
| 章、確実性区分の定義、規範、参照、版 | なし |
| 主張、各主張の確実性、反証条件、再検討条件 | props |
| 反証、誤読、批判、限界、残る不確実性 | mis |
| 人名、組織、固有名、人物の別名 | names |
| 用語、本書内の意味、概念の別名 | gloss |
| 複数の区分にまたがる質問 | 必要な遅延モジュールを並列 |
| Pack内に存在しないとの断言 | 全候補の遅延モジュールを並列 |

Entry PromptがWeb取得を許すartifactは四つから五つに増える。それ以外の規則はv2と同じである。

## 5. 実測値

AGI-book 1.1.0-beta（命題95、誤読と応答39、人名138、用語61）のwrapper込み実測値は次となる。

| lang | core | props | mis | names | gloss |
|---|---:|---:|---:|---:|---:|
| ja | 30,176 bytes / 14,762 chars | 49,029 bytes / 24,585 chars | 50,767 bytes / 20,649 chars | 80,760 bytes / 35,169 chars | 43,757 bytes / 18,783 chars |
| en | 31,023 bytes / 30,457 chars | 51,992 bytes / 51,242 chars | 53,642 bytes / 53,642 chars | 74,305 bytes / 72,475 chars | 36,623 bytes / 35,107 chars |

英語namesはv2と同じくcharacter上限の90.594%でwarning対象になる。他artifactは90%未満である。英語propsは上限の64%である。

## 6. 失われるものと維持するもの

失われるもの:

- 主張と確実性の質問が、coreだけでは答えられず追加一fetchになる。v2ではcoreだけで答えられた。
- Entry PromptのURLが一つ増える。固定URLを持つ公開先は、`props.txt`の経路を一つ追加する必要がある。

維持するもの:

- サイトからChatGPTへの一回操作、初回一URL・一round
- 正準Packの一ファイル性、SHA-256、承認単位
- 完全Pack添付fallback
- v2の公開済みbundleは不変のまま残る。v3はprofile directoryが別であり、既存URLを上書きしない。

## 7. 採用gate

自動検査はv2の§10と同じ項目を、`props`を加えた五artifactへ適用する。参照実装は、全test、AGI-book 1.1.0-betaの`delivery build`と`delivery check`（全file byte一致）に合格している。

production採用には、v2と同じ実機検査が要る。少なくとも、主張・確実性の質問でpropsが取得されること、初回受領がcore一URLで完了すること、props取得の失敗時に部分回答せず停止することを確認する。実機検査の合格までは、v2の「production採用」をv3へ引き継がない。

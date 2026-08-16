PROFILE | name=reading-pack Reference Implementation Profile | version=0.5.0 | status=alpha | language=ja | primary=true | date=2026-08-16 | author=高橋恒一 | code_license=MIT | document_license=CC BY 4.0

# reading-pack参照実装プロファイル 0.5.0（alpha）

この文書は、当リポジトリにあるPython実装の公開契約を説明する。Reading Packの形式適合または制作適合を他の実装が宣言するための条件ではない。日本語版を正本とする。

## 0. 実装の役割

**RPI-001** `reading-pack`は、[Reading Pack形式仕様](reading-pack-format-spec.ja.md)に従う成果物を、[Reading Pack制作標準](reading-pack-production-standard.ja.md)に沿って制作するためのoffline-first参照実装である。toolkitを使った事実と、二つの標準への適合状態は別々に表示する。

**RPI-002** 基本機能は通信、API key、特定のAI事業者を要求しない。外部agentまたはlocal model adapterを使う操作には、その実行環境、規約、機密性が別に適用される。

## 1. Project形式

**RPI-003** Projectは`reading-pack.toml`、`data/pack.<lang>.json`、`templates/pack.<lang>.md`、`dist/`を基本構成とする。任意の`quality-plan.json`と、tool管理の非公開状態を`.reading-pack/`へ置く。

**RPI-004** JSON構造の正本は`schema/`にあるDraft 2020-12 Schemaとする。共通validatorはSchemaの構造検査を行い、URL、hash、provenance、状態遷移、多言語対応などの意味検査を追加する。CLIの`RP`、`QP`などはこの実装の診断コードである。

**RPI-005** 言語別正本は、chapter、certainty、claim、reading issue、policy、person、glossary、referenceを安定IDで管理する。日英projectは共通ID、同じ順序、原言語recordの意味hashを使って翻訳鮮度を検査する。

## 2. 入力境界

**RPI-006** 直接取込の境界は、依存関係を解決済みの一つのlocal fileとする。UTF-8 Markdown、Org mode、EPUB3を扱い、plain text、通常PDF、縦書きPDF adapterも提供する。directory、独自bundle、Org `#+INCLUDE`、DOCX、RTF、LaTeX依存を中核取込が再帰解決しない。

**RPI-007** 取込が正本へ移せるのは、上限付きの書誌、見出し、所在、診断であり、本文ではない。EPUBは標準ZIP/XML構造から、PDFはlocal Popplerから処理する。DRM解除、外部送信、path traversal、危険なXML entity、暗号化PDF、過大入力を拒否する。

**RPI-008** `pdf-vertical`は明示的に選択し、自動推測しない。PDFの章構造、外字、見開き、紙面pageは人間による確認を要求する。

## 3. 公開CLI

**RPI-009** Python 3.11以降で導入できる`reading-pack` CLIは、少なくとも`init`、`import-plan`、`import-apply`、`validate`、`build`、`check`、`doctor`、`review export|status|plan|apply`を提供する。各commandは用途に応じた非zero終了codeと説明可能な診断を返す。

**RPI-010** `init`は空でない作成先を既定で拒否する。正本を変える操作は、読取専用planと明示的applyを分け、既存fileを無条件に上書きしない。

**RPI-011** `validate`はSchema、ID、参照、言語対応、翻訳鮮度、容量上限を検査する。`build`は正本からPackを生成し、`check`は現在の生成物を再描画結果とbyte単位で比較する。`check --release`は制作上の公開gateも検査するが、人間の判断は行わない。

## 4. Authorityとproducerの境界

**RPI-012** 中核libraryは、producer機能がなくても取込、正本検証、build、byte再現検査、単一Markdown著者reviewを実行できる。Author Input Packageと著者reviewはauthority workflowとして中核generationから分離する。

**RPI-013** Catalog抽出、candidate生成、private表示、generation session、provenance receipt、Agent Skill配布はproducer側の任意機能とする。中核からproducerへの直接依存を作らず、CLIから遅延読込する。

**RPI-014** 構造化入力のない書籍には、moduleとscope単位の`work plan/next/ingest/close/status/retry/finalize` sessionを提供する。Responseは単独で検証できる公開Schemaに従い、原資料、project、work ID、chapter spanへ拘束する。

## 5. Transactionと安全境界

**RPI-015** 複数の正本artifactを変える操作は共通transaction層を使い、変更前後hash、許可path、project lock、`prepared`記録、原子的file置換、検証失敗または中断後のrollbackを共有する。file system全体にまたがる原子性は主張しない。

**RPI-016** Local model adapterは、入力、出力、実行時間を制限し、shellを介さず設定済み実行fileを起動する。この境界は実行fileをsandbox化せず、local fileの読取や通信を防がないため、adapter自体を信頼済みcodeとして扱う。

**RPI-017** Candidate evidence、review form、plan、application recordは現在の原資料と正本hashへ結び付け、stale、別project、重複、対象外、改変、容量超過を拒否する。Hashは協調工程の鮮度と破損を検出するものであり、電子署名、本人性、敵対的改変防止とは説明しない。

## 6. 配布と検査

**RPI-018** Agent Skill directoryとZIPは、既存の生成済みPackを収める任意の決定的配布物である。新しい正本、形式適合条件、制作上の承認単位にはしない。

**RPI-019** 公開testは通信せず、架空資料だけを使い、Schema、診断互換、byte再現性、日英対応、transaction rollback、path境界、長文複製防止を検査する。実機model評価はCIの必須条件にしない。

**RPI-020** このプロファイルへの準拠は`Built with reading-pack toolkit 0.5.0`という生成器表示で示してよい。これは形式適合または制作適合の宣言を代替しない。

## 7. 変更管理

本プロファイルの版はtoolkitの版に合わせる。CLI、Schema、project形式、transaction、package境界を変えた場合は、code、日英文書、test、合成作例を同じreleaseで更新する。

Copyright 2026 Koichi Takahashi / 高橋恒一. Document licensed under CC BY 4.0; implementation licensed under MIT as mapped in `LICENSES/README.md`.

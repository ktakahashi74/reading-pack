OVERVIEW | name=Reading Pack standards suite | version=1.0-draft | language=ja | primary=true | date=2026-08-16 | author=高橋恒一 | license=CC BY 4.0

# Reading Pack標準群 1.0-draft

Reading Packに関する公開規範は、成果物、制作工程、参照実装の三層に分かれる。この文書は入口であり、それ自体を適合条件にはしない。

日本語版を正本とする。英語版は同じ構造と要件IDを持つ翻訳である。不一致が見つかった場合は日本語版を優先し、同じコミットで両方を直す。草案どうしの互換性は保証しない。

## 三つの文書

| 文書 | 定めるもの | 定めないもの | 宣言例 |
|---|---|---|---|
| [Reading Pack形式仕様](reading-pack-format-spec.ja.md) | 読者へ渡す単一Markdownの構造、意味、安全上の境界 | どのツールで、どの工程を経て作ったか | `Reading Pack Format 1.0-draft conformant` |
| [Reading Pack制作標準](reading-pack-production-standard.ja.md) | Level 1〜3、W0〜W13、根拠、レビュー、評価、公開条件 | 特定の言語、CLI、内部実装 | `Reading Pack Production 1.0-draft Level 2 beta` |
| [reading-pack参照実装プロファイル](reading-pack-reference-implementation.ja.md) | このリポジトリのproject形式、CLI、取込、transaction、plugin境界 | 他社実装がReading Pack適合を名乗るための条件 | `Built with reading-pack toolkit 0.6.0` |

## 適合性の分離

形式適合は、完成したReading Packだけを検査して判定する。制作適合は、制作記録、根拠、人間の判断、実測評価を含む工程を検査して判定する。参照実装の利用は、そのどちらの必要条件でもない。

したがって、次の組合せを認める。

- 独自ツールで形式適合Packを作る。
- 独自工程で形式適合だけを宣言し、制作適合を宣言しない。
- 本ツールを使いながら、著者レビュー未了のため制作適合を宣言しない。
- 別実装を使い、形式適合と制作適合の両方を宣言する。

旧来の`Reading Pack Specification 1.0-draft`という一括表示は非推奨とする。新しいPackは、形式仕様と制作標準への適合状態を別々に表示する。

## 規範と実装上の診断

公開Schemaは、形式仕様または制作標準が参照する機械可読契約である。CLIが返す`RP`、`QP`などの診断コードは参照実装の安定した識別子であり、標準群の要件番号とは別物である。診断コードが残っていることは、旧一括仕様への適合を意味しない。

## ライセンス

形式仕様と制作標準はCC BY 4.0で公開する。改変、再実装、商用サービスへの利用を認める。帰属表示では、高橋恒一、文書名、版、参照URLを示すことを推奨する。

推奨引用：

- 高橋恒一（2026）「Reading Pack形式仕様 1.0-draft」`https://github.com/ktakahashi74/reading-pack/blob/main/spec/reading-pack-format-spec.ja.md`
- 高橋恒一（2026）「Reading Pack制作標準 1.0-draft（beta）」`https://github.com/ktakahashi74/reading-pack/blob/main/spec/reading-pack-production-standard.ja.md`

個別書籍の原稿、構造化データ、生成したReading Packには、このライセンスを自動適用しない。各権利者が別に条件を決める。

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.

# 開発への参加

Reading Packへの改善提案を歓迎する。このリポジトリは日英対応かつオフライン優先であり、コード、テスト、仕様、公開文書を一組として保守する。

## 開発環境

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

実行時のコードはPython標準ライブラリと、公開済みDraft 2020-12 Schemaを適用する`jsonschema`だけを使う。これ以外の依存パッケージを加える提案には、オフライン動作、安全性、配布、保守の四点について理由を添えてほしい。

## 変更時に守ること

- 振る舞いの変更と失敗例にはテストを加える。
- 標準群と参照実装プロファイルについて、日英の要件IDと節構造を文書ごとに一致させる。
- テスト資料には架空のデータだけを使う。未刊原稿、書籍固有の非公開評価、認証情報、ローカルの絶対パス、著作権で保護された本文を入れない。
- 取込処理は構造だけを抽出し、書庫、文字コード、XML実体、パス、容量の上限を文書に記す。
- 生成結果を再現可能に保つ。現在時刻、乱数、ロケール依存の並び順、通信結果を生成へ混ぜない。
- 著者承認、権利確認、再構築不能性の判定、公開判断を自動化しない。
- 公開される振る舞いを変えた場合は、日英のREADME、製作工程、クイックスタートも更新する。
- 公開文書は、コマンド名やJSON項目名を除いて自然な日本語で書く。最上位の枠組みと結論を先に置き、必要な技術語は初出時に短く説明する。

## 変更前の検査

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m reading_pack check --project examples/clockwork-garden --lang all --release
git diff --check
```

秘密に管理する攻撃手順を、公開IssueやPull Requestへ書いてはならない。脆弱性は[SECURITY.md](SECURITY.md)に従って報告する。

文書への貢献はCC BY 4.0、コードとテストへの貢献はMITで配布する。貢献した時点で、そのファイルに割り当てられたライセンスによる配布に同意したものとする。

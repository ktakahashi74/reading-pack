# 検査契約の選択と固定（M2）

新規runは合否判定の契約を固定する。無指定は`legacy-reader-evaluation-1`、明示した`artifact-acceptance-1`は[直接検査による制作](pipeline-artifact-workflow.ja.md)を選ぶ。`start --prepare-only`はモデル呼出しなしで入力を保存し、`plan`は有限の割当を確認する。`resume`は選択した契約を実行し、`finalize`はその契約の著者判断を取り込む。`contract_execution_supported`は実装の有無であり、予算の充足や品質合格ではない。

未知版とrecipe・manifestの不一致は拒否する。契約フィールドのない旧manifestは従来方式として扱い、書き換えない。エンジンhashと原資料の同一性も確認する。エンジン変更後の旧runが、そのまま再開可能になるわけではない。

`restart`は固定契約を継承し、別の契約を明示した場合は後継作成前に拒否する。契約をまたぐworker応答の再利用も拒否する。旧候補には`reassess-artifact`を使い、旧証拠、累計呼出し数、元の金額枠と期限を保持する。旧承認・意味判定から新しい合格を作らない。旧workflow signatureの計算も保持する。

新契約は`pipeline recipe`へ`--contract-version artifact-acceptance-1`を追加して選ぶ。資源枠のない実験用recipeには、明示した`--experimental`を必要とする。固定プロファイル、呼出し上限、検査上限は制作ガイドを参照する。

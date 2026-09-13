# Pack生成と品質評価の納品

新規制作の推奨入口は`pipeline deliver`である。`generation-report-1`は固定回数で生成物と定量評価を返し、採否はユーザーに委ねる。低得点や評価未完了を理由に生成物を捨てたり、修正・再試行を追加したりしない。正常な納品と、高品質・著者採用・公開は別の状態である。

## 操作

```sh
reading-pack pipeline delivery-recipe --output delivery.json \
  --generator /absolute/path/to/generator-adapter --generator-model exact-generator-id \
  --evaluator /absolute/path/to/evaluator-adapter --evaluator-model exact-evaluator-id \
  --scope "第1章のみ。注・付録を除く" --language ja \
  --max-cost-usd 3 --call-allowance-usd 1 \
  --max-wall-seconds 1200 --timeout-seconds 300
reading-pack pipeline deliver chapter.md --run private/run --recipe delivery.json \
  --chapter-level 1 --title "書名・第1章" --author "著者名" \
  --output deliveries/book-ch1 --prepare-only
reading-pack pipeline plan --run private/run
reading-pack pipeline resume --run private/run
reading-pack pipeline status --run private/run
reading-pack pipeline export --run private/run --output deliveries/book-ch1
```

この例の数値は1章の工程予約を説明する設定例であり、モデルの費用実績や品質保証ではない。adapterは既存のJSON入出力プロトコルを使い、1要求を1回だけ処理する。Claude専用ではなく、モデルIDと実行コマンドを明示する。引数付きコマンドはrecipeの`workers.*.command`配列で指定できる。`reading-pack-claude-adapter`ではモデル・CLI hash・費用・timeout・監査ディレクトリを持つwrapperまたは配列を使う。adapter自身の呼出し上限は章数に合わせ、CLI timeoutをcontroller timeoutより短く設定する。

初回の`deliver`では原稿とrecipeを凍結する。`--prepare-only`を省けば続けて実行する。凍結済みrunには`pipeline deliver --run private/run`も使える。再開時に原稿やrecipeを差し替えない。完了済みrunの再開は状態確認だけで、新規送信しない。旧`pipeline start`の契約をこの入口から再開することはできない。

## 構造と資源の事前確認

直接入力はUTF-8 Markdown、Org、text。PDF・EPUB等は既存の取込機能で確認済みのテキストを用意してから使う。著者提供のモジュールは暗黙には取り込まない。`--seed`で既存プロジェクトを明示したときだけ、その全モジュールを保持して章内容を生成する（次節）。

章の前後範囲、節の順序と位置は原稿から固定する。最上位見出しが1つで下位見出しがある場合、書名なのか章名なのかを推測せず、`--chapter-level`を要求する。章抜粋の`#`を章、`##`を節とする場合は1、先頭の`#`が書名で`##`が章の場合は2とする。単一の親書名以外の部構成は黙って落とさず、章単位に構造を整えた入力を要求する。コードフェンス内の見出しは数えない。`text`は単一章として扱う。

章数をNとすると、最大呼出し数は**2N+1**。各章の生成1回、原典照合1回、最後にPack全体の内部整合・利用指示評価1回を予約する。修正0、再試行0。全体評価はPack全体を読むため章評価より大きい。recipeの`global_call_allowance_usd`（全体評価の1呼出し枠）、`evaluator_timeout_seconds`（章評価のtimeout）、`global_timeout_seconds`（全体評価のtimeout）で役割別に予約でき、省略時は`call_allowance_usd`・`timeout_seconds`を使う。予約額は各呼出しの枠の合計、時間予約は各呼出しのtimeoutの合計＋ローカル予備で計算する。adapter側のtimeoutは対応するcontroller timeoutより短くする。生成が失敗した章の評価は未実施として残し、他の成功章を含む部分生成物と報告を納品する。

事前検査は実際に取り込んだ章数から最大呼出し数・各呼出しのtimeout・費用予約を計算する。全工程の予約が上限を超える場合は送信前に拒否する。`--prior-cost-usd`と`--cumulative-cost-limit-usd`で過去費用を引き継ぐ。省略時の過去費用は0なので、既存案件では実額を必ず指定する。providerが呼出し枠を超えた場合は追加送信を止め、超過を実額として報告する。予約は請求額の保証ではない。

既定は1章50000文字、128章、章内64節まで。章内を細かく自動分割して回数を増やさない。設定した文脈・構造上限を超えた原稿は送信前に拒否する。実際の要求が1 MiBを超える場合や残時間で呼出しを完了できない場合は、その作業を未実施として記録する。期限・開始済み呼出し・予約は再開してもリセットしない。結果不明の呼出しを自動再送しない。

## seedプロジェクトの取り込み

`--seed PROJECT`に既存のReading Packプロジェクト（通常は著者レビュー済みの正本）を指定すると、その確実性区分・正準命題・読解上の論点と応答（誤読・反論）・方針・人名・用語・参照は**そのまま**納品物へ入る。生成するのは章要約・章の用語・節の要点だけである。seedなしの納品はこれらのモジュールが空になるので、品質報告の「収録範囲の完全性」に「seedなし」と明示される。

```sh
reading-pack pipeline deliver body.md --run private/run --recipe delivery.json \
  --chapter-level 1 --seed path/to/canonical-project \
  --chapter-map chapter-map.json --prepare-only
```

- 章の対応はseedの章レコードと原稿の章見出しを、空白と互換文字を無視した完全一致で1対1・同順に結ぶ。一致しない章は推測せず送信前に拒否する。表記が違う章は`--chapter-map`（`{"CH-AFTERWORD": "あとがき"}`のようにseed章ID→原稿の見出し）で明示する。章数の不一致、seedの節題と原稿の直下見出しの不一致も拒否する。
- 節はseedの章の直下見出しに揃える。さらに下位の見出しは親の節に併合し、併合数を報告する。章IDと節IDはseedの番号体系を使うので、生成物の`CH-nn`が正本と食い違わない。
- `--seed-policy preserve`（既定）は、seedに要約・用語がある章ではそれを保持し、空の章だけを生成で埋める。著者レビュー済みのレコードは所在の追記も含めて変更しない。`regenerate`は要約・用語を生成結果で置き換え、その章レコードを`draft`に戻す。節の要点は常に新規の`draft`命題として追加し、seedの命題と同じIDがあれば拒否する。
- run内の`project/`はseedの複製から作るが、`status`は`draft`、`[workflow]`の各承認は`pending`、版は`-draft`付き、言語は納品言語のみに改める。seedの承認・公開判断を新しい生成物へ引き継がない。seedの複製は`seed/`に固定し、改変を検出する。
- 品質報告の「収録範囲の完全性」はモジュールごとにseed件数・出力件数・欠落ID・追加IDを示す。欠落があれば退行として明示する。seedの著者資料宣言（`claims: provided`等）と生成追加が衝突する場合は、著者判断が必要な項目として報告し、宣言を勝手に書き換えない。seedの元原稿と納品原稿のhashが違う場合もそのまま示す。
- LLMの原典照合はこの納品が生成したレコードだけを対象にする。seed由来のレコードは件数として保持し、再採点しない。全体評価は完成したPack全体を読む。

## 後継runによる未完了jobの再実行

凍結したrunは失敗・結果不明・未実施のjobを再送しない。生成失敗や評価のtimeoutを埋めるには、後継runを作る。

```sh
reading-pack pipeline deliver --run private/run-2 --predecessor private/run --recipe delivery-2.json --prepare-only
reading-pack pipeline resume --run private/run-2
```

- 先行runは完了状態（`delivered`・`delivered_partial`・`generation_failed`）でなければならない。原稿・章節構造・seed・題名は先行runから引き継ぎ、recipeの言語・範囲・両モデルは同一でなければならない。モデルを変える比較は新しい納品として行う。
- 完了済みjobの送受信証拠を`jobs/`へ複製し、hashで固定して再送せずに再生する。再実行するのは完了していないjobだけで、生成をやり直す章がある場合はPack全体が変わるため全体評価も再実行する。予約は再実行分だけを計算する。
- 報告の費用・呼出し数は後継runの分だけで、引き継いだjob一覧と先行runのpathを併記する。先行runの費用は`--prior-cost-usd`で累計へ入れる。先行runの生成物・報告は変更しない。
- adapterは、最終試行が宣言外のキーだけを理由に拒否された場合、そのキーを剪定して受理し、剪定したpathを監査と報告に残す。宣言済みの値は変更せず、値が不正な応答は救済しない。

## 納品物と評価

runディレクトリは非公開の作業場所であり、原稿・送受信証拠・seed複製・生成物が同居する。人に渡すのは`--output`で指定した納品フォルダー（省略時は`--run`の隣の`<run>-delivery/`）で、完了時に自動で書き出す。既存runからは`pipeline export --run DIR --output DIR`で後から書き出せる。納品フォルダーには次の4件と`delivery-manifest.json`（各ファイルのhash・run・モデル・seed・先行run・生成件数・評価件数・費用・採否未決）だけを置き、原稿・`jobs/`・`seed/`・`project/`本体は含めない。同じ内容の再書き出しは何もせず、異なる内容が入った非空フォルダーへは書き出さない。

- `reading-pack.<lang>.md`：生成物。章要約、章節地図、節の要点、利用指示を収録する。
- `quality-report.<lang>.md`：評価範囲、評点、指摘、根拠、未評価、時間・費用、収録範囲の完全性（seedとの件数差分・章ID対応）。
- `quality-report.json`：機械検査・LLM原評価・使用モデル・実行状態を分けた原データ。
- `pack.<lang>.json`：描画元の正準データ。seedのモジュールと生成レコードを含み、書籍プロジェクトへの取り込みに使う。
- run内にのみ残るもの：`project/`（再描画可能なtoolkitプロジェクト）、`source.bin`・`source.txt`・`jobs/`・`seed/`（元原稿、正規化テキスト、送受信証拠、seed複製）。非公開で保管する。

出典は制御器が`source.txt#normalized-text:start-end`を付ける。単なる「存在する位置」と「その主張を裏付ける位置」を区別し、後者はLLM評価と根拠を提示する。機械検査は章節構造、内容欄の空欄、所在、引用抜粋一致、形式検証、長さ、配布物一致を数値化する。

LLMは全収録項目を`supported / partially_supported / unsupported / unclear`で分類し、節ごとの充足を`covered / partial / missing`で示す。章ごとの原典忠実性・論点充足・条件と帰属、Pack全体の内部整合・利用指示を0〜4で評価する。各値に対象数・評価済み件数・理由を付ける。全体検査はPackのみを読み、原典照合は章別評価が担当する。原稿自身の科学的妥当性は審査しない。

評点基準は送信前に固定する。0は目的をほぼ満たさない、1は重大な問題が複数、2は主要部分が使えるが重要な不足が残る、3は軽微な問題が残る、4は検査範囲で具体的な問題を認めない。評点は順序尺度であり、正しい確率ではない。総合平均や合否へ変換しない。未評価は0点としない。

評価応答が不正・失敗でも生成物は保持する。低得点は正常納品を妨げない。全章の生成が実行上失敗した場合は`generation_failed`として終了コード1、納品済み・部分納品・準備完了は0。報告の生成件数と評価済み範囲を必ず併読する。`status`はファイルの改変も確認し、改変された生成物へ古い評価を表示しない。

## 既存機能との関係

旧`legacy-reader-evaluation-1`と`artifact-acceptance-1`は、その契約で凍結したrunを検証・再開する入口として保持する。既存合否や承認を`generation-report-1`へ付け替えない。旧制作標準・release検査の適合要件は、生成物と評価を納品するという新入口の完了条件と区別する。納品から著者採用・権利確認・公開を推定しない。

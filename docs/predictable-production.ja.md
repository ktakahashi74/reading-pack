# 時間・費用・品質の予測可能性

新規の生成・評価納品には[Pack生成と品質評価の納品](pipeline-delivery.ja.md)を使う。品質評点による自動採否を行わず、生成物と評価をユーザーへ渡す。このページの旧契約による合否・承認処理とは区別する。

新契約のモデル選択と資源内完成率の実測は[モデル比較試験](pipeline-model-comparison.ja.md)を参照する。合格まで工程を増やさず、有限の試行を比較する。

このガイドは`legacy-reader-evaluation-1`の説明である。直接検査の新契約は[制作ガイド](pipeline-artifact-workflow.ja.md)を参照する。新契約の合否は回答成功や制作実績の証明書を必要としない。

新規runでは、設問・回答の生成前に読解支援の合格範囲を固定する。生成された設問の細部を、Packの必須採録事項へ自動変換しない。中心的な意味、重要な条件、正しい帰属は必須とし、付随的な数値は、情報の限界を明示してPack内にある適切で具体的な所在を案内する対応も認める。一般的な丸投げや、書かれた内容の誤りは認めない。既存試験の再開では元の範囲と結果を保持し、基準を変える場合は別の再評価として識別する。これは候補全体の再生成を要求するものではない。

通常運用に使えるワークフローには、定めた時間と費用の範囲で、読解に役立ち、原文に忠実なPackを繰り返し作れた実績が必要である。時間切れで停止するだけでは制作成功ではない。v0.7.0は適格性を実測していないalphaであり、リポジトリの制御試験は実書籍の品質実績を示さない。

## 実行全体で一つの運用条件

recipeの`operating_envelope`に、全体の経過時間上限、呼出しごとのtimeout、費用予約額と、準備・生成・開発評価・修正と再評価・最終評価の5工程の呼出し枠を固定する。ローカルの入力準備にも時間枠があり、その実測時間を実行開始時に加算する。`--prepare-only`から初回実行までの待機は除く。実行開始後の中断・再開は、最初の期限を消費する。

送信前の`pipeline plan`は、入力規模と資料の役割、PDF復元の必要工程、必須呼出し、費用予約と期限を検査する。最終評価の枠を生成前に予約する。修正枠には最低でも生成1回、独立審査1回と開発評価全体の再試験を含める。表示する最小回数には、すべての疑義判定、審査分割、設問差戻しは含まれない。それらにも工程別上限が効き、枠内で完成するかは実測で確認する。

この運用では候補を最大2ラウンド、初回生成と修正1回に制限し、通信再試行は行わない。生成の処理区間は最大50,000文字にできるが、原文の長文転載を許す設定ではない。`benchmark_chunk_limit`は、提供された各資料を最低1区間ずつ含め、設問生成用の区間を固定的に抽出する。原文忠実性の監査は引き続き全区間を対象とする。抽出は回答を見る前に固定し、既存runの設問の差替えや基準緩和には使わない。

resumeとrestartは同じ期限と累計予約額を引き継ぐ。restartは一つの後継へ枠を移し、元runや別の分岐で再使用できない。失敗・結果不明の呼出しも全額を消費した予約として扱う。最終評価の未使用枠や以前の安価な呼出しから、別工程の枠を増やさない。最終試験後に著者が内容変更を求めた場合は`revision_requested`として記録し、別予算の実行を自動開始しない。内容変更のない通常の著者承認では、従来どおり承認済み配布物をローカル生成する。

## 費用の意味

制御側はUSD建ての予約額を制限し、呼出し単位の額を付属Claude adapterへ渡す。adapterはproviderの予算指定へ反映する。CLI報告額、予約枠、実際の請求額は別の数量である。報告額が指定額を超えたら停止し、適格性試験も不合格とする。費用不明をゼロや予算内の証拠にしない。この実装からproviderの請求超過を完全に防ぐ保証はできない。請求額の厳密な上限には、その保証を持つprovider側の制御も必要である。

## 送信せずに準備・検査する

以下の数値は小規模な対応入力に対する設定例であり、推奨モデル予算でも2時間で完成した実績でもない。provider設定は実測から選び、試験前に固定する。

```sh
reading-pack pipeline recipe --output qualification-recipe.json \
  --generator /path/to/generator --generator-model generator-model-id \
  --judge /path/to/judge --judge-model judge-model-id \
  --reader /path/to/reader --reader-model reader-model-id \
  --operating-mode qualification --max-wall-seconds 7200 \
  --max-cost-usd 35 --call-allowance-usd 1 \
  --phase-calls '{"preparation":8,"generation":6,"development":8,"repair":10,"final":3}'
reading-pack pipeline start book.md --run private/trial-1 \
  --recipe qualification-recipe.json --prepare-only
reading-pack pipeline plan --run private/trial-1
```

著者の追加資料があれば`--supplement author-data appendix.md`を加える。資料の役割と元のバイト列を入力条件に保持する。内容入りの正本をseedにする場合と、本文だけから生成する場合は異なる実測対象である。小さなMarkdownの既存Packで合格しても、大きなPDFからの新規生成は適格としない。

計画の`admitted`はローカルの実行可能性検査を通過した意味であり、品質の適格性ではない。`status`は期限、経過時間、予約額、既知の報告額、費用不明の件数を表示する。`input_outside_envelope`、`deadline_exceeded`、`phase_budget_exhausted`、`cost_allowance_exceeded`、`workflow_unqualified`は、品質不合格や著者確認票への到達とは区別する。

## 有限の適格性試験を先に固定する

これはサービス提供側の設定作業であり、各書籍の著者へ試験運営を求めるものではない。送信前に、良好な入力と意図的に欠陥を入れた対照例を決める。最低2冊、各条件で新規実行2回、帰属誤り・重要条件の欠落・主張の捏造など最低3種類の実質的な欠陥を含める。観測完成率の合格値を事前に定める。初期の小規模試験では通常`1.0`とする。形式・言語・規模・プロファイル・追加資料の組合せは、提供するサービスを代表する条件を選ぶ。これらの最低数は初期検証の手順であり、任意の書籍への統計的な保証ではない。

全runを準備し、非公開のJSON suiteに次のフィールドだけを記録する。

- `schema_version`: `1`。
- `workflow_signature`: `reading_pack_producer.pipeline_qualification`の同名関数へmanifestを渡した値。
- `repetitions`、`minimum_distinct_books`、`minimum_defect_classes`、`minimum_completion_rate`。
- `cases`: 各要素に`id`、`book_id`、同moduleの関数で求める`input_identity`、`expectation`（`complete`か`detect-defect`）、`defect_class`、`record_ids`を記す。良好例は欠陥種別を空文字、IDを空配列とする。欠陥対照例は、検出すべき指摘のcriterionまたはcategoryと対象の正本IDを指定する。

各条件と反復番号を送信前に登録する。実測の各runには別のprovider監査ディレクトリを使う。完全一致の保存済み応答も、新規測定には数えない。試験を承認する前に、全runの最大予約額と期限を合計する。不合格を救うための試行追加、モデル変更、基準移動、予算延長は行わない。

```sh
reading-pack pipeline qualification-register --run private/trial-1 \
  --suite private/suite.json --case body-only --repetition 0
reading-pack pipeline resume --run private/trial-1
# 事前登録した全条件・全反復を実行し、失敗も保持する。
reading-pack pipeline qualification-report --suite private/suite.json \
  --run private/trial-1 --run private/trial-2 --output private/qualification.json
```

集計コマンドはモデルを呼ばない。成功したrunだけでなく全試行を指定する。入力・recipe・engine・モデル・adapterの同一性、新規の使用記録、経過時間、報告費用、最終候補の完全性を検査する。良好例は保留試験を通過し、著者確認票へ到達して初めて成功となる。欠陥対照例は指定した不具合と対象IDを検出した場合だけ成功とし、provider障害を欠陥検出に数えない。欠落・重複・再利用・変更済み・費用不明の試行では適格としない。

報告には観測完成率、欠陥検出率、所要時間の分位点、最大報告費用を出す。補助値のWilson下限は独立試行を仮定する。同じ書籍の反復には相関があるため記述値に限定し、運用の許可条件には使わない。適格性試験のrunは証拠として固定保存し、著者による変更や公開作業は別の本番runで行う。合成試験の成功から実モデルの適格性証明を作らない。

## 実測済みの条件で運用する

本番では`operating_envelope.mode`を`qualification`から`production`へ変え、runごとの`--audit-dir`だけを変更する。`pipeline start ... --qualification private/qualification.json`で実測報告を添付する。engine、モデル、effort、その他のadapter引数、品質基準、資源設定は同じでなければならない。実測した形式・言語・追加資料の役割・seedの有無・最大本文量を超える入力は、生成前に止める。自動選択したプロファイルも、その入力条件での実測が必要である。これだけで文章の複雑さ全体は記述できず、完成率の主張は宣言した試験範囲に限られる。

alphaには実測済み証明を付属しない。実試験で適格と確認するまで、本番モードは閉じる。従来の研究用経路は`recipe`、`start`、`resume`、`restart`へ明示的に`--experimental`を付けた場合だけ利用できる。その制御試験の成功を、実書籍の予測可能な制作実績と説明してはならない。

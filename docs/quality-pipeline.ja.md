# 品質保証

これは[Reading Pack制作標準](../spec/reading-pack-production-standard.ja.md)を`reading-pack`参照実装で満たすための品質保証手段を説明する。Schema名、command名、非公開runの構造はtoolkit固有であり、他の実装が同じ内部構造を採用する必要はない。

Reading Packが「さまざまな本に使える」とは、同じ制作工程を適用できるという意味である。一つのプロンプトで、どんな本でも自動的に公開できるという意味ではない。

ソフトウェアが扱う範囲は、正確な原資料から確認可能な下書きを作るところまでである。一般書、教科書、文芸書、論集、辞典には、それぞれ異なる品質条件がある。権利、解釈、ネタバレ、著者の権限、公開の可否は人間が判断する。

## 四つの保証層

品質は四層で守る。

1. **品質計画**：`quality-plan.json`に、用途別プロファイル、対象範囲、責任者、ネタバレ方針、項目の適否、必須条件、実測値の下限を記す。
2. **取込計画**：`import-plan`は正本を変える前に、本文を含まない構造案を作る。原資料名、容量、ハッシュ、階層、所在、抽出確度、来歴、診断、判定結果を記録する。
3. **候補処理**：候補レコードと原資料の短い抜粋を非公開領域へ隔離する。確定記録には抜粋を残さず、ハッシュと正規化した範囲だけを残す。自動処理で到達できるのは`ready_for_review`までである。
4. **正本と公開レビュー**：明示的に採用した候補だけを、IDを指定して`draft`として適用する。著者、権利、出版社、再構築不能性、実測評価、公開判断は別の条件として扱う。

取込計画や候補記録のチェックサムは、偶発的な破損や単純な変更を検出する。電子署名ではなく、確認者の本人性や原資料の所有権を証明しない。

確認者名も自己申告である。非公開の候補記録を書き換えられる者は、ハッシュを再計算して名前を偽装できる。敵対的な書込者まで想定する場合は、別に管理した署名または承認システムが必要となる。

## 品質プロファイル

`reading-pack profiles`は七つの組込みプロファイルを表示する。

| プロファイル | 主な対象 | 必須の重点 |
|---|---|---|
| `general-navigation` | 一般的な章節案内 | 構造と再構築不能性 |
| `academic-argument` | 学術書・論証中心の本 | 要約、用語、主張、参照、帰属 |
| `nonfiction-reading` | 一般向けノンフィクション | 主張、参照、限定条件の保持 |
| `textbook-learning` | 教科書・学習書 | 学習目標、主張、用語集、参照、誤概念の防止 |
| `fiction-spoiler-free` | 未読者向け文芸書 | ネタバレ範囲と解釈の開放性 |
| `anthology-attribution` | 複数著者の論集 | 寄稿者と権限の帰属 |
| `reference-routing` | 辞典・便覧・目録 | 項目網羅、別名、所在案内 |

各プロファイルは、最低製作等級、必須項目、章に必要な情報、公開上の必須条件、既定のネタバレ方針を持つ。項目を`not_applicable`にできるのは、そのprofileが明示的に許し、計画に理由がある場合だけである。一般向けノンフィクションではreferencesを監査対象に保つ一方、確認した版にPackで使える参照先が存在しなければ、その不在を明示的に宣言できる。宣言なしの空collectionは引き続き失敗し、学術書と教科書ではreferencesを必須のままとする。書籍ごとの差を認めながら、重大な欠落を平均点のなかへ隠さないための仕組みである。

新しい品質計画は未承認から始まる。`check --release`は次の七点を要求する。

1. 現在の正本データと品質計画のハッシュへ結び付いた、氏名付き責任者の承認。
2. すべての必須条件の承認と、プロファイルが要求するデータの充足。
3. 採録した人名の`book_context`と、用語の`book_meaning`。短すぎる説明や「同章で言及」だけの仮文は認めない。
4. 既存版を置き換える場合は、比較資料のSHA-256へ結び付いた`content_floor`を下回らないこと。章要約、命題、限定条件、誤読訂正、索引説明、参照先、内容文字数をそれぞれ測る。
5. 別の著者レビューを経た、全公開レコードの承認。
6. 事前に決めた下限を満たす章構造の適合率と再現率、捏造レコード数、原資料への帰属誤り数。
7. 記録したハッシュが現在も一致する評価資料。

正本または品質条件が変われば、対応する承認は古くなる。品質計画がない、未承認のまま、実測値がない、古い正本へ結び付いている、のいずれかに当たれば`--release`は失敗する。

`reading-pack measure --project PACK --json`は、同じ指標を正本から再計算する。旧版が正本JSONでない場合は、確認済みの比較報告で測定方法を固定し、その報告のハッシュと値を`content_floor`へ転記する。重複や冗長な文で件数だけを増やしても、原資料への忠実さと意味レビューを通過できない。

## 章構造の取込み

```sh
reading-pack import-plan book.pdf --output /tmp/book-import-plan.json
# 判定結果、診断、階層、所在、来歴を確認する。
reading-pack import-apply /tmp/book-import-plan.json \
  --source book.pdf --project my-pack --lang ja
```

計画の作成は正本を変えない。適用時には原資料と計画を再検査し、プロジェクトの協調ロックを取得して既存レコードと対応付ける。一意に対応できる場合は、安定IDと保持できる編集済み項目を残す。原資料の変更、対応の曖昧さ、検証の失敗があれば、正本を置き換える前に停止する。再取込みで原資料または構造が変わったレコードは`draft`へ戻る。

スキャンや複雑な紙面では、`--outline-sidecar outline.json`を手作業の構造回復に使う。この補助ファイルは原資料のSHA-256へ結び付き、確認者、理由、章の種類、安定した原資料キー、見出し、ページ、節見出しだけを持つ。要約、用語、本文、承認状態は入れられない。OCR出力そのものは非公開に保ち、目次の補助ファイルへは入れない。

PDFの章構造と紙面ページは保守的な推定である。平坦化したテキストから、すべての目次、見出し階層、物理ページを確実に復元することはできない。この場合に必要なのは、確度を高く見せる工夫ではなく、原資料との照合である。

縦書きの文字層が一字一行になる日本語PDFでは、`--format pdf-vertical`を明示する。この形式は元のPDFを原資料として保ち、候補の根拠、索引抽出、レビュー画面でも同じ読順再構成を使う。OCRではなく、外字や見開き内の章境界を完全には保証しない。確認済みの目次、明示的な章対応、元の紙面との照合を品質条件とする。

## 再開可能な上限付き生成

`work plan --session-directory ... --source ...`は、AIPの`generate`/`augment`宣言と、まだ入力されていないmoduleから作業を作る。sessionとledgerが持つのは、原資料・正本のhash、章範囲、状態、応答hashであり、原稿本文ではない。`work next`は一件分の固定prompt、原資料の所在、正確なwork binding、単独で解決できるDraft 2020-12 response Schemaを機械可読JSONとして返す。書籍全体を一つのpromptへまとめない。

固定moduleには書籍scopeの`policy`も含む。workerが返してよいのは、原資料に明記された方針だけであり、許諾、承認、公式性を推定してはならない。候補適用時には、検証済み根拠の範囲をrecord単位の`source_locations`へ変換する。これは登録済みmodule原資料のhashを置き換えるものではなく、補うものである。

外側のエージェントは宣言された原資料範囲を確認して上限付きresponse fileを作れる。根拠付き候補がないと判断した一件は、`work close --outcome no_supported_candidate|skipped --reason REASON_CODE`で閉じられる。このcommandは次の未応答itemのbindingから0件responseを内部生成し、通常のSchema、stale、scope、重複検査へ渡す。`failed`は処理障害を表すため、この短縮経路では記録できない。運用者が`work ingest --adapter-executable ...`を明示した場合は、同じJSON requestを標準入力で受け取るlocal executableを使える。shellは介さず、timeoutと出力上限を設けるが、sandboxではなく信頼済みcodeとして扱う。組込みmodel、特定provider API、通信、provider固有tool名は必要としない。

応答はsession ID、work ID、project/config hash、言語、原資料hash、正本hash、module、scope、章範囲を再掲する。重複、別project、古い状態、対象外、容量超過、構造不正、timeoutではsessionを進めない。結果は`complete`、`no_supported_candidate`、`failed`、`skipped`を区別し、recordを持てるのは`complete`だけである。本文を処理するsessionから著者Q&Aを発明する応答は拒否する。明示的に選んだ`misreadings` moduleは、重大な読み違いを防ぐために原資料が明示した反論、限定、区別だけを、中立的な`issue`と`response`として生成してよい。これは既定の自動module集合には加えず、引用された批判者を誤読者として扱わず、独立した著者Q&Aを作ってはならない。

運用者が`work plan --chapter-map`を渡した場合、確認済みでsource hashに結び付いたnormalized-text章spanをsession IDの一部にする。章scopeを持つ各根拠は、sessionを進める前のingest時点で実際の出現位置を求め、対象span内かを検査する。

非公開session directoryには厳しいpermissionを設定する。取込済みresponse fileには、短い根拠断片が一時的に含まれうる。`finalize`は通常のcandidate経路を使って正確な原資料に対する根拠を再検査し、既存work ledgerをreconcileする。全検査後に本文断片を持たない一つのcandidate runを作り、一時response fileを削除する。正本は変えない。候補検証に失敗した場合は指定したrun directoryを占有せず、診断のためopen sessionとresponseを残す。`work retry --id WORK_ID`だけが、hashを確認した該当responseを削除して`awaiting_response`へ戻せる。重複取込みは暗黙に上書きしない。候補の一次レビュー、採否、`draft`適用、著者レビュー、権利、公開gateは従来どおり別工程である。

初回下書き後には、任意で`work plan --purpose coverage`を使う。これはproject固有の自由記述promptでも、件数のquotaでもない。producerが固定の`whole_book_gap_audit_v1` rubricと、対象scopeの本文を含まない現状inventoryを与える。外側のagentは、hashへ結び付いた正本の現状と同じ原資料を比較し、実質的な追加・置換候補を根拠付きで返すか、0件を明示する。rubricは、要約の論旨と限定、検索に必要な用語、記述的・規範的命題、機序と条件、帰属と不確実性、人名・用語の本書固有の説明を点検する。人名・用語の網羅的発見はcatalog経路に残し、確認済みの明示的章対応、必要に応じた`catalog candidates --responses`、通常の候補レビュー、`catalog context-plan --refresh-existing`、`catalog context-candidates`の順で進める。

## 候補のレビュー

`prompts/candidates.ja.md`に従って、上限付きのJSON応答を用意する。本文の短い抜粋は、正確な原資料内の位置を見つけるためだけに一時利用する。

```sh
reading-pack candidates create /tmp/responses.json \
  --run-directory my-pack/.reading-pack/runs/run-001 \
  --source book.pdf --project my-pack --lang ja

reading-pack candidates report my-pack/.reading-pack/runs/run-001
reading-pack candidates verify my-pack/.reading-pack/runs/run-001 \
  --source book.pdf

# 人が内容を読み、ハッシュへ結び付いた候補を一件選ぶ。
reading-pack candidates accept my-pack/.reading-pack/runs/run-001 \
  --id CANDIDATE_ID --reviewer "編集者名"

# 監査記録付きのAIレビューを使う場合。
reading-pack candidates accept my-pack/.reading-pack/runs/run-001 \
  --id CANDIDATE_ID --reviewer "model-id" --reviewer-type ai \
  --review-artifact my-pack/.reading-pack/ai-review-run-001.json

# 適用にも候補IDが必要で、正本へ入るのはdraftだけである。
reading-pack candidates apply my-pack/.reading-pack/runs/run-001 \
  --source book.pdf --project my-pack --lang ja --id CANDIDATE_ID

# 順次適用したrunを現行正本に結び付けた一つの引き渡し記録にする。
reading-pack candidates receipt --project my-pack --lang ja \
  --artifact my-pack/.reading-pack/runs/run-001 book.pdf \
  --artifact my-pack/.reading-pack/runs/run-002 book.pdf \
  --output applied-chain.json
```

PDFとEPUBの照合用テキストは、指定した入力ファイルから内部で導出する。任意の抽出テキストへ差し替える選択肢はない。原資料、応答JSON、作業用の抽出物は確定記録の外に置き、非公開の作業領域で管理する。

根拠の検証が示すのは、記録した範囲が正確な原資料に由来し、現在も同じハッシュを持つことだけである。そこから候補が論理的に導かれること、要約の完全性、帰属の正しさ、解釈の権威性は証明しない。人間または監査記録付きのAIが採用前に判断し、著者の最終承認は後の工程で人間が行う。

候補の作成と適用では、型と文字数上限、全体で一意なID、参照、用語・人名の正確な出現、根拠の範囲、本文の過剰複製も調べる。未知、未対応、重複、古い状態、複製の危険があるレコードは隔離したままにする。章の候補は編集可能な項目だけを変え、取込済みの構造を暗黙に置き換えない。

多言語プロジェクトでは、現行の候補コマンドは原言語の新規・変更レコードを一方的に適用しない。IDの日英対応と翻訳鮮度を保つため、正本の翻訳工程を使う。複数の候補処理を同時に適用する翻訳機能は今後の課題である。

## 競合と失敗時の動作

- 候補処理は、原資料、正規化した原資料、正本の状態、候補レコード、関係する既存レコードの各ハッシュへ結び付く。
- 採否は、変更されていない候補に対して確認者の名前と種類を記録する。AIレビューでは判断記録のハッシュも残す。
- 適用時は協調ロックのなかで、原資料、正本、既存レコード、根拠、採用記録を再検査する。
- 原資料の変更、途中の正本編集、対応の曖昧さ、隔離中の候補、検証失敗があれば、新しい作業を上書きせず停止する。
- 正本JSONと非公開の候補記録は別々のファイルである。ファイルシステム全体にまたがる原子的な更新とは説明しない。`prepared`状態により、中断した適用を検出して回復できる。
- 成功した適用と回復した適用は、prepared transactionのbefore/after data hashとproject hashを決定的な`application` receiptとして残す。`candidates receipt`は各run、原資料、根拠span、terminal state、隣接hash link、最終正本との結合を再検査する。旧manifestは`--allow-legacy`が必要で、そのlinkを検証済みと表示しない。
- 取込みや候補適用に成功しても状態は`draft`であり、著者承認や公開承認にはならない。

この仕組みは、Reading Packを使う複数の書込者が作業を壊さないための協調手段である。別のプログラムがロックを無視して書き込むことまでは防がない。

## ローカルアダプターの信頼境界

任意のローカルJSONアダプターは、入出力の容量と実行時間を制限し、シェルを介さず設定済みの実行ファイルを直接起動する。この制限は入出力と資源の境界であり、サンドボックスではない。

設定した実行ファイルは、実行利用者が読めるファイルへアクセスでき、通信も利用できる。信頼できるコードとして扱い、設定、原稿の機密性、外部事業者の規約を確認する。基本機能が通信しないからといって、任意のアダプターまでオフラインで安全になるわけではない。

## 現在の対応範囲

現在の構造取込みは、UTF-8 Markdown、Org mode、EPUB3、保守的なPDF目次、保守的なプレーンテキストに対応する。スキャンPDFにはOCRと、原資料を確認して作った目次が必要となる。DOCX、HTML、より豊かなEPUBナビゲーション、ページ領域単位の自動根拠、GUIレビューは今後の候補である。

どの形式を加える場合も、取込計画、根拠、採否、古い状態の検出、公開判断という五つの境界を迂回してはならない。

Copyright 2026 Koichi Takahashi / 高橋恒一. CC BY 4.0.

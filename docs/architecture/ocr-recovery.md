# OCR中断回復・再試行・結果結合

更新日: 2026-10-16（JST）。共通実装はsrc/signal_library/ocr/recovery.py、results.py、files.py。

## 中断と結果不明

`ocr recover --operation books/<book_id>/ocr/<plan_hash>/operation.json --json`は、操作記録と唯一のmanifestを対応付けて既存jobの状態を確認する。ネットワークのcreate/uploadは呼ばない。SUBMITTINGまたはSUBMISSION_UNKNOWNでjob IDがない場合はrun_idのmetadataから検索し、1件だけ一致する場合に回復する。0件・複数件・検索の打切り・model不一致は停止し、再submitしない。manifestが0件または複数なら操作記録を保持して手動確認する。

upload途中の結果不明なファイルIDは推測しない。recordだけが残る状態を新規送信の許可と扱わない。残留lockは自動削除せず、実行中プロセスの有無を確認して退避する。同じ保存先の複数端末同時書込みは対象外。

collect失敗は同じmanifestから再回収できる。通常ファイル・redirectの検査、同一volumeの一時ファイル/fsync/原子的置換を使い、raw/管理ファイルの部分内容を公開しない。既存job ID・原本hash・custom_id・入力/出力範囲を照合する。ロック取得前後で非協調プロセスがすべてのファイルを置換する攻撃や電源断後の永続性まで保証するものではない。

## 再試行

```text
mise run library -- ocr retry --manifest <元manifestのルート相対パス> --max-pages 1 --max-cost-usd 0.01 --json
```

既定は通信・書込みなしの計画。jobがterminalで、回収済み文書のFAILED/MISSING_RESULTだけを対象にする。期待外・重複custom_id、原本変更、未確定job、成功文書だけのrunは停止する。対象hash、custom_id、parent run/manifest/hash、ページ数・見積りと上限を示す。

実送信には`--apply --confirm-plan <hash> --allow-upload`が必要。以前の通常runの許可を無条件に再試行へ拡張しない。元runを変更せず新runを作り、成功文書は再送しない。旧ファイル名が変わる場合もcustom_idは元runと同じ値を使う。旧model 4.0の履歴を再試行する場合はそのmodelを計画へ保持し、実行時の提供状況・料金を再確認する。

## 結合と検証

```text
mise run library -- ocr merge --manifests <元manifest> <成功retryのmanifest> --json
mise run library -- ocr merge --manifests <元manifest> <成功retryのmanifest> --apply --json
mise run library -- ocr validate --manifest <結合manifest> --allow-remote-files --json
```

同じ書籍/capture/modelのrunだけを結合する。元runの原本集合の外へ広がるretry、同じcustom_idで異なる原本、成功custom_id重複、未解決失敗、出力名衝突、未解決warning、欠けたMarkdown/JSON/画像を拒否する。runのディレクトリ名が同時刻でも、run_idで元manifestを区別する。

結果は新しい`ocr/<operation_hash>/combined/`へ保存する。各文書のsource_run/source_manifestと元manifestのコピーを保持し、原runを上書きしない。結合runは新しい派生run IDを持ち、元OCR jobと同一視しない。結合前計画と全ファイルhashから中断記録を作り、同じ計画で再開する。未知ファイルや変更済みコピーがあれば上書きせず停止。同じ完成結果は再利用する。

Markdown画像はローカルのインライン記法と、その文書のassets内参照だけをこの段階の検証対象とする。外部画像・HTML img・参照形式画像は自動結合せず要確認。I-08での全AST検査の代わりではない。未参照symlinkもコピーしない。結合元のcleanup未完了をCLEANEDへ偽装せず、保持する場合はallow-remote-filesと警告を使う。

## 旧履歴のコピー移入

```text
mise run library -- ocr import-run --book-id <id> --capture-id capture-001 --source-manifest <旧manifest絶対パス> --json
mise run library -- ocr import-run --book-id <id> --capture-id capture-001 --source-manifest <旧manifest絶対パス> --apply --json
```

通常schema_version=1とpurpose=signal-stack-ocr-combinedを区別して読む。旧manifestの絶対パスは変更せずlegacy-manifest.jsonへバイト列を保持し、現在の登録原本hashに対応する書籍内コピーを作る。元のcustom_id、run ID、元manifest、由来を保持する。旧結合runに全体run IDがない場合だけ、新しい移入済み結合IDを発行する。

対象capture内で原本hashが一意に解決できない場合は停止する。未撮影原本を推測して補わない。旧結合runでは文書ごとの元manifest・custom_id/hashも照合してコピーする。移入後のstatus/collectは現在の保存先を使用するが、旧jobの有無を実証するのは実際の状態確認時。移入だけでremote jobの再開成功と扱わない。

## 台帳とcleanup

book.json/catalogの更新前後と予定OCRレコードをrun内journalへ残し、途中で片方だけ更新された場合は同じrun操作で再開する。journalの内容は更新前台帳と予定レコードから再生成して一致を検査する。書籍外参照・未知の変更を上書きしない。

回収の既定はremote files保持。削除の明示許可を得た場合だけdelete-remote-filesを指定する。削除の部分失敗は成功済みIDを記録し、残りだけを再実行する。同じIDが404なら既にないものとして完了を記録する。事業者内部の全保持データ消去は保証しない。

## 旧入口の切替

旧Signal Stackのcommands/ocrの引数は保持し、domain/gateway/manifest/workflowと結果Skillの処理は共通実装へ委譲する。元のPython 3.11/Whisper環境は交換しない。旧CLIが3.11の場合は並置した.venv-ocrの3.12へ型付きJSONで委譲し、3.12以上の呼出し元は同じ環境の共通packageを直接呼ぶ。新規利用者に旧Signal Stackを要求しない。

適用用のhash付き8ファイルはtools/migration/signal-stack-compat/、適用スクリプトはtools/migration/apply-signal-stack-compat.py。未知の変更を検出したら停止する。元repoのAGENT.md/Git/利用先を確認後に使う。移管先の変更が即時に旧入口へ反映されるeditable導入は現在の開発用で、公開版の固定はI-11。

旧CLIのPDF/TIFF等は旧互換入口だけで維持し、pypdfをOCR専用環境へ導入する。Signal Libraryの登録済み新規OCRはJPEG/PNGに限定する。旧run/collectの引数契約を使う場合もkeep-remote-filesを付け、削除許可を別途確認する。個別の内部Python APIを3.11で全て互換にする目的ではなく、既存CLI/Skill入口を対象とする。

切替・検証・戻し方は[I-06作業記録](../release-status.md)。

## ライブラリ全体のコピー復元と再接続

初期化済みライブラリ全体を別の保存先へコピーした場合は、LIBRARY_ROOTをそのコピー先へ設定する。登録原本・library ID・run/operationの相対配置を保つ。管理OCRの絶対パスは自動変更せず、対象runごとに次を実行する。

```text
mise run library -- ocr rebind --manifest <コピー先rootからのmanifest相対パス> --json
mise run library -- ocr rebind --manifest <同じ相対パス> --apply --json
```

計画は旧rootと新しい入出力先を表示し、ファイル変更もAPI通信もしない。applyは登録原本hash・操作計画・run ID・モデル・capture対応を検証してから、現在の制御manifestのrun_dir/input_root（存在すればinput_path）を更新する。旧manifest/operationのバイト列をrun内path-bindingsへ保存し、hash付き履歴を追加する。job ID・custom_id・原画像・OCR本文・校正版・照合/採用・出力を変更しない。旧rootの参照や存在は不要で、旧rootへは書かない。旧Windows形式の絶対パスも読める。

処理中の.path-rebind.jsonがある間は通常の管理OCR入口を停止する。同じrebind --applyは原版と予定内容を再照合して中断箇所から再開する。未知のファイル、操作変更、原本変更、履歴改変は上書きしない。完成済みの同じ保存先はalready_boundとなる。さらに別rootへ移動した場合は前回履歴を保って追加する。

再接続後は同じmanifestのstatus/collect、送信結果不明なら同じoperationのrecoverを使用する。rebindを理由にsubmitし直さない。rebind自体は既存remote jobの存続やDropbox同期を実証しない。別途のstatus/collectは従来通りAPI通信を行い、失敗ページのretryは新しい対象別計画と許可を必要とする。旧形式の独立runを新たに取り込むimport-runとは目的が異なる。

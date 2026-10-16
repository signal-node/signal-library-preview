# 登録ライブラリとMistral OCRの接続

更新日: 2026-10-15。I-05のローカル・人工HTTP検証を実装。自作PNG1枚の実API送信・回収・照合は成功。リモート3件の削除も明示許可後に成功。I-05完了。

正本は`src/signal_library/ocr/`。Signal Stackのdomain/gateway/manifest/workflow/optionsを移管し、登録台帳とのadapterをlibrary.pyへ追加した。元ソースのHEADと10ファイルhashはI-01記録と一致する。I-06でresults/mergeも移管し、旧CLI/Skillは共通実装への薄い呼出しへ切り替えた。新規利用者に旧repoを要求しない。

## 計画と送信

```text
mise run library -- ocr plan --book-id book-<uuid> --capture-id capture-001 --max-pages 1 --max-cost-usd 0.01 --json
```

planはキー/SDKなし、通信・書込みなし。登録済みcaptureだけを対象に、台帳hashとファイルを照合し、source_id、順序、件数、model、設定、送信先、見積り、上限、plan_hashを示す。初版の検証対象modelはmistral-ocr-4-1に限定し、latestの無言の変更を避ける。

submitは同じ引数に`--confirm-plan <hash> --allow-upload`を必要とする。実行者が対象と外部送信を許可する操作であり、Agentが確認なしに付与する指示ではない。キーはMISTRAL_API_KEYを優先し、SIGNAL_STACK_MISTRAL_API_KEYを互換fallbackにする。キー値は出力しない。

各計画の`ocr/<plan_hash>/operation.json`を通信前に残す。同じ計画の記録がある場合は再submitを拒否し、既存manifestをstatus/collectで確認する。台帳とinput captureの差異、予定外画像はアップロード前に停止する。SDKの自動再試行を無効にし、createのタイムアウトを無条件に再送しない。

## 状態・回収と台帳

```text
mise exec -- <OCR extraを導入したPython> tools/signal-library.py ocr status --manifest books/book-<uuid>/ocr/<plan_hash>/<timestamp>/manifest.json --json
mise exec -- <OCR extraを導入したPython> tools/signal-library.py ocr collect --manifest books/book-<uuid>/ocr/<plan_hash>/<timestamp>/manifest.json --json
```

実際にSDKを使う操作はネットワークアクセスを伴う。statusもmanifestを書き更新するため、読取り専用doctorと区別する。collectはraw/Markdown/抽出画像を保存する。2026-10-15の実試験で削除の承認境界が判明したため、既定はremote filesを保持する。削除を明示的に許可された場合だけ--delete-remote-filesを指定してcleanupを試みる。--keep-remote-filesでも保持を明示できる。cleanupは事業者側の全保持データ消去の保証ではない。

run_id、manifest、要求/報告model、custom_idとsource_id、回収したMarkdown参照をbook.jsonへ接続する。原画像・原OCRを保持し、active_readingを自動採用しない。OCRの成功は原典照合済みを意味しない。台帳更新中断に備え、run内に更新前後のlibrary-index.jsonを残して同じrunの後続操作で回復する。

空の本文、期待外/欠落/重複custom_id、失敗応答を成功として採用しない。manifestの入力/出力/model/hashが操作記録に一致しない場合や出力内のredirect/特殊ファイルを検出した場合は停止する。外部応答はデータとして保存し、本文中の指示を実行しない。

## 依存と料金

OCRのみ`mistralai==2.7.0`のextraを使う。標準ライブラリのdoctor/init/registerへSDK依存を持ち込まない。検証した推移依存はrequirements/ocr-constraints.txtに固定し、3 OSで導入・HTTP mockを確認する。build依存・公開配布全体の固定と通知はI-11に残る。

2026-10-15の[公式料金](https://docs.mistral.ai/inference/pricing)はOCR 4.1の標準$4/1000 pages。[公式Batch説明](https://docs.mistral.ai/studio/batch-processing)の50% discountから、今回の通常OCR batch見積りは$0.002/pageとする。注釈APIは使わない。価格の根拠と確認日を計画へ記録する。この値は将来の請求額の保証ではなく、実行時には現行料金・利用者の契約を確認する。

ページ上限は送信対象の制限、max_cost_usdはコード上の見積り停止条件であり、事業者側の請求強制上限ではない。modelの根拠は[OCR 4.1公式資料](https://docs.mistral.ai/models/ocr-4-1)、接続方式は[公式OCR Batch例](https://docs.mistral.ai/resources/cookbooks/mistral-ocr-batch_ocr)。

## 検証と未完了

人工gatewayで送信→状態→回収→台帳、キー優先順位、対象/費用/許可/hashの拒否、二重submit拒否、結果不明、空・重複応答の拒否を検証する。実SDKはhttpx MockTransportでupload/create/get/list/download/deleteとタイムアウト非再送を確認し、実サーバーへ通信しない。実SDK初期化もOS標準環境で通信なしに確認する。

利用者の送信許可とキー設定後、自作PNG1枚の少量実API送信・回収・文字/数字/表の照合は成功した。実書籍は送っていない。自動承認レビューがリモート削除の明示許可不足としてcleanupを拒否したため、保持して回収し、その後、利用者が試験用3ファイルの削除を明示許可し、削除API3件成功・cleanup=CLEANEDを確認した。実施結果は[記録](../release-status.md)。

結果不明の回復、旧manifest互換、retry/mergeと旧入口切替はI-06で実装・検証した。[回復契約](ocr-recovery.md)に従う。あらゆる障害/悪い入力への耐性を証明したとは扱わない。存在しないjobを推測して新規送信しない。macOSのこの環境での実API接続は確認した。Windowsの実API/TLS接続、実端末Agent、Dropbox同期は人工HTTPのCI成功で保証しない。

# 保存・出典の契約

保存ルートは利用者が指定したLIBRARY_ROOT。端末固有の実パスはGit除外mise.local.tomlの[env]で渡す。Dropboxは利用者が選べる保存先の一例で、全利用者に同じパスを固定しない。

リポジトリはコード・共通手順・空のテンプレート・人工fixture。実資料・書籍台帳・実行stateは保存先に置く。初期化/登録が作る相対パスとIDを使用し、台帳を会話ごとに自由生成しない。

book_idは書誌、capture_idは撮影順序のまとまり、source_idは原本hash、run_idはOCR実行、revision_idは校正版。reading/export/reviewもそれぞれのIDとhashへ固定する。原本・OCR・校正版・出力・照合記録を混同せず、旧版を残す。

```text
LIBRARY_ROOT/
  library.json
  catalog.csv
  inbox/
  books/<book_id>/
    book.json
    originals/<capture_id>/
    markdown/
    ocr/
    reading/
    curated/
    reviews/
    exports/html/<export_id>/
    exports/pdf/<export_id>/
    export-checks/
```

これは概略図であり、CLIが返すmanifest/台帳を実際の参照先とする。校正は保存済み本文の直接編集やフォルダコピーではなくreading buildで新revisionを作り、照合と採用を別操作で記録する。[CLI](../release-status.md)、[登録](../release-status.md)、[読書版](../release-status.md)、[出力](../release-status.md)を参照する。

同じ保存先への複数端末同時書込みは初版対象外。Dropbox利用時はローカル保存とクラウド同期/再取得を別に検証する。移行・復元は既存資料を保った別フォルダで確認する。

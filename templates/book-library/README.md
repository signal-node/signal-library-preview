# 書籍台帳テンプレート

このフォルダは記入用の雛形。実資料・書誌・利用許可は含まない。空欄を埋める前の雛形は運用開始済みの証拠にならない。

1. 保存ルートは利用者指定の`LIBRARY_ROOT`。この所有者の運用例はDropbox直下の`Books`。初回資料を選ぶ。
2. 新規の書籍IDで`books/book-<uuid>/`を作り、`book.json`、各CSV、`checks.md`をコピーする。
3. 台帳に書誌、原本、使用中出力を登録する。`catalog.csv`は保存ルートに置く。
4. 検証結果を`pilot-checklist.md`へ記入する。

`review.csv`はページ/範囲ごとの原典照合記録。`status`は`unreviewed`、`needs_review`、`verified`。使用版が変わったら旧版の照合記録をそのまま新版へ移さず、影響する範囲を再確認する。

JSONのパスはすべて`LIBRARY_ROOT`からの相対パス。CSVはUTF-8、標準CSVとして表計算ソフトまたはテキストエディタで編集する。カンマや改行を含むセルはCSV規則に従って引用する。

`book.json`内の空の配列には、運用手順に沿って原本・処理・修正版を追加する。`active_reading`の`kind`は`ocr`または`curated`、`id`は採用run/revisionのID、`path`は採用Markdownの相対パス。初期値`null`は未採用。

`service_permissions`の各値は`pending`、`allowed`、`denied`。`allowed`にする際は`permission_notes`に利用者の判断、対象範囲、日付を残す。機密区分は`C0`〜`C4`の確認後に設定する。

原本レコードの項目: `capture_id / source_id / sha256 / path / file_pages / sequence / acquired_at / scope / derived_from`。
OCRレコードの項目: `run_id / manifest_path / model_requested / model_reported / documents`。documents内は`source_id / custom_id / markdown_path`。
修正版レコードの項目: `revision_id / based_on_run_id / source_ids / markdown_path / corrections_path / verified_at`。

原本レコードの記入例（値は実原本に置き換える）:

```json
{
  "capture_id": "capture-001",
  "source_id": "sha256:<64桁のハッシュ>",
  "sha256": "<64桁のハッシュ>",
  "path": "books/book-<uuid>/originals/capture-001/0001.jpg",
  "file_pages": 1,
  "sequence": 1,
  "acquired_at": "<タイムゾーン付き取得日時>",
  "scope": "<表紙または紙面ページ等>",
  "derived_from": null
}
```

画像の順序は撮影後に目視確認し、capture内で0001から連続番号を付ける。複数captureの境界をまたいで本文を統合する作業は初期補助ツールの対象外。初期運用では章単位のcaptureと修正版で管理する。

本文・原本・台帳の個人情報をこの仕様リポジトリへコミットせず、決定した書籍保存ルートで運用する。

手順: [運用Runbook](../../docs/operations/book-digitization-storage-runbook.md)

## 共通CLIによる登録

新規利用者向けには手動のJSON/CSV作成の代わりに共通CLIを使える。[登録計画の例](registration-plan.example.json)をGit対象外の作業場所へコピーし、実際の書誌・画像順を記入する。`order_confirmed`は画像を目視してから変更する。

```text
mise run library -- register --plan /absolute/path/to/plan.json --json
mise run library -- register --plan /absolute/path/to/plan.json --apply --json
mise run library -- books --json
mise run library -- validate --json
```

既存MDはmarkdown配列にpath、provenance、assetsを指定する。登録時のactive_readingは未採用。詳細と中断復旧は[登録の契約](../../docs/architecture/book-registration.md)。

## 読書版・校正版・照合記録

`reading build/review/adopt`を使うと、reading_bundlesに本文/資材/manifestのhashと状態、curated_revisionsにrevision、reading_reviewsに版を固定した照合記録JSONを追加する。照合内容はreview.csvとも対応する。保存済み本文や照合JSONを直接変更せず、新しい計画とIDで版・記録を追加する。

active_readingには既存のkind/id/pathに加え、reading_id、bundle_path、bundle_sha256を保存する。既存Markdown原本のkindはmarkdown。新規の読書版作成や照合だけでは採用版を変更しない。詳細は[読書版の契約](../../docs/architecture/reading-bundles.md)。

## ローカルHTMLと表示確認

共通CLIの`export html`は書籍台帳のlocal_exportsとexports.csvに元版・生成物のhashを登録する。exports.csvのgenerated_unverifiedは生成当時の記録。実表示の確認は`export check`でexport_checksへ別途保存し、`export status`で最新状態を見る。新出力へ旧表示確認を引き継がない。HTMLと資材フォルダを一緒に保管する。[HTMLの契約](../../docs/architecture/html-export.md)。PDFもlocal_exportsへ登録し、独立したPDF確認記録を残す。statusのfreshnessと表示状態は別で、regenerateは新IDに保存する。[PDF契約](../../docs/architecture/pdf-export.md)。

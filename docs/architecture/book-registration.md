# 書籍・原画像・Markdown登録の契約

更新日: 2026-10-15。I-04の正本は`src/signal_library/registration.py`。

## 操作

`register --plan <JSON> --json`は入力・書誌・順序・保存先を検査してbook_idと件数を返す。`--apply`でコピー・台帳反映を実行し、`--resume --apply`で同じ入力から中断を再開する。全操作で`--root`またはmiseが渡す`LIBRARY_ROOT`を使う。通信・API認証は不要。

`books --json`は一覧、`validate --json`は台帳・原本hash・宣言済み資材・参照ファイルの検査を行う。現時点では一覧も整合性を確認するため、原本hashを読む。大量資料での性能評価は未実施。

計画例は[テンプレート](../../templates/book-library/registration-plan.example.json)。書誌が不明なら空欄/nullまたはbibliography_unknownsへ理由を残し、推測で埋めない。書名は必須。通常の新規登録ではbook_idを省略でき、library_idと計画・入力hashから安定したUUIDを生成する。別の計画は別IDになるため、同じ版への追加は必ず一覧の既存book_idを明記する。別版・別巻は別ID。

画像は計画の配列順に0001から番号を付け、バイト列を変えずコピーする。拡張子とJPEG/PNGシグネチャを確認するが、画像の完全な復号・向き・画質・見開き・欠落は機械判定しない。Agent/利用者が画像を見て順序と範囲を確認し、`order_confirmed: true`を明示する。同一capture内および既存登録と同じhashの原本を拒否し、重複や意図しない別書籍への再登録を防ぐ。

撮り直しは既存book_idと新capture-NNNを指定する。既存captureを上書きしない。acquired_atは本ツールへの取得時刻であり、EXIFの撮影時刻ではない。元ファイル名をoriginal_filenameへ記録する。source_idはsha256に基づき、原本は改変しない。派生画像の由来は既存derived_fromを維持する。画像正規化機能の採用は別判断であり、本登録で補正しない。

## 既存Markdown

`markdown`にはpath、provenance（由来）、assets（本文からの相対資材パス一覧）を指定する。本文・資材は`markdown/sha256-<本文hash>/`へコピーし、本文名はsource.mdとする。資材の相対構造とバイト列を保持する。source_id・本文と資材のhash・由来・登録時刻をmarkdown_sourcesへ記録し、OCR runや校正版revisionのIDを捏造しない。active_readingは自動採用せず、unreviewedとして保持する。

assetsは明示リストであり、現在はMarkdownの全構文を解析して参照を自動抽出しない。validateが確認するのは台帳に宣言した資材であり、本文内リンクの完全性・安全なHTML表示の保証ではない。本文は未信頼のデータとして保存する。PDF/HTML生成前にはI-08のAST検査で全参照・生HTML/TeX・外部通信を検査する。Markdown登録が成功しても表示・引用用に確認済みとはしない。

## 台帳と境界

book-library-1と既存catalog列を維持し、markdown_sources・registration_operationsを拡張フィールドとして追加する。保存参照はLIBRARY_ROOT相対POSIX形式。原本、OCR、校正版、派生出力を混同しない。catalogは書籍台帳の検索用一覧として更新し、独立編集による不一致は停止する。

入力・保存パスのsymlink/junction、FIFO等の非通常ファイル、範囲外相対参照、Windows予約名・不正文字・末尾空白/ピリオド、大小文字とUnicode正規化の衝突を拒否する。現時点では祖先のredirectも拒否するため、macOSの/tmp・/var等の別名は/private側の実パスを指定する。登録計画は書籍データでありGitへ入れない。

ページ/出力CSVのヘッダ・列数、ページ対応の原本ID、capture内のsequence、原本ID/hash、登録資材hash、OCR/revision/採用版の参照を検査する。OCR manifest内部の全対応、校正版の照合記録、本文内の全リンクは後続単位で検査する。validateは原典照合やDropbox同期完了を意味しない。

## 中断と競合

ライブラリ共通の排他lockを使用する。登録前に`.signal-library-register.json`へ入力hash、保存内容、更新前の台帳を記録し、doctorはregisteringとして診断する。中断時はコピー済み資料を保持し、同じ計画・同じ入力で再開する。新規ファイルはhardlinkで排他公開、管理台帳は更新前の内容との一致を確認して原子的置換する。hardlink対応のローカル保存先が必要。

既存書籍への追加では、更新前book.json/pages.csv/catalog.csvを`metadata/<operation>/`へ残す。原画像・登録MDを上書きしない。失敗やWindowsの使用中ファイルの置換拒否ではjournalを残して止まり、入力や保存済み内容が変われば自動修復しない。journalから任意の保存内容を採用せず、登録計画と更新前台帳から再生成して一致を確認する。処理完了後だけjournalを削除する。

一連の処理はファイルシステム全体の一括transactionではない。中断中はbook.jsonとcatalogが異なる段階になり得るため、他操作を停止して再開する。強制終了ではlockや`.signal-library-copy-*`が残る可能性があり、自動削除しない。実行中処理の有無・journalとの対応を確認して退避し、再開する。電源断耐性・Dropbox別端末間排他・非協調プロセスの検査直後の置換を保証しない。

## 従来形式の採用

`adopt --json`は既存Booksを読取り検査し、`adopt --apply --json`はlibrary.jsonだけを追加する（inboxがなければ作成）。既存ID・相対参照・原本・OCR履歴を改編しない。実書籍での採用は今回実行していない。

catalogとbook.jsonが一致し、原本hash・ページ/出力CSV・宣言された本文参照を解決できる従来形式のみ対象とする。不整合・未知形式・絶対参照等は理由を返して停止し、一括変換や推測補完はしない。修復が必要なら対象差分を具体化して別作業として扱う。adoptの成功も旧OCRの再開互換性を保証せず、I-06でmanifest内部を確認する。

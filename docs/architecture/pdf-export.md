# PDF派生出力と共通の旧版判定（I-09）

登録済み読書bundle/校正版、または既存Markdown原本を、HTMLと共通の入力検査・ASTからPDFへ変換する。MistralキーとOCRは不要。原本、旧本文、旧出力を上書きしない。

```text
mise run library -- export pdf --book-id <ID> --export-id pdf-001 --json
mise run library -- export pdf --book-id <ID> --export-id pdf-001 --apply --json
```

入力の既定は採用版。`--bundle <相対bundle.json>`または`--markdown-id <source_id>`で別の登録版を選べる。固定Noto CJK/OFLはHTMLと同じ`export install-font`から導入し、取得済み資材は`--font-dir`で指定できる。PATH上にPandoc 3.12/3.12.1とTypst 0.15.1を要求する。製品にはバイナリ、pypdf、Popplerを同梱しない。

## 組版と保存

A4、余白20mm、本文10pt、固定日本語font、目次（深さ6）、ページ番号を初期設定とする。長い表は改ページし、ヘッダを繰り返す。画像は本文幅と高さ190mm以内へ収める。書名、元版ID、整形/原典照合状態を示す。PDFのページ番号は再組版後の番号で、原著の紙面番号とは扱わない。対象章は入力に選んだ登録本文の範囲となる。

組版の言語は書誌の基本言語（例: ja-JP→ja）を使い、未指定の場合はja。書誌自体は補完しない。外部bibliographyは読まず、@による引用表記は本文のinlineとして保持する。元の数式や図を説明文へ置き換えない。Raw/任意属性/外部画像をHTMLと同じ共通境界で検査する。未対応構文・文字・数式のエンジンwarningは停止条件。

本文・書名はPandoc writerで文字列としてエスケープし、本文由来のTypstコードやテンプレートを実行しない。検査済み資材だけを一時rootへコピーして組版する。system fontを無効化し、空のpackage/cache、固定timestamp、単一jobを指定する。TYPST_*環境設定は引き継がない。Typstのroot制限で範囲外ファイルも拒否する。[Typstの文書設定](https://typst.app/docs/reference/model/document/)、[ページ設定](https://typst.app/docs/reference/layout/page/)も参照。

`books/<ID>/exports/pdf/<export_id>/`へbook.pdf、source.md、検査後AST、source.typ、固定template.typ、使用画像、font/OFL、export.jsonを保存する。共通の排他公開・台帳更新journalから、同じ予定内容だけ再開できる。作成日時を固定してPDFを再生成し、同IDの再開時は全ファイルのhash一致を要求する。export_idは書籍内でHTML/PDFをまたいで一意。

構造検証はファイル一覧/hash、固定設定・font/OFL、元本文、PDF開始/終端を確認する。PDF構造検証のpassedは、全ページの組版・検索・選択の確認を意味しない。生成時のvisualはpending。実確認は別記録にする。

## PDF確認記録

HTMLと同じ`export check --manifest <相対export.json> --plan <JSON> [--apply]`を使う。PDF用schemaは`signal-library-pdf-check-1`。共通項目はcheck_id、checked_at（タイムゾーン付き）、reviewer、notes。browserの代わりにviewer（name/version/os）を記録する。methodはpdf_manual/pdf_page_images/user_report。不明なversionはunknown。

resultsはjapanese、images、math、tables、footnotes、toc、offline、copied_bundle、page_layout、page_numbers、text_selection、font_embeddingの12項目。値はpassed/failed/not_checked。目視画像、構造検査、対話操作の証拠をnotesで区別し、未確認を合格にしない。全passedでpassed、失敗があればneeds_review、それ以外はpartial。確認は対象manifest/PDFのhashへ固定し、新出力へ継承しない。

## HTML/PDF共通の旧版判定と再生成

```text
mise run library -- export status --book-id <ID> --json
mise run library -- export status --book-id <ID> --export-id <旧ID> --markdown-id <比較先ID> --json
mise run library -- export regenerate --book-id <ID> --from-export-id <旧ID> --export-id <新ID> --json
mise run library -- export regenerate --book-id <ID> --from-export-id <旧ID> --export-id <新ID> --apply --json
```

statusは現在の比較先と、動的なfreshness=current/stale、stale_reasonsを返す。比較先は明示指定→採用版→採用版がなければ元出力の登録版。comparison_basisはexplicit/active_reading/original_sourceで区別する。版・本文hash・資材・整形/照合状態・書誌・renderer設定の差を示す。safe-html-1の旧出力は新規既定safe-html-2に対してrenderer_settingsが異なる。表示確認済みでもstaleになり得るため、表示品質と鮮度は別の状態。

登録済み原本の直接改変は整合性エラーであり、staleとして黙認しない。変更した既存MDは新原本として同じ書籍へ登録し、変更画像も登録する。OCR由来の修正はreading buildで新revisionを作る。比較先は`--bundle`または`--markdown-id`でも指定できる。

regenerateは旧出力と同形式で、現在の入力/固定設定から別IDへ生成する。計画の既定は保存なし。`--apply`で保存・台帳登録する。旧ファイル/生成時CSV/確認記録は変更せず、表示確認はpendingから始める。同じ新IDの中断再開も内容一致の場合だけ許可する。新旧関係は返り値regenerated_fromと入力版記録で確認する。公開・外部送信は行わない。

## 検証の範囲

人工資料の既存MD（3ページ）とOCR由来校正版（1ページ）を全ページ画像で確認。日本語、図、数式、表、脚注、目次、原著と異なるPDFページ番号を確認した。80行表のヘッダ再掲・末尾、文字のページ外逸脱0、font埋込み、検索用文字抽出を検査。コピーしたPDF単体をネットワーク拒否したPopplerで描画した。既存MD版はmacOSプレビューで検索・文字選択・目次移動も確認。OCR版の対話操作は未確認としてpartialに残した。Windows GUIや全閲覧ソフトを保証する証拠とはしない。

[実施記録](../release-status.md)。製品の確認用ツールは利用者/Agentが別途選べる。任意の長大な数式や複雑な表についても、未確認の出力はvisual pendingのままページ画像を確認し、欠けがあれば入力範囲/組版を調整して新IDで生成する。

## 日本語の版・照合表示

safe-pdf-2は使用版・原典照合・整形のラベルを日本語で表示する。範囲別の記録を全文照合済みと誤表示しない。safe-pdf-1の再開・検証では旧表示と生成内容を維持し、新規出力だけ新版の表示を用いる。旧PDFはrenderer_settingsによるstaleとなり、旧版・確認記録を保持して新IDで再生成する。

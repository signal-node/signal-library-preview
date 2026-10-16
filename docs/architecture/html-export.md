# HTML派生出力の契約（I-08）

登録済み読書bundleまたは既存Markdown原本から、ローカルのHTMLと資材束を作る。キー・OCR・外部通信は不要。採用版を省略時の入力とし、明示的に別の未校正版も選べる。既存MDに架空のrun_idや原画像対応を追加しない。

```text
mise run library -- export html --book-id <book_id> --export-id html-001 --font-dir <固定fontのディレクトリ> --json
mise run library -- export html --book-id <book_id> --export-id html-001 --font-dir <同じディレクトリ> --apply --json
```

入力を選ぶ場合は`--bundle <LIBRARY_ROOT相対bundle.json>`または`--markdown-id <登録済みsource_id>`を指定する。未採用かつ入力省略の場合は停止する。Noto Sans CJK JP Regular Sans2.004の`NotoSansCJKjp-Regular.otf`と元OFLの`font-LICENSE.txt`を固定hashで確認する。省略時の確認場所はLIBRARY_ROOT内`.signal-library-assets/noto-cjk-Sans2.004`。

## fontの導入

```text
mise run library -- export install-font --json
mise run library -- export install-font --apply --json
mise run library -- export install-font --source-dir <取得済みfont/OFLのディレクトリ> --apply --json
```

既定はURL・hashを示す計画のみ。--applyで公式の固定URLからfont/OFLだけを取得する。書籍や設定は送信しない。--source-dirを指定した場合は通信せず、取得済み資材を検査して導入する。導入済みなら検査して再利用し、再取得しない。部分保存も共通の排他公開記録から回復する。未知ファイルや違うhashを上書きしない。

## 変換と表示の境界

対応エンジンは選定済みPandoc 3.12と、ローカル導入を確認した3.12.1。PATH上の外部CLIを使い、バイナリは同梱しない。公式の[安全性に関する説明](https://pandoc.org/MANUAL.html#security)に従い、sandbox、時間/heap上限、警告時停止を使う。sandboxだけで生成HTMLが安全になるとは扱わない。

本文はstdinでMarkdown→JSON AST→HTML5/MathMLへ変換する。テンプレート/実行オプションを本文から取得しない。Raw HTML/TeX/属性を無効にする。新規出力（safe-html-2）はYAML metadata/title blockをデータとして解析し、ASTのmetadataを空にして本文/変換設定へ渡さない。書名・言語は登録済み書籍台帳から取得する。既存safe-html-1の再実行は従来の読取り規則を保持し、旧ファイルを変更しない。新しい読取り規則へ切り替える場合は新export_idを使う。任意属性は除去し、ローカル資材と文書内アンカー、http/httpsリンクだけを許可する。出力HTMLでも要素・属性・URL・文書内ID・リンクの対応を検査する。危険な要素を黙って実行しない。未対応の数式・警告は完了扱いしない。

図は登録済み資材だけを読み、hashで確認して内容hashの相対ファイル名へ書き換える。JPEG/PNG/GIF/WebPは形式の識別子を検査する。SVGはUTF-8と許可した要素/属性に制限し、script・外部参照・foreignObject・処理命令・CSSの外部参照/エスケープ/コメント・DTD等を拒否する。独自CSS/SVG構文は今回の対応範囲に含めない。未登録の画像・範囲外パス・redirect・欠落は停止する。

MathMLを使い、外部CDN/JavaScriptを使わない。目次、章内リンク、脚注、表、図、書名、言語、使用版と確認状態を保持する。幅の狭い画面では画像を縮小し、長い表/数式/コードは内容を切らずスクロール可能にする。CSPでscript・外部接続・object等を抑止する。表の配置に必要な限定styleは出力検証を通して扱う。ローカルfileの資材読込み/CSPの挙動は、静的検査だけで表示成功とせず実ブラウザで確認する。

## 保存と再開

`books/<book_id>/exports/html/<export_id>/`へ保存する。index.html、style.css、font/OFL、参照画像、source.md、source.json、export.jsonを含む。manifestに元版/本文/資材のhash、設定、エンジンversion、作成日時、全ファイルのhash、構造/表示検証状態を記録する。

原本や旧出力を上書きしない。公開処理は読書bundleと共通のartifacts実装を使い、全ファイルを排他公開してmanifestを最後に書く。中断した出力は同じ入力・設定・エンジン・作成日時からの再生成が一致する場合だけ再開する。別内容は新export_idを使う。既存出力の同内容での再実行は冪等。

生成当時の表示状態はpending。構造検証は画像/CSS/font/hashとHTMLを検査するが、実ブラウザの目次操作・数式/図・狭い画面・コピー先の表示を代替しない。I-07でローカルfile://を開く操作はブラウザツールのURL方針で拒否された。迂回せず、I-08では利用者が手元のブラウザで確認した結果をuser_reportとして記録する。確認対象と未確認項目を分け、browser名/version/OSの未申告値はunknownとする。

exports.csvと書籍台帳のlocal_exportsへ生成物を登録する。更新前/予定内容のjournalから台帳更新中断を回復し、本文・manifest・CSVの対応をvalidateで検査する。stale判定/新ID再生成はI-09で共通実装した。[PDFと出力管理](pdf-export.md)を参照。HTML/PDF機能全体の要件は[派生出力設計](reading-exports.md)。

## 表示確認の記録

```text
mise run library -- export check --manifest <LIBRARY_ROOT相対export.json> --plan <確認計画JSON> --json
mise run library -- export check --manifest <同じexport.json> --plan <同じ計画> --apply --json
mise run library -- export status --book-id <book_id> --export-id <export_id> --json
```

実際に確認した人・日時・browser/OS・方法と9項目の結果を別JSONへ固定する。methodはbrowser_manual/browser_automated/user_report。browserのname/version/osが不明ならunknownと記録し、推測で補わない。resultsにはjapanese/images/math/tables/footnotes/toc/offline/copied_bundle/narrow_layoutをすべて含め、passed/failed/not_checkedで指定する。未確認はnot_checked。全項目passedでpassed、failedがあればneeds_review、それ以外はpartial。CLIは実表示を自動確認したことにしない。

```json
{
  "schema_version": "signal-library-export-check-1",
  "check_id": "check-001",
  "checked_at": "2026-10-16T12:00:00+09:00",
  "reviewer": "実際に確認した人",
  "method": "browser_manual",
  "browser": {"name": "unknown", "version": "unknown", "os": "unknown"},
  "results": {
    "japanese": "not_checked", "images": "not_checked", "math": "not_checked",
    "tables": "not_checked", "footnotes": "not_checked", "toc": "not_checked",
    "offline": "not_checked", "copied_bundle": "not_checked", "narrow_layout": "not_checked"
  },
  "notes": "対象と確認内容・問題点を記入"
}
```

対象manifestとHTMLのhashに紐づけ、export-checksと書籍台帳へ追加する。生成当時のmanifest/exports.csvの状態は書き換えない。export statusは最新日時の確認結果を合わせて返す。同日時なら後に追加した記録を使用する。新出力へ旧出力の表示確認を引き継がない。HTML表示の確認と、書籍本文の原典照合は別の状態である。

## 日本語の版・照合表示

safe-html-3は使用版・原典照合・整形のラベルを日本語で表示する。範囲別の記録の存在は「範囲別の照合記録あり」とし、全文照合済みと表示しない。safe-html-1/2の再開・検証は旧表示を維持し、既存出力・確認記録を上書きしない。新rendererとの差はstaleとして新IDの再生成へ進む。本文readerと安全条件はsafe-html-2と同じ。

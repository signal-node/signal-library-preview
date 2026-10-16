# 読書版・照合記録・採用版の契約

I-07完了。CLIと保存・照合・採用管理を接続した。人工紙面比較と3 OS回帰は[完了記録](../release-status.md)を参照。製品HTML/PDFの表示受入はI-08/I-09で行う。

## 作成と校正

登録済みの通常/結合runを対象とする。capture全件の正常出力・原本hash・custom_id・Markdown位置を検証する。部分retryは単独で統合せず、先に`ocr merge`する。外部通信やキーは不要。

```text
mise run library -- reading build --manifest <LIBRARY_ROOT相対manifest.json> --reading-id reading-001 --plan <整形計画JSON> --json
mise run library -- reading build --manifest <同じmanifest> --reading-id reading-001 --plan <同じ計画> --apply --json
mise run library -- reading build --manifest <同じmanifest> --kind curated --reading-id revision-001 --plan <更新した整形計画> --supersedes <前版の相対bundle.json> --apply --json
```

保存先は`books/<book_id>/reading/<reading_id>/`または`curated/<reading_id>/`。本文、図画像、source-map.json、corrections.json、curation-plan.json、元manifestのスナップショット、bundle.jsonを保存する。図は内容hashのファイル名と相対リンクで移動可能にする。行番号から原本IDとfile_pageへ戻れる。ページをまたぐ結合には両原本を記録する。

新revisionは元OCRへ**全対象の整形計画**を再適用して作る。前版の計画をコピーして必要な修正を追加する。`supersedes`は前版のパス/hashによる履歴関係であり、前版の編集や照合結果を自動継承する指定ではない。保存済みreading.mdを直接編集せず、計画のreplacementsに対象・変更後・理由を記入する。原文の誤植、説明・要約等はOCR誤りと区別した理由を記録する。解説・要約を校正本文へ混ぜない。

整形計画の例:

```json
{
  "schema_version": "signal-library-curation-1",
  "headings": [{"before": "### 第1章", "after": "## 第1章", "reason": "原典の章階層"}],
  "pages": [{
    "source_id": "sha256:<原画像hash>",
    "remove": [
      {"location": "header", "text": "書名", "reason": "原典の柱"},
      {"location": "footer", "text": "1", "reason": "紙面番号"}
    ],
    "replacements": [{"before": "誤認識", "after": "正しい文字", "reason": "原画像の本文と比較"}],
    "expected_figures": 1,
    "expected_tables": 1,
    "header_footer_confirmed": true,
    "paragraphs_confirmed": true
  }],
  "join_pages": [],
  "unresolved": []
}
```

柱/フッタの除去はページ端の完全一致、修正はページ内の一意一致だけを受け付ける。見出し対応は同時に適用し、変換の連鎖を防ぐ。図の参照は元OCR Markdownからの相対パスで指定する。数式・コード・英語・数値区切り・参照先を保護し、日本語読点と通常段落の折返しを整える。ページ間の結合は`left_source_id/right_source_id/expected_left_suffix/expected_right_prefix/reason`を明示する。

図表数や柱/段落の確認が不足、見出し階層やHTML図表が未解決なら`format_status=needs_review`。未完了でも保存して調査できるが採用できない。整形完了は原典照合済みを意味しない。7要件は[整形要件](curated-markdown.md)を参照。

## 照合と採用

```text
mise run library -- reading review --bundle <相対bundle.json> --plan <照合計画JSON> --json
mise run library -- reading review --bundle <同じbundle> --plan <同じ計画> --apply --json
mise run library -- reading adopt --bundle <相対bundle.json> --json
mise run library -- reading adopt --bundle <同じbundle> --apply --json
```

照合計画の例:

```json
{
  "schema_version": "signal-library-reading-review-1",
  "review_id": "check-001",
  "reviewer": "照合者",
  "checked_at": "2026-10-16T12:00:00+09:00",
  "notes": "本文の対象範囲を比較",
  "ranges": [{
    "source_id": "sha256:<原画像hash>",
    "file_page": 1,
    "region": "第1段落",
    "scope": "text",
    "status": "verified",
    "compared_with_original": true,
    "notes": "数式・図表は今回の対象外"
  }]
}
```

scopeは`full/text/math/figures/tables`、statusは`unreviewed/needs_review/verified`。verifiedは原画像との比較の明示が必要。CLIは実際に目視した事実を自動証明しない。照合者が比較を行ってから記録する。原本位置/hash、bundleと本文のhashを固定したJSONを`reviews/`に保存し、review.csvと台帳へ接続する。部分照合を全体verifiedへ昇格しない。新しい版へ旧版の照合結果を引き継がない。

採用は別操作。整形が完了した登録済み版だけをactive_readingで選択する。未照合版も読書用に選べるが、照合記録の有無を結果へ表示する。引用・カード化の範囲は別途原典照合する。照合や版作成だけで現在の採用版を切り替えない。

## 中断・変更・検証

ファイルは排他公開し、bundle.jsonを最後に書く。途中停止は、入力から再計算した公開記録が保存済みjournalと一致する場合だけ再開する。未知の既存フォルダ、未知ファイル、別計画、変更済み入力/出力は拒否する。ファイル公開前にフォルダ作成のみが残った場合は自動採用せず、空であることを確認して新しいIDへ切り替える。

台帳更新は更新前・予定内容をjournalへ保存してからbook.json/catalog.csv/review.csvを公開する。同じ`--apply`操作で復旧する。読取り専用計画では中断journalを変更しない。異なる操作へ進む前に中断した操作を回復する。library lockで同時更新を止める。

`validate`はbundle内全ファイルのhash、台帳との対応、原本対応、照合記録JSON/hash、review.csvの対応、採用版を検査する。旧版・原OCR・原画像は上書きしない。bundle.json自体のhashは書籍台帳へ保存する。これは署名・攻撃者による台帳全体の改ざん検知を提供するものではない。

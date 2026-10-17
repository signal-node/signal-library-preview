# Signal Library

Signal, Not Volume。書籍の原画像、OCR、校正版と読書用PDF/HTMLを版ごとに管理するローカルツール。

この版は0.1.0の初版（限定サポート）です。本体はMITライセンスを採用。確認環境と未確認事項は[対応範囲](docs/known-limitations.md)に記載しています。書籍・画像・OCR全文・APIキーは含めない。利用者が自分の保存先とアカウントを使う。外部サービスの公式製品ではない。

## 始める

Git/miseとPython 3.12.13を用意し、このフォルダをCodexまたはClaude Codeで開く。「本を管理したい。保存先は〈絶対パス〉。初期化してください」と伝える。共通CLIを使う[Agent手順](docs/agent-workflow.md)と[初期設定](docs/setup.md)を参照する。所有者のGitHubアカウントや旧Signal Stackは必要ない。

```text
mise trust
mise install python
mise run library -- doctor --json
```

保存先をGit除外mise.local.tomlの[env].LIBRARY_ROOTへ設定し、initの計画→applyで初期化する。既存資料を消して初期化しない。

- JPEG/PNGを順序確認して登録し、Mistral OCRの計画と対象/上限を確認して送信する。キーは利用者の端末非表示入力で設定する。
- 原画像と比較して整形・校正し、新revisionと照合範囲・採用版を保存する。OCR成功だけで校正済みにしない。
- キー/OCRなしで既存MDも登録できる。Pandocと固定Noto CJK、PDFではTypstを追加し、HTML/PDFをローカル生成する。
- 変更後はstale理由を確認し、新IDで再生成する。原本・旧本文・旧出力・確認記録を保持する。

## 対応範囲と未確認

macOS/WindowsとCodex/Claude Codeを対象とします。共通CLIは3 OS CIで検証し、macOS Codexはキーなし導入/出力と回収済み人工OCRの整形/校正を実対話で確認しました。人工資料のDropbox同期/Web再取得も確認済みです。

**Windows実端末、Claudeの実対話、実キー設定から新規実OCRまでのAgentによる一巡は未確認です。** 未確認の組合せは実験的対応です。全経路の実Agent受入成功とは扱いません。[未確認事項](docs/known-limitations.md)と[受入判定](docs/acceptance/release-0.1.0.md)を確認してください。

同じ保存先への複数端末同時書込み、複数captureの自動本文統合、Notion/NotebookLM搬送、Gemini TTSは初版対象外。日本語の数式・図表は必ず利用範囲を原典/生成ページと比較する。出力の表示確認も原典照合とは別の記録。

## 利用条件と更新

資料と外部APIを利用できる範囲は各利用者が確認する。API料金の見積りは請求停止の保証ではない。キーは設定ファイルに平文保存され、Git除外でもクラウド同期対象になり得る。[外部依存](THIRD_PARTY_NOTICES.md)、[障害報告](SUPPORT.md)、[更新](docs/updates.md)を参照。

本体のコード・スキル・共通文書・空の雛形・人工試験資料は[MITライセンス](LICENSE)です。著作権・許諾表示を保持して利用・改変・再配布できます。外部依存と利用者の書籍資料は、それぞれ別の権利・条件に従います。

利用者向けの文書とCLI案内は日本語です。コマンド名・JSON契約・製品名は保持し、AI向け指示文は英語でも構いません。LICENSEは英語原文を正本とします。

共有する情報と除外範囲は[個人情報と公開対象](docs/privacy.md)を参照してください。

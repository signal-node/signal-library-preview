# 利用者の初期設定

対象: macOSとWindowsネイティブ環境。Windows実端末、Claudeの実対話、実キー設定から新規実OCRまでのAgentによる一巡は未確認で、該当する組合せは実験的対応です。[確認環境と未確認事項](known-limitations.md)を先に確認してください。macOSローカルとmacOS/Windows/Linux CIで人工データを検証済み。実Agent端末・Dropbox同期の受入はI-10で行う。OCR正常系/回復、読書版・校正・照合/採用、HTML/PDFと出力版管理まで実装済み。両Agentの入口は[共通手順](agent-workflow.md)、実対話/同期の証拠は[受入記録](acceptance/agent-onboarding.md)。

## 依存を用意する

Gitとmiseを導入してリポジトリをcloneし、そのルートへ移動する。一般利用にGitHub CLIや所有者のアカウント設定は不要。公開配布先は[signal-library-preview](https://github.com/signal-node/signal-library-preview)。利用するtag/versionと[変更履歴](changelog.md)を確認して取得してください。開発正本の非公開リポジトリへのアクセス権は利用に不要です。

miseがなければ[公式導入手順](https://mise.jdx.dev/installing-mise.html)を使う。WindowsのPowerShellでは`winget install jdx.mise`、macOSでHomebrewを利用している場合は`brew install mise`を使える。これは導入例であり、Agentが暗黙に端末全体へインストールする指示ではない。導入後は必要に応じて端末を開き直し、`mise --version`で確認する。

この手順はmise 2026.10.2で検証した。キー設定には`mise set --stdin`を使用するため、この版を基準とする。シェルのactivationは必須ではなく、以下の`mise run`/`mise exec`を使う。

```text
mise trust
mise install python
mise run library -- doctor --json
```

`mise install python`はmise.tomlのPython 3.12.13だけを導入する。現時点のCLIは標準ライブラリだけで動くためpip、uv、仮想環境、Mistral SDKは不要。Pythonが既にある場合は`python tools/signal-library.py --version`で入口を確認できるが、保存先設定を反映した通常実行にはmiseを使う。

doctorの終了値1は、未設定や保存先の確認が必要な状態でも返る。キーがないだけではローカル操作を止めない。Mistral SDKとPandoc/Typstは機能別の診断情報。出力エンジンは対応版まで確認する。optional_missing/optional_incompatibleは初期化や登録を止める理由ではなく、該当機能を使う時に解消する。OCR実APIは自作PNG1枚で確認済み、一般の書籍の送信許可を意味しない。

## 保存先を設定する

新しい端末で`mise.local.toml`がなければ`mise.local.toml.example`をコピーする。既存ファイルがある場合は上書きしない。`[env]`内の`LIBRARY_ROOT`に保存先の絶対パスを設定する。WindowsはTOMLのエスケープ誤りを避けるためスラッシュのパス、または単一引用符の文字列を使う。

```toml
[env]
# Windowsの記入例。実際の保存先に置き換える。
LIBRARY_ROOT = 'C:\Users\example\Dropbox\Books'
# macOSの記入例。
# LIBRARY_ROOT = '/Users/example/Dropbox/Books'
```

Dropbox以外の利用者指定フォルダも使える。保存先とこのリポジトリを重ねない。親フォルダは先に用意する。

```text
mise run library -- init --json
mise run library -- init --apply --json
```

最初は検査、次が初期化。未知の非空フォルダや従来のBooksは拒否する。既存資料を削除して初期化を通してはいけない。doctorの`legacy_unmanaged`は従来形式の候補を検出した状態で、登録済み書籍の検証完了ではない。詳細検査はvalidate、採用はadoptの別操作として実装した。[登録・採用の契約](architecture/book-registration.md)を参照する。初期化にはhardlinkを作成できるローカル保存先（APFS/NTFS等）が必要で、非対応の場合は上書きに切り替えず停止する。中断時は[復旧の契約](architecture/library-cli.md)を参照する。

## Mistralキーを設定する

利用者が自分のローカル端末で、リポジトリルートから実行する。APIキーをチャット、引数、シェルへの代入文に貼らない。

```text
mise run library -- configure-key --json
```

非表示プロンプトに入力する。入力を非表示にできない環境では保存せず停止する。キーはGit対象外の`mise.local.toml`の`[env].MISTRAL_API_KEY`へ保存される。Git追跡済み/除外未設定/symlinkのファイルは変更しない。既存設定は保持し、設定済みキーの更新だけは`configure-key --replace --json`を使う。

設定は平文。macOSでは作成ファイルを0600に制限する。Windowsのアクセス権は親フォルダから継承されるため、自分のユーザー領域で使用し、共有ユーザーからのアクセス権を確認する。リポジトリ自体をDropboxに置いた場合、Git除外でもローカル設定はDropbox同期対象になり得る。

保存後、新しいプロセスで`mise run library -- doctor --json`を実行する。「設定済み」はキーの有無で、認証成功を意味しない。オンライン認証/OCR送信はこの設定では行わない。OCRは`MISTRAL_API_KEY`を優先し、旧`SIGNAL_STACK_MISTRAL_API_KEY`をfallbackとして使う。

レビュー時に`mise env`/`mise set`の一覧やローカル設定本文をAgentへ読み出させない。キーの有無はdoctorを使用する。

設定途中で強制終了した場合は`.signal-library-config.lock`や`.signal-library-config-*/`が残る可能性がある。実行中の処理がないことを確認し、一時設定を表示せずに除去して再実行する。秘密を含む一時ファイルを作業ログへ添付しない。

実装はmise標準の[TOML編集コマンド](https://mise.jdx.dev/cli/set.html)を標準入力で呼び出す。CLI独自の環境変数ローダーは持たない。

## OCR依存を別途導入する

登録・診断は引き続きmiseのPythonだけで使える。OCR実行には、同じPython 3.12から隔離環境を作り、固定版extraを導入する。キーはmiseから渡し、仮想環境内へ別コピーしない。

```text
mise exec -- python -m venv .venv
```

macOS:

```text
mise exec -- .venv/bin/python -m pip install -c requirements/ocr-constraints.txt '.[ocr]'
mise exec -- .venv/bin/python tools/signal-library.py doctor --json
```

Windows PowerShell:

```text
mise exec -- .venv/Scripts/python.exe -m pip install -c requirements/ocr-constraints.txt '.[ocr]'
mise exec -- .venv/Scripts/python.exe tools/signal-library.py doctor --json
```

以降のocr submit/status/collectも同じ仮想環境のPythonで共通CLIを呼ぶ。mise run libraryの標準PythonへSDKが導入されたとは扱わない。操作と料金・許可の境界は[OCR接続の契約](architecture/ocr-library.md)。macOSでは自作PNG1ページの実API送信・回収・照合まで確認済み。回収は既定でリモートファイルを保持し、削除は別途の許可と--delete-remote-files指定を必要とする。

## HTML/PDFの出力依存

OCRや初期化だけなら出力エンジンは不要。HTMLにはPandoc 3.12または3.12.1、PDFには加えてTypst 0.15.1を使う。PATH上に対応CLIがなければ、例えばmise経由で次のように指定する。固定font/OFLは`export install-font`の計画を見て`--apply`で導入する。取得済みfont/OFLの`--source-dir`導入も可能。

```text
mise exec pandoc@3.12 typst@0.15.1 -- python tools/signal-library.py export pdf --book-id <ID> --export-id pdf-001 --json
```

同じ入力/引数に`--apply`を付けると保存する。両Agentの入口は[共通手順](agent-workflow.md)。実対話の受入状態は[記録](acceptance/agent-onboarding.md)を確認する。仕様は[HTML](architecture/html-export.md)、[PDF/旧版判定](architecture/pdf-export.md)。

## 保存先のコピー復元

ライブラリ全体をコピー/再取得したらLIBRARY_ROOTを新しいrootへ設定し、books/validateで台帳と原本を確認する。管理OCRが旧絶対パスを示す場合は`ocr rebind --manifest <相対パス> --json`で計画を確認し、`--apply`で原版を残して再接続する。既存runの再submitや初期化し直しは不要。[再接続と中断回復](architecture/ocr-recovery.md#ライブラリ全体のコピー復元と再接続)を参照。ローカルコピーの成功とDropboxクラウド同期の確認は別に記録する。

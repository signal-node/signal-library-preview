# 共通CLIと初期化の契約

更新日: 2026-10-06。I-02、I-03の実装契約。

## 入口と設定

`mise run library -- doctor --json`を共通入口とする。依存なしの起動スクリプトは`tools/signal-library.py`、処理の正本は`src/signal_library/`。Python 3.12以上を使用する。通常利用にGitHub認証は不要。パッケージ導入後は`signal-library`でも同じ実装を呼ぶ。I-11で候補だけからwheelをbuildし、空の仮想環境へのオフライン導入と実CLIの初期化・登録・検証を確認した。

設定はmiseが渡した環境変数を読む。独自のdotenv/TOMLローダーを追加しない。`--root`はこの操作だけの指定であり、ローカル設定を書き換えない。`config/library.toml`は運用方針で、ランタイム設定として自動読込しない。

`doctor`は読取りのみ、通信なし。キーは`MISTRAL_API_KEY`または既存互換の`SIGNAL_STACK_MISTRAL_API_KEY`の有無だけを返す。認証成功・Dropbox同期・実際の書込可否はこの診断で保証しない。Pandoc/Typstは対応版まで検査し、機能別の任意依存として返す。診断成功は全出力の組版/表示確認済みを意味しない。

`--json`の結果は`result_schema=signal-library-cli-1`、command、version、status、next_actionsを共通に持つ。終了値0はコマンドの検査/操作成功、1は対応必要/操作拒否、2は引数エラー。`init`の計画成功は初期化完了を意味しない。`files_written=null`は失敗までに書き込まれた可能性がある状態。

## 初期化

キーの設定は`configure-key`で端末の非表示入力を使い、miseの`set --stdin`でGit対象外のローカル設定へ書く。TOMLの検証用読込は行うが、実処理への環境変数供給はmiseへ任せる。一時コピーで更新して既存設定の保持を検証し、競合がなければ置き換える。子プロセスの出力と例外本文は表示しない。一時コピーの信頼・cache/stateはその一時領域内に限定し、ユーザーのmise信頼stateは変更しない。詳細は[初期設定](../setup.md)。

```bash
mise run library -- init --root /absolute/path/to/Books --json
mise run library -- init --root /absolute/path/to/Books --apply --json
```

先頭は検査のみ、`--apply`で新規作成する。空の保存先、または既存の親フォルダ直下に作る保存先だけを扱う。日本語・空白パスに対応する。コードリポジトリとの包含関係、未知の非空フォルダ、未知schema、symlink/junctionの保存先・管理ファイルを拒否する。

作成するのは`library.json`（schema_version、library_id UUID、created_at）、`inbox/`、`books/`、既存テンプレートと同じ列の`catalog.csv`。書籍単位の`book-library-1`を変更しない。ID・日時はライブラリ作成時のもので、書籍/原本/run/revisionのIDとは独立。

同じ初期化は資料を変更せず成功する。`storage.py`の共通検査で、管理JSONのフィールド集合、正規形UUID、タイムゾーン付き日時、重複キー、64KiB上限、通常ファイルであることを確認する。CSVはUTF-8・既定ヘッダ・全行の列数を検査する。これは台帳内の書籍ID・参照先・hashの整合性検証ではない。

従来のBooksで`books/`と形式の合う`catalog.csv`を検出した場合、doctorは`legacy_unmanaged`を返し、initは変更を拒否する。書籍台帳の詳細検証と採用・移行はI-04で別操作として扱い、自動的な採用や再編をしない。中断記録が残る場合は`initializing`、ロックが残る場合は`locked`とし、管理ファイルが揃っていても`ready`にしない。

書込み前に`.signal-library.lock`を排他作成する。ローカルの協調するプロセスの競合検知であり、Dropboxの別端末や他の編集ツールを排他しない。初版は同じ保存先への複数端末同時書込みを扱わない。

## 中断と復旧

初期化内容を`.signal-library-init.json`へ記録し、完了後に削除する。処理例外でも、自分が取得したロックが同じ保存先に残っている場合だけ解放する。別プロセスに置換されたロックは削除しない。記録があり、内容と作成済みファイルが予定どおりなら`init --resume --apply`で再開できる。

管理ファイルは同じ保存先の一時ファイルへ書込み・fsync後、hardlinkによって完成内容だけを排他公開する。既存ファイルを上書きするrenameへのfallbackは行わない。APFS/NTFS等、hardlinkを作成できるローカル保存先が必要であり、非対応のファイルシステムやオンラインのみの仮想保存先では停止する。全てのDropbox構成が対応するとは保証しない。

ロック取得後に保存先と中断記録を再検査し、完了直前にも予定外資料・管理ファイル・中断記録の変更を検査する。競合する編集を検出した場合、資料を残して停止する。協調しない別プロセスによる検査直後の置換まで防止するものではない。

プロセス強制終了でロックが残った場合は、自動削除しない。実行中の処理がないこと、対象保存先と中断記録を確認してから残留ロックを退避する。強制終了で`.signal-library-init-*.tmp`が残る場合も未知ファイルとして停止し、内容を確認・退避してから再開する。JSON/CSVが壊れている場合、未知の資料が追加された場合は自動復旧しない。該当物を保存して内容を確認する。初期化は既存ファイルを上書きしないため、部分ファイルの修復も暗黙には行わない。

## 検証範囲

人工一時フォルダで新規、再実行、非空拒否、コードとの重なり、競合ロック、中断再開、既存ファイル保持、未知schema/symlink、管理JSONの境界、FIFOの読込拒否、CSV全行、途中での記録変更・資料追加・ロック置換を検証する。キー設定は実miseを人工キーで実行し、既存設定保持と出力への秘密混入防止を確認する。

I-03ではmacOSローカル34件とmacOS/Windows/Linux CIが成功した。WindowsのPOSIX FIFO試験は対象外。実Booksにはdoctorのみ実行し、変更していない。強制終了・電源断・Dropbox同期や別端末同時編集、実Agent端末での非表示入力は未検証。パッケージ導入の後続検証はI-11の記録を参照する。CIのOS検証と実利用者の受入を区別する。結果は[I-03実施記録](../release-status.md)。

## I-04/I-05の追加（2026-10-15）

登録・旧形式の検査と採用は[登録契約](book-registration.md)、OCR計画・送信・回収は[OCR契約](ocr-library.md)を参照する。従来Booksは初期化での採用を引き続き拒否し、別のadoptで検査済み管理マーカーだけ追加する。登録journalがある場合はdoctorがregisteringと診断する。OCR planは読取り専用、submit/status/collectは外部通信と書込みを伴う。

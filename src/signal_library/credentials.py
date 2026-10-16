"""端末で非表示入力し、mise標準のローカル設定へ保存する。"""

import getpass
import os
import shutil
import subprocess
import tempfile
import tomllib
import warnings
from pathlib import Path


class CredentialError(Exception):
    pass


def configure_mistral(repository: Path, replace: bool = False) -> dict:
    target = repository / "mise.local.toml"
    if not (repository / "mise.toml").is_file():
        raise CredentialError("リポジトリのルートで実行してください。")
    if target.is_symlink():
        raise CredentialError("ローカル設定がsymlinkのため変更できません。")
    # ignore指定だけでなく、すでに追跡された設定も拒否する。
    for arguments, expected in ((["check-ignore", "--no-index", "-q", "--", "mise.local.toml"], 0),
                                (["ls-files", "--error-unmatch", "--", "mise.local.toml"], 1)):
        check = subprocess.run(["git", *arguments], cwd=repository, capture_output=True)
        if check.returncode != expected:
            raise CredentialError("mise.local.tomlのGit除外・非追跡を確認できません。変更しません。")
    original = target.read_bytes() if target.exists() else None
    parsed = tomllib.loads(original.decode("utf-8")) if original is not None else {}
    if "MISTRAL_API_KEY" in parsed.get("env", {}) and not replace:
        raise CredentialError("キーは既に設定されています。更新する場合だけ--replaceを指定してください。")
    mise = shutil.which("mise")
    if not mise:
        raise CredentialError("miseがありません。導入手順を確認してください。")
    # 非表示入力できない環境では、getpassの可視入力へのfallbackを禁止する。
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("Mistral APIキー（非表示）: ")
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        raise CredentialError("非表示入力できませんでした。利用者のローカル端末で実行してください。") from None
    if not key or any(c.isspace() or not c.isprintable() for c in key) or "{{" in key or "{%" in key:
        raise CredentialError("空白・制御文字・テンプレート構文を含む入力は保存できません。")
    lock = repository / ".signal-library-config.lock"
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.close(fd)
        with tempfile.TemporaryDirectory(prefix=".signal-library-config-", dir=repository) as stage:
            staged = Path(stage) / "mise.local.toml"
            with staged.open("xb") as stream:
                os.chmod(staged, 0o600)
                stream.write(original or b"")
            # CLIは設定読込をせず、TOML編集はmiseに任せる。キーをargvへ渡さない。
            # miseの出力・失敗詳細は秘密を含む可能性があるので表示しない。
            process_env = {name: value for name, value in os.environ.items()
                           if name.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}}
            # 自分で作成した一時コピーだけをこの子プロセス内で信頼する。
            # mise trustの永続stateや親フォルダ全体の信頼設定を変更しない。
            process_env["MISE_TRUSTED_CONFIG_PATHS"] = stage
            process_env["MISE_STATE_DIR"] = str(Path(stage) / "mise-state")
            process_env["MISE_CACHE_DIR"] = str(Path(stage) / "mise-cache")
            result = subprocess.run([mise, "--quiet", "set", "--file", str(staged), "--stdin", "MISTRAL_API_KEY"],
                                    input=key, text=True, encoding="utf-8", errors="replace", capture_output=True, cwd=stage,
                                    env=process_env, timeout=30)
            if result.returncode != 0:
                raise CredentialError("miseによる設定保存が失敗しました。既存設定は変更していません。")
            updated = tomllib.loads(staged.read_text(encoding="utf-8"))
            expected = {**parsed, "env": {**parsed.get("env", {}), "MISTRAL_API_KEY": key}}
            if updated != expected:
                raise CredentialError("設定の検証が失敗しました。既存設定は変更していません。")
            if target.is_symlink() or (target.read_bytes() if target.exists() else None) != original:
                raise CredentialError("設定が別の処理で変更されました。上書きしません。")
            os.chmod(staged, 0o600)
            # Windowsの_commitは書込み可能なハンドルを必要とする。
            with staged.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(staged, target)
    finally:
        lock.unlink()
    return {"status": "ok", "state": "key_saved", "files_written": True, "network_used": False,
            "next_actions": ["mise run library -- doctor --jsonを新しいプロセスで実行してください。認証確認は未実施です。"]}

"""空の保存先だけを初期化する。既存ライブラリの移行は行わない。"""

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
import tempfile

from .diagnostics import LIBRARY_SCHEMA, inspect_root
from .storage import is_redirect, read_record, regular_file

CATALOG = "book_id,title,contributors,edition,volume,language,scope,book_path,status,updated_at\n"
JOURNAL = ".signal-library-init.json"
LOCK = ".signal-library.lock"


class InitializationError(Exception):
    pass


def write_new(path: Path, data: str):
    # 同一volumeの完成した一時ファイルを排他公開し、既存targetを上書きしない。
    # hardlink非対応の保存先では、上書き型renameへfallbackせず停止する。
    fd, name = tempfile.mkstemp(prefix=".signal-library-init-", suffix=".tmp", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def validate_partial(root: Path, record: dict):
    allowed = {JOURNAL, LOCK, "inbox", "books", "catalog.csv", "library.json"}
    if set(p.name for p in root.iterdir()) - allowed:
        raise InitializationError("初期化途中の保存先に未知のファイルがあります。手動確認が必要です。")
    for name in ("inbox", "books"):
        path = root / name
        if is_redirect(path) or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
            raise InitializationError("初期化途中のフォルダに資料があります。変更しません。")
    for name, expected in (("catalog.csv", CATALOG), ("library.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n")):
        path = root / name
        if is_redirect(path) or (path.exists() and (not regular_file(path) or path.read_text(encoding="utf-8") != expected)):
            raise InitializationError("初期化途中のファイルが予定内容と一致しません。変更しません。")


def initialize(root_value: str | None, apply: bool, resume: bool, repository: Path | list[Path]) -> dict:
    if not root_value or not Path(root_value).expanduser().is_absolute():
        raise InitializationError("保存先の絶対パスを--rootまたはLIBRARY_ROOTで指定してください。")
    supplied = Path(root_value).expanduser()
    if is_redirect(supplied):
        raise InitializationError("保存先そのものにsymlinkは指定できません。")
    root = supplied.resolve()
    repositories=[repository] if isinstance(repository,(str,Path)) else repository
    for item in repositories:
        repo=Path(item).resolve()
        if root == repo or root in repo.parents or repo in root.parents:
            raise InitializationError("保存先とコードのリポジトリを重ねることはできません。")
    if not root.parent.is_dir():
        raise InitializationError("保存先の親フォルダを先に用意してください。")
    if root.exists() and not root.is_dir():
        raise InitializationError("保存先がフォルダではありません。")
    journal = root / JOURNAL
    lock = root / LOCK
    if lock.exists() or is_redirect(lock):
        raise InitializationError("書込みロックがあります。実行中の処理と中断状態を確認してください。")
    recovering = journal.exists() or is_redirect(journal)
    state = inspect_root(root)
    if state == "ready" and not recovering:
        return {"status": "ok", "state": "already_initialized", "files_written": False, "next_actions": []}
    if recovering:
        if not resume:
            raise InitializationError("初期化途中です。内容を確認して--resumeで再開してください。")
        if is_redirect(journal):
            raise InitializationError("初期化記録がsymlinkです。変更しません。")
        record = read_record(journal)
        validate_partial(root, record)
    elif state not in ("empty", "missing"):
        raise InitializationError("空の保存先ではありません。既存資料の移行は初期化と別に行います。")
    else:
        record = {"schema_version": LIBRARY_SCHEMA, "library_id": str(uuid.uuid4()),
                  "created_at": datetime.now(UTC).isoformat()}
    if not apply:
        return {"status": "ok", "state": "resume_planned" if recovering else "planned",
                "files_written": False, "next_actions": ["内容を確認し、--applyを付けて実行してください。"]}
    root.mkdir(exist_ok=True)
    root_identity = root.stat()
    # 診断後の競合も排他作成で止める。Dropboxの別端末間の排他は保証しない。
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    lock_identity = os.fstat(fd)
    try:
        os.close(fd)
        if (is_redirect(supplied) or supplied.resolve() != root
                or (root.stat().st_dev, root.stat().st_ino) != (root_identity.st_dev, root_identity.st_ino)):
            raise InitializationError("検査後に保存先が変更されました。変更しません。")
        if recovering and read_record(journal) != record:
            raise InitializationError("検査後に初期化記録が変更されました。変更しません。")
        if not recovering:
            if set(p.name for p in root.iterdir()) != {LOCK}:
                raise InitializationError("検査後に保存先が変更されました。変更しません。")
            write_new(journal, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        validate_partial(root, record)
        for name in ("inbox", "books"):
            (root / name).mkdir(exist_ok=True)
        if not (root / "catalog.csv").exists():
            write_new(root / "catalog.csv", CATALOG)
        if not (root / "library.json").exists():
            write_new(root / "library.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        validate_partial(root, record)
        if read_record(journal) != record:
            raise InitializationError("初期化記録が別の処理で変更されました。完了扱いにしません。")
        journal.unlink()
    finally:
        # 別の処理がroot/lockを置換した場合、そのファイルを削除しない。
        if not is_redirect(root) and root.exists() and (root.stat().st_dev, root.stat().st_ino) == (root_identity.st_dev, root_identity.st_ino):
            if lock.exists() and not is_redirect(lock) and (lock.lstat().st_dev, lock.lstat().st_ino) == (lock_identity.st_dev, lock_identity.st_ino):
                lock.unlink()
    return {"status": "ok", "state": "initialized", "files_written": True, "next_actions": []}

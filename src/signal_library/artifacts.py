"""不変の派生ファイル群を排他公開する。呼出側でlibrary lockを取得する。"""

from pathlib import Path

from .ocr.files import check_tree, write_bytes_atomic
from .registration import RegistrationError, checked_file, contained, encode, read_json


def publish_files(root, destination, files, transaction, manifest_name, journal_name, validate):
    folder=contained(Path(root),destination)
    journal=folder/journal_name
    if folder.exists():
        check_tree(folder)
        if not journal.exists():
            if not (folder/manifest_name).exists():
                raise RegistrationError('未知の既存保存先を引き継げません。新しい版のIDを指定してください。')
            validate(folder)
            if any(not (folder/name).is_file() or checked_file(folder/name).read_bytes()!=data for name,data in files.items()):
                raise RegistrationError('保存済み版と内容が異なります。新しい版のIDを指定してください。')
            return
        if read_json(journal)!=transaction:
            raise RegistrationError('中断した出力と現在の入力・設定が異なります。')
        allowed=set(files)|{journal_name}
        if any(path.relative_to(folder).as_posix() not in allowed for path in folder.rglob('*') if path.is_file()):
            raise RegistrationError('中断した出力に未知のファイルがあります。')
    else:
        folder.mkdir(parents=True,exist_ok=False)
        write_bytes_atomic(journal,encode(transaction).encode(),exclusive=True)
    for name in sorted(files,key=lambda name:(name==manifest_name,name)):
        path=contained(folder,name)
        if path.exists():
            if checked_file(path).read_bytes()!=files[name]:
                raise RegistrationError('中断した出力ファイルが変更されています。')
        else:write_bytes_atomic(path,files[name],exclusive=True)
    validate(folder)
    if read_json(journal)!=transaction:
        raise RegistrationError('公開記録が変更されました。')
    journal.unlink()

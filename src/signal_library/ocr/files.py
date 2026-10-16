"""OCRのファイル境界。管理ファイル・派生物の完成内容だけを公開する。"""

import os
import tempfile
from pathlib import Path

from ..registration import checked_file, contained, digest, relative
from ..storage import is_redirect, regular_file
from .errors import OcrError


def check_path(path):
    path = Path(os.path.abspath(path))
    for entry in (path, *path.parents):
        # macOS標準のOSパス別名は内部処理で許容し、書籍内redirectは許容しない。
        if is_redirect(entry) and not (os.name == "posix" and entry in {Path('/var'), Path('/tmp')}):
            raise OcrError("redirect in OCR path")
    if path.exists() and not path.is_dir() and not regular_file(path):
        raise OcrError("non-regular OCR file")
    return path


def write_bytes_atomic(path, data, exclusive=False):
    path = check_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    check_path(path)
    fd, name = tempfile.mkstemp(prefix='.signal-ocr-', suffix='.tmp', dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        check_path(path)
        if exclusive:
            os.link(staged, path)
        else:
            os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def check_tree(root):
    check_path(root)
    for path in root.rglob('*'):
        check_path(path)


def copy_new(source, target, expected=None):
    source = checked_file(check_path(source).resolve())
    data = source.read_bytes()
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    if expected is not None and sha != expected:
        raise OcrError("source changed during OCR copy")
    if target.exists():
        check_path(target)
        if digest(target) != sha:
            raise OcrError("existing OCR output differs")
    else:
        write_bytes_atomic(target, data, exclusive=True)
    return sha

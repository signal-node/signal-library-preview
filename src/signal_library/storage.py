"""ライブラリ管理ファイルの共通形式・境界検査。"""

import csv
import json
import stat
import uuid
from datetime import datetime
from pathlib import Path

LIBRARY_SCHEMA = "signal-library-1"
CATALOG_FIELDS = "book_id,title,contributors,edition,volume,language,scope,book_path,status,updated_at".split(",")


class UnknownLibrarySchema(ValueError):
    pass


def is_redirect(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def regular_file(path: Path) -> bool:
    return not is_redirect(path) and stat.S_ISREG(path.lstat().st_mode)


def validate_record(record: object) -> dict:
    if isinstance(record, dict) and record.get("schema_version") != LIBRARY_SCHEMA:
        raise UnknownLibrarySchema("unknown library schema")
    if not isinstance(record, dict) or set(record) != {"schema_version", "library_id", "created_at"}:
        raise ValueError("invalid library fields")
    if record["schema_version"] != LIBRARY_SCHEMA:
        raise ValueError("unknown library schema")
    identifier = record["library_id"]
    if not isinstance(identifier, str) or str(uuid.UUID(identifier)) != identifier:
        raise ValueError("invalid library id")
    timestamp = record["created_at"]
    if not isinstance(timestamp, str):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must have timezone")
    return record


def read_record(path: Path) -> dict:
    if not regular_file(path) or path.stat().st_size > 65536:
        raise ValueError("invalid management file")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return validate_record(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys))


def valid_catalog(path: Path) -> bool:
    if not regular_file(path):
        return False
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            rows = csv.reader(stream, strict=True)
            if next(rows, None) != CATALOG_FIELDS:
                return False
            return all(len(row) == len(CATALOG_FIELDS) for row in rows)
    except (csv.Error, UnicodeError):
        return False

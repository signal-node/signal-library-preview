from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from signal_library.ocr.errors import OcrError
from .files import check_path, write_bytes_atomic
from ..registration import read_json


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_json_atomic(path: Path, payload: Any) -> None:
    write_bytes_atomic(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def write_text_atomic(path: Path, text: str) -> None:
    write_bytes_atomic(path, text.encode("utf-8"))


def write_jsonl(path: Path, entries: Iterable[dict[str, Any]]) -> None:
    text = "".join(
        json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        for entry in entries
    )
    write_text_atomic(path, text)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    check_path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise OcrError(f"JSONL line {line_number} is not an object: {path.name}")
        entries.append(value)
    return entries


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = read_json(check_path(path).resolve())
    except FileNotFoundError as exc:
        raise OcrError(f"manifest does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise OcrError(f"could not read manifest: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise OcrError(f"unsupported OCR manifest: {path}")
    return value


def persist_manifest(path: Path, manifest: dict[str, Any], state: str | None = None) -> None:
    if state is not None:
        manifest["state"] = state
    manifest["updated_at"] = utc_now()
    write_json_atomic(path, manifest)

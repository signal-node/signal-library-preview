from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signal_library.ocr.errors import OcrError
from .files import check_path
from ..registration import checked_file

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png"}
LEGACY_SUFFIXES = SUPPORTED_SUFFIXES | {".pdf", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
IMAGE_SUFFIXES = LEGACY_SUFFIXES - {".pdf"}
TERMINAL_STATUSES = {"SUCCESS", "FAILED", "TIMEOUT_EXCEEDED", "CANCELLED"}
BATCH_PRICE_PER_PAGE_USD = 0.002


@dataclass(frozen=True)
class OcrDocument:
    path: Path
    relative_path: str
    sha256: str
    custom_id: str
    size_bytes: int
    mime_type: str
    pages: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with checked_file(check_path(path).resolve()).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_input_files(input_path: Path, recursive: bool = False, legacy: bool = False) -> tuple[Path, list[Path]]:
    suffixes = LEGACY_SUFFIXES if legacy else SUPPORTED_SUFFIXES
    resolved = check_path(input_path).resolve()
    if not resolved.exists():
        raise OcrError(f"input does not exist: {input_path}")
    if resolved.is_file():
        if resolved.suffix.lower() not in suffixes:
            raise OcrError(f"unsupported OCR file type: {resolved.suffix or '(none)'}")
        return resolved.parent, [resolved]
    if not resolved.is_dir():
        raise OcrError(f"input is not a file or directory: {input_path}")
    iterator = list(resolved.rglob("*")) if recursive else list(resolved.iterdir())
    for path in iterator:
        check_path(path)
    files = sorted(
        (path for path in iterator if path.is_file() and path.suffix.lower() in suffixes),
        key=lambda path: path.relative_to(resolved).as_posix().casefold(),
    )
    if not files:
        raise OcrError(f"no supported OCR files found in: {input_path}")
    return resolved, files


def _page_count(path: Path) -> int:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return 1
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise OcrError("PDF page counting requires the ocr extra; run `uv sync --extra ocr`") from exc
    try:
        return len(PdfReader(path).pages)
    except Exception as exc:
        raise OcrError(f"could not count PDF pages: {path.name}") from exc


def make_custom_id(relative_path: str, content_sha256: str) -> str:
    material = f"{relative_path}\0{content_sha256}".encode()
    return f"doc-{hashlib.sha256(material).hexdigest()[:20]}"


def discover_documents(input_path: Path, recursive: bool = False, legacy: bool = False) -> list[OcrDocument]:
    root, paths = collect_input_files(input_path, recursive=recursive, legacy=legacy)
    documents: list[OcrDocument] = []
    seen_ids: set[str] = set()
    for path in paths:
        relative_path = path.relative_to(root).as_posix()
        digest = sha256_file(path)
        custom_id = make_custom_id(relative_path, digest)
        if custom_id in seen_ids:
            raise OcrError(f"OCR custom_id collision for: {relative_path}")
        seen_ids.add(custom_id)
        documents.append(
            OcrDocument(
                path=path,
                relative_path=relative_path,
                sha256=digest,
                custom_id=custom_id,
                size_bytes=path.stat().st_size,
                mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                pages=_page_count(path),
            )
        )
    return documents


def estimate_batch_cost(pages: int) -> float:
    return round(pages * BATCH_PRICE_PER_PAGE_USD, 6)


def build_batch_entry(custom_id: str, remote_file_id: str, include_images: bool) -> dict[str, Any]:
    return {
        "custom_id": custom_id,
        "body": {
            "document": {"type": "file", "file_id": remote_file_id},
            "include_image_base64": include_images,
        },
    }


def response_body(entry: dict[str, Any]) -> dict[str, Any] | None:
    response = entry.get("response")
    if isinstance(response, dict) and isinstance(response.get("body"), dict):
        return response["body"]
    body = entry.get("body")
    return body if isinstance(body, dict) else None


def markdown_from_ocr_body(
    body: dict[str, Any],
    image_paths: dict[tuple[int, str], str] | None = None,
) -> str:
    pages = body.get("pages")
    if not isinstance(pages, list):
        return ""
    sections: list[str] = []
    for position, page in enumerate(pages):
        if not isinstance(page, dict) or not isinstance(page.get("markdown"), str):
            continue
        index = int(page.get("index", position))
        markdown = page["markdown"]
        if image_paths:
            for image in page.get("images", []):
                if not isinstance(image, dict) or not isinstance(image.get("id"), str):
                    continue
                image_id = image["id"]
                replacement = image_paths.get((index, image_id))
                if replacement:
                    markdown = markdown.replace(f"]({image_id})", f"]({replacement})")
        sections.append(f"<!-- page: {index} -->\n\n{markdown.strip()}")
    return "\n\n".join(sections).strip() + ("\n" if sections else "")


def safe_asset_name(image_id: str, position: int) -> str:
    basename = Path(image_id).name
    if (basename == image_id and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", basename)
            and not basename.endswith(".") and basename.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
        return basename
    suffix = Path(basename).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        suffix = ".bin"
    return f"image-{position}{suffix}"


def decode_image_base64(value: str) -> bytes:
    encoded = value
    if value.startswith("data:"):
        header, separator, encoded = value.partition(",")
        if not separator or ";base64" not in header:
            raise ValueError("unsupported image data URL")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid image base64 payload") from exc


def without_image_base64(entry: dict[str, Any]) -> dict[str, Any]:
    sanitized = copy.deepcopy(entry)
    body = response_body(sanitized)
    if body is None or not isinstance(body.get("pages"), list):
        return sanitized
    for page in body["pages"]:
        if not isinstance(page, dict) or not isinstance(page.get("images"), list):
            continue
        for image in page["images"]:
            if isinstance(image, dict):
                image.pop("image_base64", None)
    return sanitized

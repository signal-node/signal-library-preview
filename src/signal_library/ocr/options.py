from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signal_library.ocr.workflow import (
    cancel_ocr,
    collect_ocr,
    retry_ocr,
    run_ocr,
    status_ocr,
    submit_ocr,
)


@dataclass(frozen=True)
class OcrSubmitOptions:
    input_path: Path
    output_dir: Path
    model: str
    include_images: bool
    recursive: bool
    max_pages: int
    max_cost_usd: float
    request_timeout_seconds: int
    batch_timeout_hours: int
    keep_remote_files: bool


@dataclass(frozen=True)
class OcrRunOptions(OcrSubmitOptions):
    poll_interval_seconds: float
    wait_timeout_seconds: float


def submit(options: OcrSubmitOptions) -> Path:
    return submit_ocr(options)


def run(options: OcrRunOptions) -> tuple[Path, int]:
    return run_ocr(options)


def status(manifest_path: Path) -> dict[str, Any]:
    return status_ocr(manifest_path)


def collect(
    manifest_path: Path,
    request_timeout_seconds: int,
    keep_remote_files: bool,
) -> int:
    return collect_ocr(manifest_path, request_timeout_seconds, keep_remote_files)


def cancel(manifest_path: Path, request_timeout_seconds: int) -> dict[str, Any]:
    return cancel_ocr(manifest_path, request_timeout_seconds)


def retry(
    manifest_path: Path,
    output_dir: Path | None,
    request_timeout_seconds: int,
) -> Path:
    return retry_ocr(manifest_path, output_dir, request_timeout_seconds)

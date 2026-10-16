from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from signal_library.ocr.errors import OcrError
from .files import write_bytes_atomic


def api_key_from_env() -> str:
    key = os.getenv("MISTRAL_API_KEY", "").strip() or os.getenv("SIGNAL_STACK_MISTRAL_API_KEY", "").strip()
    if not key:
        raise OcrError("set MISTRAL_API_KEY or SIGNAL_STACK_MISTRAL_API_KEY")
    return key


def batch_snapshot(job: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for name in (
        "id",
        "status",
        "model",
        "created_at",
        "started_at",
        "completed_at",
        "total_requests",
        "completed_requests",
        "succeeded_requests",
        "failed_requests",
        "output_file",
        "error_file",
    ):
        value = getattr(job, name, None)
        if value is not None and value.__class__.__name__ != "Unset":
            snapshot[name] = str(value) if name == "status" else value
    return snapshot


def safe_error(exc: BaseException) -> dict[str, Any]:
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(getattr(exc, "raw_response", None), "status_code", None)
    result: dict[str, Any] = {"type": type(exc).__name__}
    if isinstance(status_code, int):
        result["status_code"] = status_code
    return result


def definite_rejection(failure: dict[str, Any]) -> bool:
    status_code = failure.get("status_code")
    return isinstance(status_code, int) and 400 <= status_code < 500 and status_code != 429


class MistralOcrGateway:
    def __init__(self, request_timeout_seconds: int = 300) -> None:
        try:
            from mistralai.client import Mistral
        except ImportError as exc:
            raise OcrError("mistralai is not installed; install the Signal Library ocr extra") from exc
        self._client = Mistral(
            api_key=api_key_from_env(),
            timeout_ms=request_timeout_seconds * 1000,
            retry_config=None,
        )

    def __enter__(self) -> "MistralOcrGateway":
        self._client.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        self._client.__exit__(*args)

    def upload(self, path: Path, purpose: str) -> str:
        with path.open("rb") as handle:
            result = self._client.files.upload(
                file={"file_name": path.name, "content": handle},
                purpose=purpose,
                retries=None,
            )
        return result.id

    def create_batch(
        self,
        input_file_id: str,
        model: str,
        run_id: str,
        timeout_hours: int,
    ) -> Any:
        return self._client.batch.jobs.create(
            endpoint="/v1/ocr",
            input_files=[input_file_id],
            model=model,
            metadata={"purpose": "signal-stack-ocr", "run_id": run_id},
            timeout_hours=timeout_hours,
            retries=None,
        )

    def get_batch(self, job_id: str) -> Any:
        return self._client.batch.jobs.get(job_id=job_id)

    def find_batches(self, run_id: str) -> list[Any]:
        response = self._client.batch.jobs.list(
            metadata={"purpose": "signal-stack-ocr", "run_id": run_id},
            page_size=100,
        )
        data = getattr(response, "data", None)
        jobs = list(data) if data is not None else []
        total = getattr(response, "total", None)
        if isinstance(total, int) and total > len(jobs):
            raise OcrError("batch search is truncated; manual reconciliation required")
        return jobs

    def cancel_batch(self, job_id: str) -> Any:
        return self._client.batch.jobs.cancel(job_id=job_id)

    def download(self, file_id: str, destination: Path) -> None:
        response = self._client.files.download(file_id=file_id)
        try:
            content = response.read()
        finally:
            response.close()
        write_bytes_atomic(destination, content)

    def delete_file(self, file_id: str) -> None:
        self._client.files.delete(file_id=file_id)

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from signal_library.ocr.options import OcrSubmitOptions
from signal_library.ocr.domain import (
    build_batch_entry,
    decode_image_base64,
    discover_documents,
    estimate_batch_cost,
    safe_asset_name,
)
from signal_library.ocr.errors import OcrError
from signal_library.ocr.workflow import (
    collect_ocr,
    retry_ocr,
    status_ocr,
    submit_ocr,
)
from signal_library.ocr.manifest import read_manifest, write_json_atomic


class FakeGateway:
    uploads: dict[str, bytes] = {}
    downloads: dict[str, bytes] = {}
    deleted: list[str] = []
    job = SimpleNamespace(
        id="job-1",
        status="SUCCESS",
        model="mistral-ocr-4-0",
        total_requests=1,
        completed_requests=1,
        succeeded_requests=1,
        failed_requests=0,
        output_file="output-1",
        error_file=None,
    )

    @classmethod
    def reset(cls) -> None:
        cls.uploads = {}
        cls.downloads = {}
        cls.deleted = []
        cls.job = SimpleNamespace(
            id="job-1",
            status="SUCCESS",
            model="mistral-ocr-4-0",
            total_requests=1,
            completed_requests=1,
            succeeded_requests=1,
            failed_requests=0,
            output_file="output-1",
            error_file=None,
        )

    def __init__(self, request_timeout_seconds: int) -> None:
        self.request_timeout_seconds = request_timeout_seconds

    def __enter__(self) -> "FakeGateway":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def upload(self, path: Path, purpose: str) -> str:
        file_id = f"{purpose}-{len(self.uploads) + 1}"
        self.uploads[file_id] = path.read_bytes()
        return file_id

    def create_batch(self, input_file_id: str, model: str, run_id: str, timeout_hours: int):
        return self.job

    def get_batch(self, job_id: str):
        return self.job

    def find_batches(self, run_id: str):
        return [self.job]

    def cancel_batch(self, job_id: str):
        self.job.status = "CANCELLED"
        return self.job

    def download(self, file_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.downloads.get(file_id, b""))

    def delete_file(self, file_id: str) -> None:
        self.deleted.append(file_id)


def image_options(input_path: Path, output_dir: Path) -> OcrSubmitOptions:
    return OcrSubmitOptions(
        input_path=input_path,
        output_dir=output_dir,
        model="mistral-ocr-4-0",
        include_images=True,
        recursive=False,
        max_pages=500,
        max_cost_usd=1.5,
        request_timeout_seconds=30,
        batch_timeout_hours=24,
        keep_remote_files=False,
    )


class OcrDomainTests(unittest.TestCase):
    def test_discovery_and_cost_are_deterministic(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "page.png"
            source.write_bytes(b"fake image")
            first = discover_documents(source)[0]
            second = discover_documents(source)[0]
        self.assertEqual(first.custom_id, second.custom_id)
        self.assertEqual(first.pages, 1)
        self.assertEqual(estimate_batch_cost(400), 0.8)

    def test_batch_entry_uses_file_id_mode_and_images(self) -> None:
        self.assertEqual(
            build_batch_entry("doc-1", "file-1", True),
            {
                "custom_id": "doc-1",
                "body": {
                    "document": {"type": "file", "file_id": "file-1"},
                    "include_image_base64": True,
                },
            },
        )

    def test_asset_name_and_base64_reject_path_traversal(self) -> None:
        self.assertEqual(safe_asset_name("../../secret.png", 2), "image-2.png")
        with self.assertRaisesRegex(ValueError, "invalid image base64"):
            decode_image_base64("not base64")


class OcrOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeGateway.reset()

    def test_submit_collect_materializes_and_cleans_remote_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "chapter.png"
            source.write_bytes(b"fake image")
            manifest_path = submit_ocr(
                image_options(source, root / "output"),
                gateway_factory=FakeGateway,
            )
            manifest = read_manifest(manifest_path)
            custom_id = manifest["documents"][0]["custom_id"]
            output = {
                "custom_id": custom_id,
                "response": {
                    "body": {
                        "pages": [
                            {
                                "index": 0,
                                "markdown": "Text ![figure](figure.png)",
                                "images": [
                                    {
                                        "id": "figure.png",
                                        "image_base64": "data:image/png;base64,aW1hZ2U=",
                                    }
                                ],
                            }
                        ]
                    }
                },
            }
            FakeGateway.downloads["output-1"] = (json.dumps(output) + "\n").encode()

            exit_code = collect_ocr(
                manifest_path,
                gateway_factory=FakeGateway,
            )
            collected = read_manifest(manifest_path)
            documents_dir = Path(collected["run_dir"]) / "documents"

            self.assertEqual(exit_code, 0)
            self.assertEqual(collected["state"], "COLLECTED")
            self.assertEqual(collected["cleanup"]["state"], "CLEANED")
            self.assertIn("chapter.assets/page-0/figure.png", (documents_dir / "chapter.md").read_text())
            self.assertNotIn("image_base64", (documents_dir / "chapter.json").read_text())
            self.assertEqual(
                (documents_dir / "chapter.assets/page-0/figure.png").read_bytes(),
                b"image",
            )
            self.assertTrue(FakeGateway.deleted)

    def test_collect_preserves_success_when_image_decode_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "page.png"
            source.write_bytes(b"fake image")
            manifest_path = submit_ocr(
                image_options(source, root / "output"),
                gateway_factory=FakeGateway,
            )
            manifest = read_manifest(manifest_path)
            output = {
                "custom_id": manifest["documents"][0]["custom_id"],
                "response": {
                    "body": {
                        "pages": [
                            {
                                "index": 0,
                                "markdown": "OCR text",
                                "images": [{"id": "../bad.png", "image_base64": "broken"}],
                            }
                        ]
                    }
                },
            }
            FakeGateway.downloads["output-1"] = (json.dumps(output) + "\n").encode()

            self.assertEqual(collect_ocr(manifest_path, gateway_factory=FakeGateway), 0)
            collected = read_manifest(manifest_path)
            self.assertEqual(collected["documents"][0]["state"], "SUCCEEDED")
            self.assertTrue(collected["documents"][0]["warnings"])

    def test_provider_error_is_partial_and_retryable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "page.png"
            source.write_bytes(b"fake image")
            manifest_path = submit_ocr(
                image_options(source, root / "output"),
                gateway_factory=FakeGateway,
            )
            manifest = read_manifest(manifest_path)
            custom_id = manifest["documents"][0]["custom_id"]
            FakeGateway.job.output_file = None
            FakeGateway.job.error_file = "error-1"
            FakeGateway.job.succeeded_requests = 0
            FakeGateway.job.failed_requests = 1
            FakeGateway.downloads["error-1"] = (
                json.dumps({"custom_id": custom_id, "error": {"message": "bad document"}}) + "\n"
            ).encode()

            self.assertEqual(collect_ocr(manifest_path, gateway_factory=FakeGateway), 4)
            collected = read_manifest(manifest_path)
            self.assertEqual(collected["state"], "PARTIAL")
            self.assertEqual(collected["cleanup"]["state"], "CLEANED")
            self.assertFalse(collected["integrity"]["all_succeeded"])
            self.assertEqual(collected["documents"][0]["state"], "FAILED")

            retry_manifest = retry_ocr(manifest_path, gateway_factory=FakeGateway)
            retried = read_manifest(retry_manifest)
            self.assertEqual(len(retried["documents"]), 1)
            self.assertEqual(retried["parent_manifest"], str(manifest_path))

    def test_status_refreshes_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "page.png"
            source.write_bytes(b"fake image")
            manifest_path = submit_ocr(
                image_options(source, root / "output"),
                gateway_factory=FakeGateway,
            )
            FakeGateway.job.status = "RUNNING"
            FakeGateway.job.completed_requests = 0

            report = status_ocr(manifest_path, gateway_factory=FakeGateway)

            self.assertEqual(report["batch"]["status"], "RUNNING")
            self.assertEqual(report["batch"]["completed_requests"], 0)

    def test_status_recovers_unknown_submission_by_run_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "page.png"
            source.write_bytes(b"fake image")
            manifest_path = submit_ocr(
                image_options(source, root / "output"),
                gateway_factory=FakeGateway,
            )
            manifest = read_manifest(manifest_path)
            manifest["state"] = "SUBMISSION_UNKNOWN"
            manifest["batch"].pop("id")
            write_json_atomic(manifest_path, manifest)

            report = status_ocr(manifest_path, gateway_factory=FakeGateway)

            self.assertEqual(report["state"], "SUBMITTED_RECOVERED")
            self.assertEqual(report["batch"]["id"], "job-1")

    def test_page_and_cost_guard_runs_before_upload(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "page.png"
            source.write_bytes(b"fake image")
            options = image_options(source, root / "output")
            options = OcrSubmitOptions(**{**options.__dict__, "max_pages": 0})
            with self.assertRaisesRegex(OcrError, "exceeds --max-pages"):
                submit_ocr(options, gateway_factory=FakeGateway)
            self.assertFalse(FakeGateway.uploads)

    def test_retry_rejects_submission_unknown(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            write_json_atomic(path, {"schema_version": 1, "state": "SUBMISSION_UNKNOWN"})
            with self.assertRaisesRegex(OcrError, "do not retry"):
                retry_ocr(path, gateway_factory=FakeGateway)


if __name__ == "__main__":
    unittest.main()

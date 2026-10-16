from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from signal_library.ocr import results as ocr_results


def create_run(
    root: Path,
    name: str,
    documents: list[tuple[str, str, str]],
) -> Path:
    run = root / name
    output = run / "documents"
    output.mkdir(parents=True)
    records = []
    for custom_id, stem, state in documents:
        records.append(
            {
                "custom_id": custom_id,
                "source_relative_path": f"{stem}.png",
                "output_stem": stem,
                "state": state,
            }
        )
        if state == "SUCCEEDED":
            (output / f"{stem}.md").write_text(f"OCR {stem}\n", encoding="utf-8")
            (output / f"{stem}.json").write_text("{}\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "run_id": name,
        "state": "COLLECTED" if all(item[2] == "SUCCEEDED" for item in documents) else "PARTIAL",
        "batch": {"status": "SUCCESS"},
        "cleanup": {"state": "CLEANED", "pending_file_ids": []},
        "integrity": {"complete": True},
        "documents": records,
    }
    (run / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return run


class OcrSkillResultTests(unittest.TestCase):
    def test_validate_complete_run(self) -> None:
        with TemporaryDirectory() as tmp:
            run = create_run(Path(tmp), "run-a", [("doc-1", "page-1", "SUCCEEDED")])
            report = ocr_results.validate_run(run)
        self.assertTrue(report["ok"])
        self.assertEqual(report["markdown"], 1)

    def test_merge_resolves_failure_with_retry_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = create_run(
                root,
                "run-a",
                [
                    ("doc-1", "page-1", "SUCCEEDED"),
                    ("doc-2", "page-2", "FAILED"),
                ],
            )
            retry = create_run(root, "run-b", [("doc-2", "page-2", "SUCCEEDED")])
            destination = ocr_results.merge_runs(
                [first, retry],
                root / "combined",
                "merged",
            )
            report = ocr_results.validate_run(destination)
        self.assertTrue(report["ok"])
        self.assertEqual(report["documents"], 2)

    def test_merge_rejects_unresolved_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = create_run(root, "run-a", [("doc-1", "page-1", "FAILED")])
            second = create_run(root, "run-b", [("doc-2", "page-2", "SUCCEEDED")])
            with self.assertRaisesRegex(ocr_results.ResultError, "unresolved"):
                ocr_results.merge_runs([first, second], root / "combined", "merged")

    def test_validate_can_allow_explicit_remote_retention(self) -> None:
        with TemporaryDirectory() as tmp:
            run = create_run(Path(tmp), "run-a", [("doc-1", "page-1", "SUCCEEDED")])
            manifest_path = run / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["cleanup"] = {"state": "PENDING", "pending_file_ids": ["file-1"]}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = ocr_results.validate_run(run, allow_remote_files=True)
        self.assertTrue(report["ok"])
        self.assertFalse(report["cleanup_complete"])


if __name__ == "__main__":
    unittest.main()

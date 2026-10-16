from __future__ import annotations

import time
from dataclasses import replace
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

from signal_library.ocr.domain import (
    TERMINAL_STATUSES,
    build_batch_entry,
    decode_image_base64,
    discover_documents,
    estimate_batch_cost,
    markdown_from_ocr_body,
    response_body,
    safe_asset_name,
    sha256_file,
    without_image_base64,
)
from signal_library.ocr.errors import OcrError
from .files import check_tree, write_bytes_atomic
from ..registration import checked_file, contained, relative
from signal_library.ocr.gateway import (
    MistralOcrGateway,
    batch_snapshot,
    definite_rejection,
    safe_error,
)
from signal_library.ocr.manifest import (
    persist_manifest,
    read_jsonl,
    read_manifest,
    utc_now,
    write_json_atomic,
    write_jsonl,
    write_text_atomic,
)


def _new_run_dir(output_root: Path) -> Path:
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = output_root.resolve() / name
    if candidate.exists():
        candidate = output_root.resolve() / f"{name}-{uuid.uuid4().hex[:8]}"
    candidate.mkdir(parents=True)
    return candidate


def _document_record(document: Any) -> dict[str, Any]:
    return {
        "custom_id": document.custom_id,
        "source_relative_path": document.relative_path,
        "size_bytes": document.size_bytes,
        "sha256": document.sha256,
        "mime_type": document.mime_type,
        "pages": document.pages,
        "remote_file_id": None,
        "state": "DISCOVERED",
        "warnings": [],
    }


def _source_path(manifest: dict[str, Any], document: dict[str, Any]) -> Path:
    relative = document.get("source_relative_path")
    if not isinstance(relative, str):
        raise OcrError("manifest document has no source_relative_path")
    root = Path(manifest["input_root"])
    return checked_file(contained(root, relative))


def _prepare_manifest(
    options: Any,
    run_dir: Path,
    only_custom_ids: set[str] | None = None,
    parent_manifest: Path | None = None,
) -> dict[str, Any]:
    discovered = discover_documents(options.input_path, recursive=options.recursive, legacy=getattr(options, "legacy_inputs", False))
    overrides = getattr(options, "custom_id_map", {})
    discovered = [replace(d, custom_id=overrides.get(d.relative_path, d.custom_id)) for d in discovered]
    input_root = (
        options.input_path.resolve().parent
        if options.input_path.resolve().is_file()
        else options.input_path.resolve()
    )
    if only_custom_ids is not None:
        discovered = [document for document in discovered if document.custom_id in only_custom_ids]
        found = {document.custom_id for document in discovered}
        missing = only_custom_ids - found
        if missing:
            raise OcrError(
                "retry sources changed or are missing for custom_id(s): " + ", ".join(sorted(missing))
            )
    if not discovered:
        raise OcrError("no OCR documents selected")
    expected = getattr(options, "expected_sources", None)
    if expected is not None and {d.relative_path: d.sha256 for d in discovered} != expected:
        raise OcrError("registered sources changed after planning; nothing uploaded")
    total_pages = sum(document.pages for document in discovered)
    estimated_cost = estimate_batch_cost(total_pages)
    if total_pages > options.max_pages:
        raise OcrError(
            f"estimated {total_pages} pages exceeds --max-pages {options.max_pages}"
        )
    if estimated_cost > options.max_cost_usd:
        raise OcrError(
            f"estimated batch cost ${estimated_cost:.3f} exceeds --max-cost-usd "
            f"${options.max_cost_usd:.3f}"
        )
    return {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex,
        "purpose": "mistral-batch-ocr",
        "legacy_inputs": bool(getattr(options, "legacy_inputs", False)),
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "state": "PREPARED",
        "input_path": str(options.input_path.resolve()),
        "input_root": str(input_root),
        "run_dir": str(run_dir),
        "model_requested": options.model,
        "ocr_options": {
            "include_images": options.include_images,
            "recursive": options.recursive,
        },
        "runtime": {"batch_timeout_hours": options.batch_timeout_hours},
        "limits": {
            "max_pages": options.max_pages,
            "max_cost_usd": options.max_cost_usd,
            "estimated_pages": total_pages,
            "estimated_cost_usd": estimated_cost,
            "batch_price_per_page_usd": 0.002,
        },
        "documents": [_document_record(document) for document in discovered],
        "batch": {},
        "cleanup": {"state": "PENDING", "deleted_file_ids": [], "pending_file_ids": []},
        "parent_manifest": str(parent_manifest.resolve()) if parent_manifest else None,
    }


def _submit_prepared(
    options: Any,
    manifest: dict[str, Any],
    manifest_path: Path,
    gateway_factory: Any = MistralOcrGateway,
) -> Path:
    run_dir = Path(manifest["run_dir"])
    requests_path = run_dir / "requests.jsonl"
    try:
        with gateway_factory(options.request_timeout_seconds) as gateway:
            persist_manifest(manifest_path, manifest, "UPLOADING_DOCUMENTS")
            entries: list[dict[str, Any]] = []
            for document in manifest["documents"]:
                source = _source_path(manifest, document)
                if not source.is_file() or sha256_file(source) != document["sha256"]:
                    raise OcrError(f"source changed before upload: {document['source_relative_path']}")
                file_id = gateway.upload(source, "ocr")
                document["remote_file_id"] = file_id
                document["state"] = "UPLOADED"
                entries.append(
                    build_batch_entry(
                        document["custom_id"],
                        file_id,
                        manifest["ocr_options"]["include_images"],
                    )
                )
                persist_manifest(manifest_path, manifest)

            write_jsonl(requests_path, entries)
            manifest["batch"]["request_sha256"] = sha256_file(requests_path)
            persist_manifest(manifest_path, manifest, "REQUEST_READY")
            input_file_id = gateway.upload(requests_path, "batch")
            manifest["batch"]["input_file_id"] = input_file_id
            persist_manifest(manifest_path, manifest, "REQUEST_UPLOADED")

            persist_manifest(manifest_path, manifest, "SUBMITTING")
            job = gateway.create_batch(
                input_file_id=input_file_id,
                model=options.model,
                run_id=manifest["run_id"],
                timeout_hours=options.batch_timeout_hours,
            )
            manifest["batch"].update(batch_snapshot(job))
            persist_manifest(manifest_path, manifest, "SUBMITTED")
            return manifest_path
    except OcrError:
        persist_manifest(manifest_path, manifest, "SUBMISSION_UNKNOWN" if manifest.get("state") == "SUBMITTING" else "FAILED")
        raise
    except Exception as exc:
        failure = safe_error(exc)
        manifest["failure"] = failure
        if manifest.get("state") == "SUBMITTING" and not definite_rejection(failure):
            persist_manifest(manifest_path, manifest, "SUBMISSION_UNKNOWN")
            raise OcrError(
                f"batch submission result is unknown; inspect manifest before retrying: {manifest_path}"
            ) from exc
        persist_manifest(manifest_path, manifest, "FAILED")
        raise OcrError(f"Mistral OCR submission failed ({type(exc).__name__})") from exc


def submit_ocr(
    options: Any,
    *,
    only_custom_ids: set[str] | None = None,
    parent_manifest: Path | None = None,
    gateway_factory: Any = MistralOcrGateway,
) -> Path:
    run_dir = _new_run_dir(options.output_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = _prepare_manifest(options, run_dir, only_custom_ids, parent_manifest)
    persist_manifest(manifest_path, manifest)
    return _submit_prepared(options, manifest, manifest_path, gateway_factory)


def _refresh(
    manifest_path: Path,
    request_timeout_seconds: int,
    gateway_factory: Any = MistralOcrGateway,
) -> tuple[dict[str, Any], Any]:
    manifest = read_manifest(manifest_path)
    job_id = manifest.get("batch", {}).get("id")
    try:
        with gateway_factory(request_timeout_seconds) as gateway:
            if not isinstance(job_id, str) or not job_id:
                if manifest.get("state") not in {"SUBMISSION_UNKNOWN", "SUBMITTING"}:
                    raise OcrError("manifest does not contain a batch job id")
                matches = gateway.find_batches(manifest["run_id"])
                if not matches:
                    raise OcrError(
                        "no remote batch matched this run yet; wait and check status again"
                    )
                if len(matches) != 1:
                    raise OcrError(
                        "multiple remote batches matched this run; manual reconciliation is required"
                    )
                recovered = batch_snapshot(matches[0])
                job_id = recovered.get("id")
                if not isinstance(job_id, str) or not job_id:
                    raise OcrError("matched remote batch did not contain a job id")
                manifest["batch"].update(recovered)
                persist_manifest(manifest_path, manifest, "SUBMITTED_RECOVERED")
            job = gateway.get_batch(job_id)
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError(f"could not read Mistral batch status ({type(exc).__name__})") from exc
    snapshot = batch_snapshot(job)
    if snapshot.get("id") != job_id or snapshot.get("model", manifest["model_requested"]) != manifest["model_requested"]:
        raise OcrError("remote job identity/model does not match manifest")
    manifest["batch"].update(snapshot)
    persist_manifest(manifest_path, manifest)
    return manifest, job


def status_ocr(
    manifest_path: Path,
    request_timeout_seconds: int = 300,
    gateway_factory: Any = MistralOcrGateway,
) -> dict[str, Any]:
    manifest, _ = _refresh(manifest_path.resolve(), request_timeout_seconds, gateway_factory)
    return {
        "manifest": str(manifest_path.resolve()),
        "state": manifest["state"],
        "run_id": manifest["run_id"],
        "batch": manifest["batch"],
        "limits": manifest["limits"],
        "documents": {
            "total": len(manifest["documents"]),
            "succeeded": sum(d.get("state") == "SUCCEEDED" for d in manifest["documents"]),
            "failed": sum(d.get("state") in {"FAILED", "MISSING_RESULT"} for d in manifest["documents"]),
        },
    }


def _materialize_images(
    body: dict[str, Any],
    assets_root: Path,
) -> tuple[dict[tuple[int, str], str], list[str]]:
    image_paths: dict[tuple[int, str], str] = {}
    warnings: list[str] = []
    seen_names = set()
    pages = body.get("pages")
    if not isinstance(pages, list):
        return image_paths, warnings
    for page_position, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        page_index = int(page.get("index", page_position))
        images = page.get("images")
        if not isinstance(images, list):
            continue
        for image_position, image in enumerate(images):
            if not isinstance(image, dict):
                continue
            image_id = image.get("id")
            encoded = image.get("image_base64")
            if not isinstance(image_id, str) or not isinstance(encoded, str) or not encoded:
                continue
            filename = safe_asset_name(image_id, image_position)
            key = (page_index, filename.casefold())
            if key in seen_names or (page_index, image_id) in image_paths:
                warnings.append("duplicate image name or id requires review")
                continue
            seen_names.add(key)
            destination = assets_root / f"page-{page_index}" / filename
            try:
                payload = decode_image_base64(encoded)
                write_bytes_atomic(destination, payload)
                image_paths[(page_index, image_id)] = (
                    f"{assets_root.name}/page-{page_index}/{filename}"
                )
            except (OSError, ValueError) as exc:
                warnings.append(f"image {image_id!r} was not materialized ({type(exc).__name__})")
    return image_paths, warnings


def _safe_output_stem(relative_path: str) -> str:
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or ".." in pure.parts:
        raise OcrError("manifest contains an unsafe output path")
    stem = "__".join(pure.with_suffix("").parts)
    stem = "".join(character if character.isprintable() else "_" for character in stem)
    if not stem or stem in {".", ".."}:
        stem = "document"
    if len(stem) > 120:
        suffix = uuid.uuid5(uuid.NAMESPACE_URL, relative_path).hex[:12]
        stem = f"{stem[:107]}-{suffix}"
    return stem


def _output_stems(documents: list[dict[str, Any]]) -> dict[str, str]:
    candidates = {
        document["custom_id"]: _safe_output_stem(document["source_relative_path"])
        for document in documents
    }
    counts: dict[str, int] = {}
    for stem in candidates.values():
        counts[stem.casefold()] = counts.get(stem.casefold(), 0) + 1
    return {
        custom_id: (
            f"{stem}--{custom_id.removeprefix('doc-')}"
            if counts[stem.casefold()] > 1
            else stem
        )
        for custom_id, stem in candidates.items()
    }


def _materialize_results(
    manifest: dict[str, Any],
    output_entries: list[dict[str, Any]],
    error_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = {document["custom_id"] for document in manifest["documents"]}
    observed: dict[str, tuple[str, dict[str, Any]]] = {}
    duplicates: set[str] = set()
    for category, entries in (("success", output_entries), ("error", error_entries)):
        for entry in entries:
            custom_id = entry.get("custom_id")
            if not isinstance(custom_id, str):
                continue
            if custom_id in observed:
                duplicates.add(custom_id)
            observed[custom_id] = (category, entry)
    unknown = sorted(set(observed) - expected)
    missing = sorted(expected - set(observed))
    documents_dir = Path(manifest["run_dir"]) / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    output_stems = _output_stems(manifest["documents"])

    for document in manifest["documents"]:
        custom_id = document["custom_id"]
        value = observed.get(custom_id)
        if value is None:
            document["state"] = "MISSING_RESULT"
            continue
        category, entry = value
        stem = output_stems[custom_id]
        document["output_stem"] = stem
        if category == "error":
            document["state"] = "FAILED"
            write_json_atomic(documents_dir / f"{stem}.error.json", entry)
            continue
        body = response_body(entry)
        if body is None or not isinstance(body.get("pages"), list) or not body["pages"] or any(not isinstance(page, dict) or not isinstance(page.get("markdown"), str) or not page["markdown"].strip() for page in body["pages"]):
            document["state"] = "FAILED"
            document["warnings"].append("successful response did not contain an OCR body")
            write_json_atomic(documents_dir / f"{stem}.error.json", entry)
            continue
        image_paths, warnings = _materialize_images(body, documents_dir / f"{stem}.assets")
        document["warnings"].extend(warnings)
        document["materialized_images"] = len(image_paths)
        document["state"] = "SUCCEEDED"
        write_text_atomic(
            documents_dir / f"{stem}.md",
            markdown_from_ocr_body(body, image_paths),
        )
        write_json_atomic(documents_dir / f"{stem}.json", without_image_base64(entry))

    failed_bodies = [
        document["custom_id"]
        for document in manifest["documents"]
        if document["state"] == "FAILED" and document["custom_id"] not in {
            entry.get("custom_id") for entry in error_entries
        }
    ]
    failed_documents = sorted(
        document["custom_id"]
        for document in manifest["documents"]
        if document["state"] != "SUCCEEDED"
    )
    return {
        "expected": len(expected),
        "observed": len(observed),
        "missing_custom_ids": missing,
        "unknown_custom_ids": unknown,
        "duplicate_custom_ids": sorted(duplicates),
        "invalid_success_custom_ids": sorted(failed_bodies),
        "complete": not missing and not unknown and not duplicates and not failed_bodies,
        "all_succeeded": not failed_documents,
        "failed_custom_ids": failed_documents,
    }


def _remote_file_ids(manifest: dict[str, Any]) -> list[str]:
    candidates = [
        *(document.get("remote_file_id") for document in manifest.get("documents", [])),
        manifest.get("batch", {}).get("input_file_id"),
        manifest.get("batch", {}).get("output_file"),
        manifest.get("batch", {}).get("error_file"),
    ]
    return list(dict.fromkeys(value for value in candidates if isinstance(value, str) and value))


def _cleanup(
    gateway: Any,
    manifest: dict[str, Any],
    manifest_path: Path,
) -> bool:
    cleanup = manifest.setdefault(
        "cleanup",
        {"state": "PENDING", "deleted_file_ids": [], "pending_file_ids": []},
    )
    deleted = set(cleanup.get("deleted_file_ids", []))
    pending: list[str] = []
    for file_id in reversed(_remote_file_ids(manifest)):
        if file_id in deleted:
            continue
        try:
            gateway.delete_file(file_id)
            cleanup["deleted_file_ids"].append(file_id)
            deleted.add(file_id)
        except Exception as exc:
            if safe_error(exc).get("status_code") == 404:
                cleanup["deleted_file_ids"].append(file_id)
                deleted.add(file_id)
            else:
                pending.append(file_id)
        cleanup["pending_file_ids"] = pending
        persist_manifest(manifest_path, manifest)
    cleanup["pending_file_ids"] = pending
    cleanup["state"] = "CLEANED" if not pending else "PENDING"
    persist_manifest(manifest_path, manifest)
    return not pending


def collect_ocr(
    manifest_path: Path,
    request_timeout_seconds: int = 300,
    keep_remote_files: bool = False,
    gateway_factory: Any = MistralOcrGateway,
) -> int:
    manifest_path = manifest_path.resolve()
    manifest = read_manifest(manifest_path)
    check_tree(Path(manifest["run_dir"]))
    if manifest.get("state") in {"COLLECTED", "PARTIAL", "COLLECTED_WITH_CLEANUP_PENDING"}:
        if keep_remote_files or manifest.get("cleanup", {}).get("state") == "CLEANED":
            return 0 if manifest.get("integrity", {}).get("all_succeeded") and manifest.get("integrity", {}).get("complete") else 4
        try:
            with gateway_factory(request_timeout_seconds) as gateway:
                cleaned = _cleanup(gateway, manifest, manifest_path)
        except Exception as exc:
            raise OcrError(f"Mistral OCR cleanup failed ({type(exc).__name__})") from exc
        if not cleaned:
            manifest["cleanup"]["state"] = "PENDING"
        persist_manifest(manifest_path, manifest)
        return 0 if manifest.get("integrity", {}).get("all_succeeded") and manifest.get("integrity", {}).get("complete") else 4
    job_id = manifest.get("batch", {}).get("id")
    if not isinstance(job_id, str) or not job_id:
        raise OcrError("manifest does not contain a batch job id")

    try:
        with gateway_factory(request_timeout_seconds) as gateway:
            job = gateway.get_batch(job_id)
            manifest["batch"].update(batch_snapshot(job))
            status = str(manifest["batch"].get("status"))
            persist_manifest(manifest_path, manifest)
            if status not in TERMINAL_STATUSES:
                raise OcrError(f"batch is not terminal yet: {status}")

            raw_dir = Path(manifest["run_dir"]) / "raw"
            output_path = raw_dir / "output.jsonl"
            error_path = raw_dir / "errors.jsonl"
            output_file = manifest["batch"].get("output_file")
            error_file = manifest["batch"].get("error_file")
            if isinstance(output_file, str) and output_file:
                gateway.download(output_file, output_path)
            else:
                write_jsonl(output_path, [])
            if isinstance(error_file, str) and error_file:
                gateway.download(error_file, error_path)
            else:
                write_jsonl(error_path, [])

            integrity = _materialize_results(
                manifest,
                read_jsonl(output_path),
                read_jsonl(error_path),
            )
            manifest["integrity"] = integrity
            state = "COLLECTED" if integrity["all_succeeded"] and integrity["complete"] else "PARTIAL"
            persist_manifest(manifest_path, manifest, state)
            if not keep_remote_files:
                _cleanup(gateway, manifest, manifest_path)
    except OcrError:
        raise
    except Exception as exc:
        manifest["failure"] = safe_error(exc)
        persist_manifest(manifest_path, manifest, "COLLECT_FAILED")
        raise OcrError(f"Mistral OCR collection failed ({type(exc).__name__})") from exc
    return 0 if manifest["integrity"]["all_succeeded"] and manifest["integrity"]["complete"] and status == "SUCCESS" else 4


def run_ocr(options: Any) -> tuple[Path, int]:
    manifest_path = submit_ocr(options)
    deadline = time.monotonic() + options.wait_timeout_seconds
    while True:
        status = status_ocr(manifest_path, options.request_timeout_seconds)["batch"].get("status")
        if str(status) in TERMINAL_STATUSES:
            break
        if time.monotonic() >= deadline:
            manifest = read_manifest(manifest_path)
            persist_manifest(manifest_path, manifest, "WAIT_TIMEOUT")
            return manifest_path, 3
        time.sleep(options.poll_interval_seconds)
    return (
        manifest_path,
        collect_ocr(
            manifest_path,
            options.request_timeout_seconds,
            options.keep_remote_files,
        ),
    )


def cancel_ocr(
    manifest_path: Path,
    request_timeout_seconds: int = 300,
    gateway_factory: Any = MistralOcrGateway,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = read_manifest(manifest_path)
    job_id = manifest.get("batch", {}).get("id")
    if not isinstance(job_id, str) or not job_id:
        raise OcrError("manifest does not contain a batch job id")
    status = str(manifest.get("batch", {}).get("status", ""))
    if status in TERMINAL_STATUSES:
        return batch_snapshot(SimpleNamespace(**manifest["batch"]))
    try:
        with gateway_factory(request_timeout_seconds) as gateway:
            job = gateway.cancel_batch(job_id)
    except Exception as exc:
        raise OcrError(f"could not cancel Mistral batch ({type(exc).__name__})") from exc
    manifest["batch"].update(batch_snapshot(job))
    persist_manifest(manifest_path, manifest, "CANCEL_REQUESTED")
    return manifest["batch"]


def retry_ocr(
    manifest_path: Path,
    output_dir: Path | None = None,
    request_timeout_seconds: int = 300,
    gateway_factory: Any = MistralOcrGateway,
    legacy_inputs: bool = False,
) -> Path:
    manifest_path = manifest_path.resolve()
    manifest = read_manifest(manifest_path)
    if manifest.get("state") == "SUBMISSION_UNKNOWN":
        raise OcrError("submission outcome is unknown; do not retry until the remote job is reconciled")
    retry_ids = {
        document["custom_id"]
        for document in manifest["documents"]
        if document.get("state") in {"FAILED", "MISSING_RESULT"}
    }
    if not retry_ids:
        raise OcrError("manifest has no failed or missing documents to retry")
    limits = manifest["limits"]
    ocr_options = manifest["ocr_options"]
    options = SimpleNamespace(
        input_path=Path(manifest["input_path"]),
        output_dir=output_dir or Path(manifest["run_dir"]).parent,
        model=manifest["model_requested"],
        include_images=bool(ocr_options.get("include_images", True)),
        recursive=bool(ocr_options.get("recursive", False)),
        max_pages=int(limits["max_pages"]),
        max_cost_usd=float(limits["max_cost_usd"]),
        request_timeout_seconds=request_timeout_seconds,
        batch_timeout_hours=int(manifest.get("runtime", {}).get("batch_timeout_hours", 24)),
        keep_remote_files=True,
        legacy_inputs=legacy_inputs or manifest.get("legacy_inputs", False),
    )
    return submit_ocr(
        options,
        only_custom_ids=retry_ids,
        parent_manifest=manifest_path,
        gateway_factory=gateway_factory,
    )

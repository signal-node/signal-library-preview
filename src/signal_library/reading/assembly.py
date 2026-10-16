#!/usr/bin/env python3
"""Assemble a verified Signal Stack image OCR run, without network calls."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit
from ..ocr.files import check_path, check_tree, write_bytes_atomic
from ..registration import contained, checked_file, RegistrationError
from ..ocr.errors import OcrError


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inside(root: Path, relative: str) -> Path:
    return contained(root.resolve(), relative)


def assemble(manifest_path: Path, destination: Path, book_id: str, capture_id: str) -> None:
    manifest_path = check_path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported Signal Stack manifest")
    integrity = manifest.get("integrity", {})
    if not integrity.get("complete") or not integrity.get("all_succeeded"):
        raise ValueError("OCR run is incomplete or contains failures")
    documents = manifest.get("documents", [])
    if not documents or any(d.get("state") != "SUCCEEDED" for d in documents):
        raise ValueError("no successful documents, or a document failed")
    if any(d.get("warnings") for d in documents):
        raise ValueError("resolve document warnings before assembly")
    input_root = check_path(Path(manifest["input_root"])).resolve()
    documents_root = manifest_path.parent / "documents"
    check_tree(documents_root)
    if Path(manifest["run_dir"]).resolve() != manifest_path.parent:
        raise ValueError("run was relocated; use its original location for assembly")
    ordered = []
    for doc in documents:
        relative = doc["source_relative_path"]
        # A single-image source is one page; sequence is explicit in its filename.
        match = re.fullmatch(r"(\d{4,})\.(?:jpg|jpeg|png)", relative, re.IGNORECASE)
        if not match:
            raise ValueError("input names must be zero-padded page numbers: 0001.jpg, 0002.png, ...")
        sequence = int(match.group(1))
        original = inside(input_root, relative)
        if not original.is_file() or sha256(original) != doc["sha256"]:
            raise ValueError(f"source missing or hash changed: {relative}")
        if doc.get("pages") != 1:
            raise ValueError("this helper accepts only single-page JPEG/PNG sources")
        ordered.append((sequence, doc, original))
    ordered.sort(key=lambda item: item[0])
    if [item[0] for item in ordered] != list(range(1, len(ordered) + 1)):
        raise ValueError("page sequence has gaps or duplicates; expected 0001 through N")
    destination = check_path(destination).resolve()
    if destination.exists():
        raise ValueError("destination already exists; choose a new reading/revision folder")
    if destination.is_relative_to(manifest_path.parent):
        raise ValueError("keep the reading bundle outside the original OCR run")

    texts, page_rows, link_records = [], [], []
    run_id = manifest["run_id"]
    for sequence, doc, original in ordered:
        stem = doc["output_stem"]
        markdown_path = inside(documents_root, f"{stem}.md")
        markdown = markdown_path.read_text(encoding="utf-8")
        markers = re.findall(r"<!-- page: (\d+) -->", markdown)
        if len(markers) != 1 or not re.sub(r"<!-- page: \d+ -->", "", markdown).strip():
            raise ValueError(f"expected exactly one OCR page marker: {stem}")
        original_uri = original.as_uri()
        # Preserve relative image references by carrying the entire documents folder.
        def rewrite_image(match: re.Match[str]) -> str:
            label, target = match.group(1), match.group(2)
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                raise ValueError("unexpected remote/complex image reference; inspect manually")
            asset = inside(documents_root, unquote(target))
            if not asset.is_file():
                raise ValueError(f"missing local image: {target}")
            relative_asset = asset.relative_to(documents_root.resolve()).as_posix()
            return f"![{label}](documents/{quote(relative_asset, safe='/._-')})"
        markdown = re.sub(r"!\[([^\]]*)\]\(([^)\n]+)\)", rewrite_image, markdown)
        # The original URI is a convenience; source hash and sequence remain portable.
        texts.append(
            f"## 画像 {sequence:04d}\n\n"
            f"[原画像を開く]({original_uri})\n\n"
            f"<!-- book_id: {book_id}; capture_id: {capture_id}; "
            f"source_id: sha256:{doc['sha256']}; sequence: {sequence}; "
            f"run_id: {run_id}; custom_id: {doc['custom_id']} -->\n\n{markdown.strip()}\n"
        )
        page_rows.append([capture_id, f"sha256:{doc['sha256']}", 1, sequence, "full", "", "", "false", ""])
        link_records.append({
            "sequence": sequence, "source_id": f"sha256:{doc['sha256']}",
            "source_relative_path": doc["source_relative_path"],
            "custom_id": doc["custom_id"], "ocr_page_index": int(markers[0]),
        })
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".assemble-", dir=destination.parent))
    try:
        # 正常文書と実際の参照資材だけをコピーし、未参照の未知ファイルを巻き込まない。
        selected = set()
        for _, doc, _ in ordered:
            md = inside(documents_root, f"{doc['output_stem']}.md")
            selected.add(md)
            raw = inside(documents_root, f"{doc['output_stem']}.json")
            if raw.exists(): selected.add(checked_file(raw))
            for target in re.findall(r"!\[[^]]*]\(([^)\n]+)\)", md.read_text(encoding="utf-8")):
                selected.add(checked_file(inside(documents_root, unquote(target))))
        for source in selected:
            target = temporary / "documents" / source.relative_to(documents_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        reading = temporary / "reading.md"
        reading.write_text(f"# OCR統合本文（未校正）\n\n" + "\n".join(texts), encoding="utf-8")
        with (temporary / "pages.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["capture_id", "source_id", "file_page", "sequence", "region", "printed_page", "section", "verified", "notes"])
            writer.writerows(page_rows)
        record = {
            "schema_version": "book-reading-1", "book_id": book_id,
            "capture_id": capture_id, "run_id": run_id, "status": "unreviewed",
            "reading_sha256": sha256(reading), "source_manifest_sha256": sha256(manifest_path),
            "source_manifest_original_path": str(manifest_path), "pages": link_records,
        }
        (temporary / "assembly.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        destination.mkdir(exist_ok=False)
        files = sorted(temporary.rglob("*"), key=lambda path: path.name == "assembly.json")
        for source in files:
            if source.is_file():
                target = inside(destination, source.relative_to(temporary).as_posix())
                write_bytes_atomic(target, source.read_bytes(), exclusive=True)
        shutil.rmtree(temporary)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--capture-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"book-[A-Za-z0-9-]+", args.book_id) or not re.fullmatch(r"capture-\d+", args.capture_id):
        parser.error("use book-<uuid> and capture-NNN identifiers")
    try:
        assemble(args.manifest, args.destination, args.book_id, args.capture_id)
    except (ValueError, KeyError, OSError, TypeError, RegistrationError, OcrError) as exc:
        parser.exit(1, f"assembly stopped: {exc}\n")
    print(f"Created unreviewed reading bundle: {args.destination}")


if __name__ == "__main__":
    main()

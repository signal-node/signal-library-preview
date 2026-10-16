"""書籍・原画像・既存Markdownの登録。通信せず、原本を変更しない。"""

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .diagnostics import inspect_root
from .initialization import write_new
from .storage import CATALOG_FIELDS, is_redirect, read_record, regular_file

LOCK = ".signal-library.lock"
JOURNAL = ".signal-library-register.json"
METADATA = {"title", "contributors", "edition", "volume", "publisher", "publication_year", "isbn",
            "language", "acquisition_method", "scope", "use_scope", "bibliography_unknowns", "bibliography_evidence"}
CSV_HEADERS = {
    "pages.csv": "capture_id,source_id,file_page,sequence,region,printed_page,section,verified,notes\n",
    "review.csv": "reading_kind,reading_id,source_id,file_page,region,scope,status,checked_at,notes\n",
    "corrections.csv": "revision_id,source_id,file_page,region,ocr_page_index,original_text,corrected_text,reason,verified_at\n",
    "exports.csv": "export_id,service,book_id,source_ids,reading_kind,reading_id,output_path,output_sha256,destination_id_or_url,exported_at,status,notes\n",
}


class RegistrationError(Exception):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def checked_file(path):
    """全祖先を検査し、redirect/FIFO経由での読込みを拒否する。"""
    path = Path(os.path.abspath(path))
    if any(is_redirect(p) for p in (path, *path.parents)) or not regular_file(path):
        raise RegistrationError("通常ファイル以外やsymlink/junction経由の入力は扱えません。")
    return path


def digest(path):
    with checked_file(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RegistrationError("JSONのキーが重複しています。")
            result[key] = value
        return result
    if checked_file(path).stat().st_size > 16 * 1024 * 1024:
        raise RegistrationError("管理JSONが大きすぎます。")
    return json.loads(Path(path).read_bytes().decode("utf-8"), object_pairs_hook=unique)


def relative(value):
    """台帳パスを両OSで同じ意味に限定する。"""
    if not isinstance(value, str) or not value or "\\" in value:
        raise RegistrationError("保存パスは相対POSIX形式にしてください。")
    p = PurePosixPath(value)
    if not p.parts or p.is_absolute() or p.as_posix() != value or any(part in (".", "..") for part in p.parts):
        raise RegistrationError("保存範囲外または非正規形のパスです。")
    for part in p.parts:
        if (part.endswith((" ", ".")) or re.search(r'[<>:"|?*\x00-\x1f]', part)
                or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
            raise RegistrationError("Windowsで使用できない保存パスです。")
    return p


def contained(root, value):
    p = root.joinpath(*relative(value).parts)
    if any(is_redirect(q) for q in (p, *p.parents)):
        raise RegistrationError("保存先または参照先にsymlink/junctionがあります。")
    return p


def book_id(value):
    if not isinstance(value, str) or not value.startswith("book-") or "book-" + str(uuid.UUID(value[5:])) != value:
        raise RegistrationError("book_idはbook-に正規形UUIDを続けてください。")
    return value


@contextmanager
def locked(root):
    path = root / LOCK
    root_identity = root.stat()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    identity = os.fstat(fd)
    os.close(fd)
    try:
        yield
    finally:
        if not is_redirect(root) and root.exists() and (root.stat().st_dev, root.stat().st_ino) == (root_identity.st_dev, root_identity.st_ino):
            if path.exists() and not is_redirect(path) and (path.lstat().st_dev, path.lstat().st_ino) == (identity.st_dev, identity.st_ino):
                path.unlink()


def library_root(value, pending=False, legacy=False):
    if not value or not Path(value).expanduser().is_absolute():
        raise RegistrationError("保存先には絶対パスを指定してください。")
    root = Path(os.path.abspath(Path(value).expanduser()))
    if any(is_redirect(p) for p in (root, *root.parents)):
        raise RegistrationError("保存先にsymlink/junctionがあります。")
    state = inspect_root(root)
    permitted = {"ready"}
    if pending:
        permitted.update({"locked", "registering"})  # 取得済みの自分のlock。呼出し前にも検査する。
    if legacy:
        permitted.add("legacy_unmanaged")
    if state not in permitted:
        raise RegistrationError("保存先を利用できません。doctorで初期化・中断・ロックを確認してください。")
    if (root / JOURNAL).exists() and not pending:
        raise RegistrationError("登録が中断しています。同じ計画に--resumeを指定してください。")
    return root


def catalog_rows(root):
    with checked_file(root / "catalog.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != CATALOG_FIELDS:
            raise RegistrationError("catalogの形式が一致しません。")
        rows = list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in rows):
        raise RegistrationError("catalogの列数が一致しません。")
    ids = [book_id(row["book_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise RegistrationError("catalogの書籍IDが重複しています。")
    return rows


def catalog_text(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CATALOG_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def row_for(book):
    return {"book_id": book["book_id"], "title": book["title"], "contributors": "; ".join(book["contributors"]),
            "edition": book["edition"], "volume": book["volume"] if book["volume"] is not None else "",
            "language": book["language"], "scope": book["scope"], "book_path": f"books/{book['book_id']}/book.json",
            "status": book["status"], "updated_at": book["updated_at"]}


def validate_book(root, book, expected, pages_snapshot=None):
    if not isinstance(book, dict) or book.get("schema_version") != "book-library-1" or book_id(book.get("book_id")) != expected:
        raise RegistrationError("書籍台帳の形式・IDが一致しません。")
    for key in ("title", "edition", "language", "scope", "status"):
        if not isinstance(book.get(key), str):
            raise RegistrationError("書誌の文字列形式が不正です。")
    if not isinstance(book.get("contributors"), list) or not all(isinstance(x, str) for x in book["contributors"]):
        raise RegistrationError("著者は文字列の配列にしてください。")
    for key in ("originals", "ocr_runs", "curated_revisions", "markdown_sources"):
        if not isinstance(book.get(key, []), list):
            raise RegistrationError("書籍台帳の配列形式が不正です。")
    prefix = f"books/{expected}/"
    references = []
    sequences = {}
    source_ids = {s["source_id"] for s in book.get("originals", [])}
    for key, header_name in (("page_map_path", "pages.csv"), ("exports_path", "exports.csv")):
        if not book.get(key):
            raise RegistrationError("ページ対応・出力台帳の参照がありません。")
        with (io.StringIO(pages_snapshot) if key == "page_map_path" and pages_snapshot is not None else checked_file(contained(root, book[key])).open(encoding="utf-8", newline="")) as stream:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames != CSV_HEADERS[header_name].strip().split(","):
                raise RegistrationError("ページ対応・出力CSVの列が一致しません。")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise RegistrationError("ページ対応・出力CSVの列数が一致しません。")
                if key == "page_map_path" and row["source_id"] not in source_ids:
                    raise RegistrationError("ページ対応の原本IDを解決できません。")
    for source in book.get("originals", []):
        sha = source["sha256"]
        if not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha) or source["source_id"] != "sha256:" + sha:
            raise RegistrationError("原本ID・hashが一致しません。")
        capture = source["capture_id"]
        if not re.fullmatch(r"capture-[0-9]{3,}", capture) or type(source["sequence"]) is not int or source["sequence"] < 1:
            raise RegistrationError("captureとsequenceが不正です。")
        sequences.setdefault(capture, []).append(source["sequence"])
        references.append((source["path"], sha))
    for sequence in sequences.values():
        if sorted(sequence) != list(range(1, len(sequence) + 1)):
            raise RegistrationError("capture内の順序に欠落・重複があります。")
    for source in book.get("markdown_sources", []):
        if source["source_id"] != "sha256:" + source["sha256"]:
            raise RegistrationError("Markdownの原本IDが一致しません。")
        references.append((source["path"], source["sha256"]))
        references.extend((asset["path"], asset["sha256"]) for asset in source["assets"])
    for run in book.get("ocr_runs", []):
        references.append((run["manifest_path"], None))
        references.extend((doc["markdown_path"], None) for doc in run.get("documents", []))
    bundles = book.get('reading_bundles', [])
    if not isinstance(bundles, list):
        raise RegistrationError('読書版台帳の配列形式が不正です。')
    reading_ids = set()
    for bundle in bundles:
        from .reading.library import bundle_record
        key = (bundle['kind'], bundle['reading_id'])
        if key in reading_ids or not bundle['bundle_path'].startswith(prefix):
            raise RegistrationError('読書版のID重複または書籍外への参照があります。')
        reading_ids.add(key)
        if bundle != bundle_record(root, bundle['bundle_path']):
            raise RegistrationError('読書版台帳の内容と保存済み版が一致しません。')
        if not any(run['run_id']==bundle['run_id'] for run in book['ocr_runs']):
            raise RegistrationError('読書版の元OCR runを解決できません。')
        references.extend(((bundle['bundle_path'], bundle['bundle_sha256']),
                           (bundle['markdown_path'], bundle['markdown_sha256'])))
        value = read_json(contained(root,bundle['bundle_path']))
        if value['identity'].get('source_manifest_path') and not any(
                run['run_id']==bundle['run_id'] and run['manifest_path']==value['identity']['source_manifest_path']
                for run in book['ocr_runs']):
            raise RegistrationError('読書版の元manifestとOCR台帳が一致しません。')
        originals = {s['source_id']:s for s in book['originals'] if s['capture_id']==bundle['capture_id']}
        if {s['source_id'] for s in value['sources']}!=set(originals):
            raise RegistrationError('読書版とcaptureの原本IDが一致しません。')
        for source in value['sources']:
            original=originals[source['source_id']]
            if (source['original_path'],source['original_sha256'],source['sequence'])!=(original['path'],original['sha256'],original['sequence']):
                raise RegistrationError('読書版と登録原本の対応が一致しません。')
    reviews=book.get('reading_reviews',[])
    if not isinstance(reviews,list) or len({r['review_id'] for r in reviews})!=len(reviews):
        raise RegistrationError('照合記録の配列・IDが不正です。')
    for review in reviews:
        from .reading.review import load_review
        if not review['path'].startswith(prefix):
            raise RegistrationError('照合記録が書籍外を参照しています。')
        event=load_review(root,review['path'])
        if (event['plan']['review_id']!=review['review_id'] or event['bundle_path']!=review['bundle_path'] or
                event['bundle_sha256']!=review['bundle_sha256'] or event['book_id']!=expected or
                not any(b['bundle_path']==event['bundle_path'] for b in bundles)):
            raise RegistrationError('照合記録と登録済み読書版の対応が不正です。')
        references.append((review['path'],review['sha256']))
    for revision in book.get("curated_revisions", []):
        references.append((revision["markdown_path"], None))
        if revision.get("corrections_path"):
            references.append((revision["corrections_path"], None))
    for field in ("page_map_path", "exports_path"):
        if book.get(field):
            references.append((book[field], None))
    reading = book.get("active_reading", {})
    if reading.get("path"):
        kinds = {"ocr": ("ocr_runs", "run_id"), "curated": ("curated_revisions", "revision_id"), "markdown": ("markdown_sources", "source_id")}
        if reading.get("kind") not in kinds:
            raise RegistrationError("採用版の種類が不正です。")
        array, key = kinds[reading["kind"]]
        if not any(item.get(key) == reading.get("id") for item in book.get(array, [])):
            raise RegistrationError("採用版のIDを解決できません。")
        references.append((reading["path"], None))
        if reading.get('bundle_path'):
            if not any(b['bundle_path']==reading['bundle_path'] and b['bundle_sha256']==reading.get('bundle_sha256') and
                       b['markdown_path']==reading['path'] and b['reading_id']==reading.get('reading_id') and
                       b['kind']==reading['kind'] and b['format_status']=='complete' and
                       (b['run_id'] if b['kind']=='ocr' else b['reading_id'])==reading['id'] for b in bundles):
                raise RegistrationError('採用版のbundle・本文・状態が登録済み版と一致しません。')
    seen = {}
    from .exports.library import validate_records
    validate_records(root,book)
    for path, sha in references:
        if not path.startswith(prefix):
            raise RegistrationError("書籍外への参照があります。")
        target = contained(root, path)
        checked_file(target)
        if sha and digest(target) != sha:
            raise RegistrationError("台帳と実ファイルのhashが一致しません。")
        canonical = unicodedata.normalize("NFC", path).casefold()
        if canonical in seen and seen[canonical] != path:
            raise RegistrationError("参照パスの衝突があります。")
        seen[canonical] = path


def inventory(root):
    rows = catalog_rows(root)
    books = {}
    directory = contained(root, "books")
    for entry in directory.iterdir():
        if is_redirect(entry) or not entry.is_dir():
            raise RegistrationError("books内に未知のファイル・redirectがあります。")
        identifier = book_id(entry.name)
        book = read_json(entry / "book.json")
        validate_book(root, book, identifier)
        from .reading.review import validate_review_csv
        validate_review_csv(root,book)
        from .exports.library import validate_csv
        validate_csv(root,book)
        books[identifier] = book
    if set(books) != {row["book_id"] for row in rows}:
        raise RegistrationError("catalogと書籍フォルダが一致しません。")
    for row in rows:
        if row != {k: str(v) for k, v in row_for(books[row["book_id"]]).items()}:
            raise RegistrationError("catalogと書籍台帳の内容が一致しません。手動編集を確認してください。")
    return books, rows


def list_books(value, verify=False):
    root = library_root(value, legacy=True)
    books, rows = inventory(root)
    return {"status": "ok", "state": "validated" if verify else "listed", "books": rows,
            "count": len(books), "files_written": False, "network_used": False, "next_actions": []}


def input_file(base, value):
    if not isinstance(value, str):
        raise RegistrationError("入力パスは文字列にしてください。")
    path = Path(value).expanduser()
    return checked_file(path if path.is_absolute() else base / path)


def image_format(path):
    with path.open("rb") as stream:
        head = stream.read(12)
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg") and head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if ext == ".png" and head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    raise RegistrationError("JPEG/PNGの拡張子とシグネチャが一致しません。画質・復号の確認は別途必要です。")


def prepare(root, plan_path):
    plan_path = checked_file(Path(plan_path).absolute())
    plan = read_json(plan_path)
    if not isinstance(plan, dict) or set(plan) - {"schema_version", "book_id", "metadata", "capture_id", "order_confirmed", "images", "markdown"} or plan.get("schema_version") != "signal-library-registration-1":
        raise RegistrationError("登録計画の形式が不正です。")
    metadata = plan.get("metadata", {})
    if not isinstance(metadata, dict) or set(metadata) - METADATA:
        raise RegistrationError("書誌の項目が不正です。")
    for key, item in metadata.items():
        if key in {"contributors", "bibliography_unknowns", "bibliography_evidence"}:
            if not isinstance(item, list) or not all(isinstance(x, str) for x in item):
                raise RegistrationError("書誌の配列は文字列の配列にしてください。")
        elif item is not None and (not isinstance(item, (str, int)) or isinstance(item, bool)):
            raise RegistrationError("書誌の値が不正です。")
    images, markdown = plan.get("images", []), plan.get("markdown", [])
    if not isinstance(images, list) or not isinstance(markdown, list):
        raise RegistrationError("imagesとmarkdownは配列にしてください。")
    if images and plan.get("order_confirmed") is not True:
        raise RegistrationError("画像を目視し、順序・欠落・取得範囲を確認してorder_confirmedをtrueにしてください。")
    inputs = []
    for image in images:
        if not isinstance(image, dict) or set(image) - {"path", "scope"} or not isinstance(image.get("scope", ""), str):
            raise RegistrationError("画像入力の形式が不正です。")
        path = input_file(plan_path.parent, image["path"])
        inputs.append({"input": str(path), "sha256": digest(path), "extension": image_format(path), "scope": image.get("scope", "")})
    if len({p["sha256"] for p in inputs}) != len(inputs):
        raise RegistrationError("同一captureに同じ画像が重複しています。順序と欠落を確認してください。")
    md_inputs = []
    for item in markdown:
        if not isinstance(item, dict) or set(item) - {"path", "provenance", "assets"} or not isinstance(item.get("provenance"), str) or not item["provenance"].strip():
            raise RegistrationError("Markdownには由来provenanceを記入してください。")
        path = input_file(plan_path.parent, item["path"])
        if path.suffix.lower() != ".md":
            raise RegistrationError("Markdownは.mdファイルを指定してください。")
        path.read_bytes().decode("utf-8")
        assets = []
        names = set()
        if not isinstance(item.get("assets", []), list):
            raise RegistrationError("assetsは配列にしてください。")
        for asset in item.get("assets", []):
            if not isinstance(asset, str):
                raise RegistrationError("資材はMarkdownからの相対パスにしてください。")
            relative(asset)
            canonical = unicodedata.normalize("NFC", asset).casefold()
            if canonical in names or canonical == "source.md":
                raise RegistrationError("資材名が衝突しています。")
            names.add(canonical)
            target = input_file(path.parent, asset)
            assets.append({"name": asset, "input": str(target), "sha256": digest(target)})
        md_inputs.append({"input": str(path), "sha256": digest(path), "provenance": item["provenance"], "assets": assets})
    canonical = {"plan": plan, "images": inputs, "markdown": md_inputs}
    operation = hash_bytes(encode(canonical).encode())
    library = read_record(root / "library.json")
    identifier = book_id(plan.get("book_id") or "book-" + str(uuid.uuid5(uuid.UUID(library["library_id"]), operation)))
    return plan, identifier, operation, inputs, md_inputs


def new_book(identifier, metadata, now):
    book = {"schema_version": "book-library-1", "status": "draft", "book_id": identifier,
            "title": "", "contributors": [], "edition": "", "volume": None, "publisher": None,
            "publication_year": None, "isbn": None, "language": "", "bibliography_evidence": [],
            "bibliography_unknowns": [], "acquisition_method": "", "scope": "", "sensitivity": None,
            "use_scope": "", "service_permissions": {key: "pending" for key in ("dropbox", "mistral", "notion", "notebooklm", "chatgpt", "third_party_sharing")},
            "permission_notes": [], "originals": [], "ocr_runs": [], "curated_revisions": [],
            "active_reading": {"kind": None, "id": None, "path": None}, "page_map_path": f"books/{identifier}/pages.csv",
            "exports_path": f"books/{identifier}/exports.csv", "created_at": now, "updated_at": now, "aliases": [],
            "markdown_sources": [], "registration_operations": []}
    book.update(metadata)
    return book


def build_transaction(root, prepared, baseline=None):
    plan, identifier, operation, images, markdown = prepared
    if baseline is None:
        books, rows = inventory(root)
        existing = books.get(identifier)
        raw_book = (root / "books" / identifier / "book.json").read_bytes().decode("utf-8") if existing else None
        raw_catalog = (root / "catalog.csv").read_bytes().decode("utf-8")
        now = datetime.now(UTC).isoformat()
        raw_pages = contained(root, existing["page_map_path"]).read_bytes().decode("utf-8") if existing else None
        if not existing:
            old_hashes = {s["sha256"] for b in books.values() for s in b.get("originals", []) + b.get("markdown_sources", [])}
            if any(s["sha256"] in old_hashes for s in images + markdown):
                raise RegistrationError("同じ原本が別の書籍に登録されています。booksで既存book_idを確認してください。")
    else:
        raw_book, raw_catalog, raw_pages, now = baseline
        existing = json.loads(raw_book) if raw_book is not None else None
        if existing:
            validate_book(root, existing, identifier, pages_snapshot=raw_pages)
        reader = csv.DictReader(io.StringIO(raw_catalog))
        if reader.fieldnames != CATALOG_FIELDS:
            raise RegistrationError("中断記録のcatalogが不正です。")
        rows = list(reader)
    if existing and operation in existing.get("registration_operations", []):
        return None
    book = json.loads(json.dumps(existing)) if existing else new_book(identifier, plan.get("metadata", {}), now)
    if existing and any(book.get(k) != v for k, v in plan.get("metadata", {}).items()):
        raise RegistrationError("既存書誌と計画が異なります。別版は新規book_idで登録してください。")
    if not book.get("title") or not isinstance(book["title"], str):
        raise RegistrationError("書名を指定してください。不明の書誌を推測で補わないでください。")
    prefix = f"books/{identifier}/"
    files = []
    if not existing:
        files.extend({"path": prefix + name, "text": header, "sha256": hash_bytes(header.encode())} for name, header in CSV_HEADERS.items())
    if existing:
        for name, text in (("book-before.json", raw_book), ("pages-before.csv", raw_pages), ("catalog-before.csv", raw_catalog)):
            files.append({"path": prefix + f"metadata/{operation}/{name}", "text": text, "sha256": hash_bytes(text.encode())})
    if images:
        capture = plan.get("capture_id", "capture-001" if not existing else None)
        if not isinstance(capture, str) or not re.fullmatch(r"capture-[0-9]{3,}", capture):
            raise RegistrationError("追加画像には新しいcapture-NNNを指定してください。")
        if any(source["capture_id"] == capture for source in book["originals"]):
            raise RegistrationError("captureは既に存在します。上書きしません。")
        old_hashes = {source["sha256"] for source in book["originals"]}
        if any(image["sha256"] in old_hashes for image in images):
            raise RegistrationError("同じ画像が既に登録されています。既存captureを確認してください。")
        page_rows = []
        for sequence, image in enumerate(images, 1):
            path = prefix + f"originals/{capture}/{sequence:04d}{image['extension']}"
            files.append({"path": path, "input": image["input"], "sha256": image["sha256"]})
            book["originals"].append({"capture_id": capture, "source_id": "sha256:" + image["sha256"],
                "sha256": image["sha256"], "path": path, "file_pages": 1, "sequence": sequence,
                "acquired_at": now, "scope": image["scope"], "derived_from": None, "original_filename": Path(image["input"]).name})
            page_rows.append([capture, "sha256:" + image["sha256"], 1, sequence, "full", "", "", "false", "未照合"])
        page_path = contained(root, book["page_map_path"])
        before = raw_pages if existing else CSV_HEADERS["pages.csv"]
        stream = io.StringIO(newline="")
        csv.writer(stream, lineterminator="\n").writerows(page_rows)
        after = before + ("" if before.endswith("\n") else "\n") + stream.getvalue()
        files = [entry for entry in files if entry["path"] != book["page_map_path"]]
        files.append({"path": book["page_map_path"], "text": after, "sha256": hash_bytes(after.encode()),
                      "before": raw_pages if existing else None})
    for source in markdown:
        if any(m["sha256"] == source["sha256"] for m in book.get("markdown_sources", [])):
            raise RegistrationError("同じMarkdownが既に登録されています。由来と資材を確認してください。")
        folder = prefix + f"markdown/sha256-{source['sha256']}/"
        files.append({"path": folder + "source.md", "input": source["input"], "sha256": source["sha256"]})
        assets = []
        for asset in source["assets"]:
            target = folder + asset["name"]
            files.append({"path": target, "input": asset["input"], "sha256": asset["sha256"]})
            assets.append({"path": target, "sha256": asset["sha256"]})
        book.setdefault("markdown_sources", []).append({"source_id": "sha256:" + source["sha256"], "sha256": source["sha256"],
            "path": folder + "source.md", "provenance": source["provenance"], "registered_at": now, "assets": assets, "status": "unreviewed"})
    book["updated_at"] = now
    book.setdefault("registration_operations", []).append(operation)
    rows = [row for row in rows if row["book_id"] != identifier] + [row_for(book)]
    # 可変台帳は最後に公開。中断時はbefore/afterの完全一致だけ再開する。
    files.append({"path": prefix + "book.json", "text": encode(book), "sha256": hash_bytes(encode(book).encode()),
                  "before": raw_book})
    after = catalog_text(rows)
    files.append({"path": "catalog.csv", "text": after, "sha256": hash_bytes(after.encode()), "before": raw_catalog})
    names = [unicodedata.normalize("NFC", entry["path"]).casefold() for entry in files]
    if len(names) != len(set(names)) or any(a.startswith(b + "/") for a in names for b in names if a != b):
        raise RegistrationError("登録先パスが衝突しています。")
    return {"schema_version": "signal-library-register-1", "operation": operation, "book_id": identifier, "files": files, "baseline": [raw_book, raw_catalog, raw_pages, now]}


def publish(root, entry):
    target = contained(root, entry["path"])
    if target.exists():
        current = checked_file(target).read_bytes()
        if hash_bytes(current) == entry["sha256"]:
            return
        if entry.get("before") is None or current != entry["before"].encode():
            raise RegistrationError("登録先が計画と異なります。既存資料を上書きしません。")
    elif entry.get("before") is not None:
        raise RegistrationError("更新対象の台帳がありません。復旧前に確認してください。")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".signal-library-copy-", dir=target.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            if "input" in entry:
                with checked_file(entry["input"]).open("rb") as source:
                    while block := source.read(1024 * 1024):
                        output.write(block)
            else:
                output.write(entry["text"].encode())
            output.flush()
            os.fsync(output.fileno())
        if digest(staged) != entry["sha256"]:
            raise RegistrationError("コピー中に入力が変わりました。台帳を確定しません。")
        contained(root, entry["path"])
        if entry.get("before") is not None:
            if checked_file(target).read_bytes() != entry["before"].encode():
                raise RegistrationError("台帳が別の処理で変更されました。上書きしません。")
            os.replace(staged, target)
        else:
            os.link(staged, target)
    finally:
        staged.unlink(missing_ok=True)


def register(value, plan_path, apply=False, resume=False):
    root = library_root(value, pending=resume)
    if (root / LOCK).exists() or is_redirect(root / LOCK):
        raise RegistrationError("書込みロックがあります。実行中の処理と残留状態を確認してください。")
    prepared = prepare(root, plan_path)
    plan, identifier, operation, images, markdown = prepared
    journal = root / JOURNAL
    if journal.exists() or is_redirect(journal):
        if not resume:
            raise RegistrationError("中断した登録があります。同じ計画で--resumeを指定してください。")
        transaction = read_json(journal)
        if transaction.get("schema_version") != "signal-library-register-1" or transaction.get("operation") != operation:
            raise RegistrationError("中断記録と入力計画・hashが一致しません。")
        # コピー元・保存内容を元の計画とbefore台帳から再生成する。
        if transaction.get("book_id") != identifier or not isinstance(transaction.get("files"), list):
            raise RegistrationError("中断記録が不正です。")
        if build_transaction(root, prepared, transaction["baseline"]) != transaction:
            raise RegistrationError("中断記録の保存内容が登録計画と一致しません。")
    else:
        transaction = build_transaction(root, prepared)
    if transaction is None:
        return {"status": "ok", "state": "already_registered", "book_id": identifier, "files_written": False, "network_used": False, "next_actions": []}
    # 計画とjournalを再照合するため、登録対象はその書籍とcatalogのみに限定する。
    for entry in transaction["files"]:
        path = entry["path"]
        if path != "catalog.csv" and not path.startswith(f"books/{identifier}/"):
            raise RegistrationError("中断記録に対象外への書込みがあります。")
        contained(root, path)
        data = checked_file(entry["input"]).read_bytes() if "input" in entry else entry["text"].encode()
        if hash_bytes(data) != entry["sha256"]:
            raise RegistrationError("登録計画の入力または記録が変わっています。")
    result = {"status": "ok", "state": "planned", "book_id": identifier, "operation": operation,
              "image_count": len(images), "markdown_count": len(markdown), "files_written": False, "network_used": False,
              "next_actions": ["画像順・取得範囲・書誌と計画を確認し、--applyを指定してください。"]}
    if not apply:
        return result
    with locked(root):
        library_root(value, pending=True)
        if prepare(root, plan_path) != prepared:
            raise RegistrationError("検査後に計画・入力が変わりました。")
        if journal.exists():
            if read_json(journal) != transaction:
                raise RegistrationError("中断記録が変更されました。")
        else:
            # 同時更新が先に完了した場合は、古い計画を適用しない。
            if build_transaction(root, prepared) is None:
                return {**result, "state": "already_registered", "next_actions": []}
            for entry in transaction["files"]:
                target = contained(root, entry["path"])
                if entry.get("before") is not None and checked_file(target).read_bytes() != entry["before"].encode():
                    raise RegistrationError("検査後に台帳が変更されました。")
            write_new(journal, encode(transaction))
        for entry in transaction["files"]:
            publish(root, entry)
        inventory(root)
        if read_json(journal) != transaction:
            raise RegistrationError("中断記録が変更されたため完了扱いにしません。")
        journal.unlink()
    return {**result, "state": "registered", "files_written": True, "next_actions": []}


def adopt(value, apply=False):
    """旧形式を改編せず管理マーカーだけ追加。台帳不整合なら停止する。"""
    root = library_root(value, legacy=True)
    books, rows = inventory(root)
    if (root / "library.json").exists():
        return {"status": "ok", "state": "already_managed", "files_written": False, "network_used": False, "next_actions": []}
    before = hash_bytes(encode(books).encode())
    result = {"status": "ok", "state": "adoption_planned", "count": len(rows), "files_written": False, "network_used": False,
              "next_actions": ["旧台帳の検査結果を確認し、--applyで管理マーカーだけ追加してください。"]}
    if apply:
        with locked(root):
            library_root(value, pending=True, legacy=True)
            if hash_bytes(encode(inventory(root)[0]).encode()) != before:
                raise RegistrationError("検査後に旧台帳が変わりました。")
            if not (root / "inbox").exists():
                (root / "inbox").mkdir()
            if is_redirect(root / "inbox") or not (root / "inbox").is_dir():
                raise RegistrationError("inboxの形式を確認してください。")
            record = {"schema_version": "signal-library-1", "library_id": str(uuid.uuid4()), "created_at": datetime.now(UTC).isoformat()}
            write_new(root / "library.json", encode(record))
        result.update(state="adopted", files_written=True, next_actions=[])
    return result

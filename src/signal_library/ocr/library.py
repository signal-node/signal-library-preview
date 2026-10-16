"""登録済みcaptureと旧OCRワークフローを接続する。"""

import math
from pathlib import Path
from types import SimpleNamespace

from ..registration import (RegistrationError, book_id, checked_file, contained, digest, encode, hash_bytes,
                            inventory, library_root, locked, publish, read_json, row_for, catalog_text, catalog_rows, validate_book)
from ..initialization import write_new
from .domain import discover_documents
from .gateway import MistralOcrGateway, api_key_from_env
from .manifest import read_manifest, write_json_atomic
from .workflow import submit_ocr, status_ocr, collect_ocr

PRICE = 0.002
PRICE_DATE = "2026-10-15"
PRICE_SOURCE = "https://docs.mistral.ai/inference/pricing"


def plan_ocr(value, identifier, capture, model="mistral-ocr-4-1", max_pages=1, max_cost_usd=0.01, include_images=True, historical_model=False):
    root = library_root(value)
    book_id(identifier)
    if (type(max_pages) is not int or max_pages < 1 or not isinstance(max_cost_usd, (int, float))
            or not math.isfinite(max_cost_usd) or max_cost_usd <= 0 or model not in ({"mistral-ocr-4-0", "mistral-ocr-4-1"} if historical_model else {"mistral-ocr-4-1"})):
        raise RegistrationError("ページ・費用上限は正の有限値、modelは検証対象mistral-ocr-4-1を指定してください。")
    books, _ = inventory(root)
    if identifier not in books:
        raise RegistrationError("book_idが登録されていません。")
    selected = sorted((s for s in books[identifier]["originals"] if s["capture_id"] == capture), key=lambda s: s["sequence"])
    if not selected:
        raise RegistrationError("指定captureに画像がありません。")
    folder = contained(root, f"books/{identifier}/originals/{capture}")
    docs = discover_documents(folder)
    expected = {s["path"].split("/")[-1]: s for s in selected}
    if len(docs) != len(selected) or any(d.relative_path not in expected or d.sha256 != expected[d.relative_path]["sha256"] for d in docs):
        raise RegistrationError("capture実体と台帳が一致しません。追加ファイル・hashを確認してください。")
    pages = len(selected)
    if pages > max_pages or pages * PRICE > max_cost_usd:
        raise RegistrationError("計画がページ数または見積費用の上限を超えます。")
    plan = {"schema_version": "signal-library-ocr-plan-1", "book_id": identifier, "capture_id": capture,
            "model": model, "endpoint": "https://api.mistral.ai/v1/ocr", "mode": "batch",
            "include_images": include_images, "pages": pages, "max_pages": max_pages, "max_cost_usd": max_cost_usd,
            "estimated_cost_usd": round(pages * PRICE, 6), "price_per_page_usd": PRICE, "price_checked_on": PRICE_DATE,
            "price_source": PRICE_SOURCE, "billing_limit_is_enforced": False,
            "sources": [{"source_id": s["source_id"], "path": s["path"], "sha256": s["sha256"], "sequence": s["sequence"]} for s in selected]}
    fingerprint = hash_bytes(encode(plan).encode())
    return {"status": "ok", "state": "ocr_planned", "plan": plan, "plan_hash": fingerprint,
            "files_written": False, "network_used": False,
            "next_actions": ["対象・設定・費用と外部送信可否を確認し、submitへ同じplan hashを渡してください。見積りは請求上限ではありません。"]}


def no_redirect_tree(root):
    if not root.exists():
        return
    if root.is_symlink() or root.is_junction():
        raise RegistrationError("OCR出力先がredirectです。")
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction() or (not path.is_dir() and not path.is_file()):
            raise RegistrationError("OCR出力内にredirect・特殊ファイルがあります。")


def options_for(root, plan):
    return SimpleNamespace(input_path=contained(root, f"books/{plan['book_id']}/originals/{plan['capture_id']}"),
        output_dir=contained(root, f"books/{plan['book_id']}/ocr/{hash_bytes(encode(plan).encode())}"),
        model=plan["model"], include_images=plan["include_images"], recursive=False,
        max_pages=plan["max_pages"], max_cost_usd=plan["max_cost_usd"],
        expected_sources={Path(s["path"]).name: s["sha256"] for s in plan["sources"]}, request_timeout_seconds=60,
        custom_id_map={Path(s["path"]).name: s["custom_id"] for s in plan["sources"] if s.get("custom_id")},
        batch_timeout_hours=24, keep_remote_files=True)


def index_run(root, identifier, manifest_path):
    """更新前台帳と新台帳を記録してから公開。途中失敗は同じrunで回復する。"""
    manifest = read_manifest(manifest_path)
    journal = manifest_path.parent / "library-index.json"
    if journal.exists():
        transaction = read_json(journal)
        if transaction.get("run_id") != manifest["run_id"]:
            raise RegistrationError("OCR台帳更新記録が一致しません。")
        before_book, before_catalog, record = transaction["baseline"]
        expected = index_entries(root, identifier, before_book, before_catalog, record)
        if transaction["files"] != expected or record["run_id"] != manifest["run_id"] or record["manifest_path"] != manifest_path.relative_to(root).as_posix():
            raise RegistrationError("OCR台帳更新記録が予定内容と一致しません。")
        for entry in expected:
            publish(root, entry)
        inventory(root)
        if read_json(journal) != transaction:
            raise RegistrationError("OCR台帳更新記録が変更されました。")
        journal.unlink()
    books, rows = inventory(root)
    book = books[identifier]
    record = {"run_id": manifest["run_id"], "manifest_path": manifest_path.relative_to(root).as_posix(),
        "model_requested": manifest["model_requested"], "model_reported": manifest.get("batch", {}).get("model"), "documents": []}
    plan_sources = read_json(manifest_path.parent.parent / "operation.json")["plan"]["sources"]
    by_path = {s["path"]: s for s in book["originals"]}
    sources = {Path(s["path"]).name: by_path[s["path"]] for s in plan_sources if s["path"] in by_path}
    for doc in manifest["documents"]:
        if doc["source_relative_path"] not in sources or sources[doc["source_relative_path"]]["sha256"] != doc["sha256"]:
            raise RegistrationError("OCR原本を台帳へ対応付けられません。")
        if doc.get("state") == "SUCCEEDED" and doc.get("output_stem"):
            md = manifest_path.parent / "documents" / (doc["output_stem"] + ".md")
            checked_file(md)
            record["documents"].append({"source_id": sources[doc["source_relative_path"]]["source_id"], "custom_id": doc["custom_id"],
                "markdown_path": md.relative_to(root).as_posix()})
    old = next((run for run in book["ocr_runs"] if run["run_id"] == record["run_id"]), None)
    if old == record:
        return
    before_book = checked_file(root / f"books/{identifier}/book.json").read_bytes().decode("utf-8")
    before_catalog = checked_file(root / "catalog.csv").read_bytes().decode("utf-8")
    entries = index_entries(root, identifier, before_book, before_catalog, record)
    transaction = {"run_id": manifest["run_id"], "baseline": [before_book, before_catalog, record], "files": entries}
    write_new(journal, encode(transaction))
    for entry in entries:
        publish(root, entry)
    inventory(root)
    if read_json(journal) != transaction:
        raise RegistrationError("OCR台帳更新記録が変更されました。")
    journal.unlink()


def index_entries(root, identifier, before_book, before_catalog, record):
    import json, csv, io
    book = json.loads(before_book)
    validate_book(root, book, identifier)
    if not record["manifest_path"].startswith(f"books/{identifier}/ocr/"):
        raise RegistrationError("OCR台帳に書籍外への参照があります。")
    checked_file(contained(root, record["manifest_path"]))
    source_ids = {s["source_id"] for s in book["originals"]}
    for doc in record["documents"]:
        if doc["source_id"] not in source_ids or not doc["markdown_path"].startswith(f"books/{identifier}/ocr/"):
            raise RegistrationError("OCR文書の原本・保存先を解決できません。")
        checked_file(contained(root, doc["markdown_path"]))
    book["ocr_runs"] = [run for run in book["ocr_runs"] if run["run_id"] != record["run_id"]] + [record]
    rows = list(csv.DictReader(io.StringIO(before_catalog)))
    rows = [row for row in rows if row["book_id"] != identifier] + [row_for(book)]
    return [{"path": path, "text": text, "sha256": hash_bytes(text.encode()), "before": before}
            for path, text, before in ((f"books/{identifier}/book.json", encode(book), before_book),
                                      ("catalog.csv", catalog_text(rows), before_catalog))]


def submit(value, identifier, capture, confirm_plan, allow_upload=False, model="mistral-ocr-4-1", max_pages=1, max_cost_usd=0.01,
           include_images=True, gateway_factory=MistralOcrGateway):
    result = plan_ocr(value, identifier, capture, model, max_pages, max_cost_usd, include_images)
    return submit_plan(value, result, confirm_plan, allow_upload, gateway_factory)


def submit_plan(value, result, confirm_plan, allow_upload=False, gateway_factory=MistralOcrGateway):
    identifier = result["plan"]["book_id"]
    if not allow_upload or confirm_plan != result["plan_hash"]:
        raise RegistrationError("送信の許可と現在のplan hashを明示してください。計画が変わった場合は再確認が必要です。")
    # 人工gatewayの試験以外は、書込前にキーとSDK不足を確認する。
    if gateway_factory is MistralOcrGateway:
        api_key_from_env()
        import importlib.util
        if importlib.util.find_spec("mistralai") is None:
            raise RegistrationError("OCR extraの導入が必要です。初期設定手順を確認してください。")
    root = library_root(value)
    options = options_for(root, result["plan"])
    with locked(root):
        no_redirect_tree(options.input_path)
        no_redirect_tree(options.output_dir)
        if options.output_dir.exists():
            raise RegistrationError("この計画の送信記録があります。再送せずstatus/collectで確認してください。")
        # ロック下で原本と計画を再照合する。
        library_root(value, pending=True)
        if result["plan"].get("parent_manifest") and digest(contained(root, result["plan"]["parent_manifest"])) != result["plan"]["parent_manifest_sha256"]:
            raise RegistrationError("検査後に再試行の元manifestが変わりました。再計画してください。")
        options.output_dir.mkdir(parents=True)
        operation = options.output_dir / "operation.json"
        write_new(operation, encode({"state": "STARTED", "plan": result["plan"], "plan_hash": confirm_plan, "upload_authorized": True}))
        try:
            manifest_path = submit_ocr(options,
                only_custom_ids=set(options.custom_id_map.values()) if options.custom_id_map else None,
                parent_manifest=contained(root, result["plan"]["parent_manifest"]) if result["plan"].get("parent_manifest") else None,
                gateway_factory=gateway_factory)
        except Exception:
            # 原実装のmanifestと送信記録を保持。結果不明時に自動再送しない。
            raise
        write_json_atomic(operation, {"state": "SUBMITTED", "plan": result["plan"], "plan_hash": confirm_plan,
            "upload_authorized": True, "manifest_path": manifest_path.relative_to(root).as_posix()})
        index_run(root, identifier, manifest_path)
    return {"status": "ok", "state": "ocr_submitted", "manifest_path": manifest_path.relative_to(root).as_posix(),
            "files_written": True, "network_used": True, "next_actions": ["ocr status/collectで同じmanifestを確認してください。再submitしないでください。"]}


def load_managed(value, manifest_relative, locked_context=False):
    return _load_managed(value, manifest_relative, locked_context)


def _load_managed(value, manifest_relative, locked_context=False, historical_paths=False):
    root = library_root(value, pending=locked_context)
    path = contained(root, manifest_relative)
    checked_file(path)
    parts = path.relative_to(root).parts
    if len(parts) != 6 or parts[0] != "books" or parts[2] != "ocr" or parts[-1] != "manifest.json":
        raise RegistrationError("本ツールが作成した書籍内OCR manifestを指定してください。")
    identifier = book_id(parts[1])
    manifest = read_manifest(path)
    operation = read_json(path.parent.parent / "operation.json")
    plan = operation["plan"]
    if operation["plan_hash"] != hash_bytes(encode(plan).encode()) or operation["plan_hash"] != parts[3] or plan["book_id"] != identifier:
        raise RegistrationError("OCR操作記録が一致しません。")
    from .bindings import JOURNAL, check_paths, validate_history
    if (path.parent/JOURNAL).exists() and not historical_paths:
        raise RegistrationError('OCRの保存先再接続が中断しています。同じocr rebind --applyで再開してください。')
    options = options_for(root, plan)
    check_paths(root,path,manifest,plan,historical_paths)
    validate_history(root,path,manifest,plan)
    if manifest["model_requested"] != plan["model"]:
        raise RegistrationError("manifestのモデルが計画と一致しません。")
    expected = {s["sha256"] for s in plan["sources"]}
    if {d["sha256"] for d in manifest["documents"]} != expected:
        raise RegistrationError("manifestの原本hashが計画と一致しません。")
    if len(manifest["documents"]) != len(expected) or len({d["custom_id"] for d in manifest["documents"]}) != len(expected):
        raise RegistrationError("manifestの原本・custom_idに重複があります。")
    paths = {Path(s["path"]).name: s for s in plan["sources"]}
    for doc in manifest["documents"]:
        source = paths.get(doc["source_relative_path"])
        if source is None or source["sha256"] != doc["sha256"] or (source.get("custom_id") and source["custom_id"] != doc["custom_id"]):
            raise RegistrationError("manifestの原本対応が計画と一致しません。")
        checked_file(contained(root, source["path"]))
        if digest(contained(root, source["path"])) != source["sha256"]:
            raise RegistrationError("登録原本が変更されています。")
        if doc.get("output_stem"):
            from .results import output_stem
            output_stem(doc)
    return root, path, manifest, operation


def follow(value, manifest_relative, collect=False, gateway_factory=MistralOcrGateway, keep_remote_files=True):
    root, path, manifest, operation = load_managed(value, manifest_relative)
    identifier = operation["plan"]["book_id"]
    if manifest.get("purpose") == "signal-stack-ocr-combined":
        from .results import validate_run
        report = validate_run(path.parent, allow_remote_files=keep_remote_files)
        return {"status": "ok" if report["ok"] else "action_required", "state": "combined", "report": report,
                "files_written": False, "network_used": False, "next_actions": []}
    with locked(root):
        no_redirect_tree(path.parent)
        code = collect_ocr(path, 60, keep_remote_files, gateway_factory) if collect else None
        if not collect:
            status_ocr(path, 60, gateway_factory)
        index_run(root, identifier, path)
    current = read_manifest(path)
    return {"status": "ok" if code in (0, None) else "action_required", "state": current["state"],
            "run_id": current["run_id"], "batch_status": current.get("batch", {}).get("status"),
            "integrity": current.get("integrity"), "cleanup": current.get("cleanup", {}).get("state"),
            "files_written": True, "network_used": True,
            "next_actions": ["回収した本文・図表を原画像と照合してください。"] if collect else ["完了後、同じmanifestをcollectしてください。"]}

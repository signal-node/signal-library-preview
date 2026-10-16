"""中断・再試行・旧runの読取り移入・結果結合。既存jobを再送しない。"""

import copy
from pathlib import Path

from ..initialization import write_new
from ..registration import RegistrationError, checked_file, contained, digest, encode, hash_bytes, inventory, library_root, locked, read_json
from .errors import OcrError
from .domain import TERMINAL_STATUSES
from .files import check_path, check_tree, copy_new
from .manifest import write_json_atomic
from .results import merge_plan, merge_runs, validate_run, read_manifest as read_result_manifest, document_paths, output_stem
from . import library


def recover(value, operation_relative, gateway_factory=library.MistralOcrGateway):
    root = library_root(value)
    operation_path = contained(root, operation_relative)
    operation = read_json(operation_path)
    plan = operation['plan']
    fingerprint = hash_bytes(encode(plan).encode())
    if operation_path.name != 'operation.json' or operation_path.parent != library.options_for(root, plan).output_dir or operation.get('plan_hash') != fingerprint:
        raise RegistrationError('OCR操作記録の保存先・hashが一致しません。')
    check_tree(operation_path.parent)
    candidates = list(operation_path.parent.glob('*/manifest.json'))
    if len(candidates) != 1:
        raise RegistrationError('manifestが一意に決まりません。送信記録を保持して手動確認してください。再submitしないでください。')
    path = candidates[0]
    result = library.follow(value, path.relative_to(root).as_posix(), gateway_factory=gateway_factory)
    # follow成功後のみ、既存操作へmanifestを関連付ける。ネットワークcreateは行わない。
    with locked(root):
        if read_json(operation_path) != operation:
            raise RegistrationError('復旧中に操作記録が変わりました。')
        write_json_atomic(operation_path, {**operation, 'state': 'RECOVERED', 'manifest_path': path.relative_to(root).as_posix()})
    return {**result, 'manifest_path': path.relative_to(root).as_posix()}


def retry_plan(value, manifest_relative, max_pages, max_cost_usd):
    root, path, manifest, operation = library.load_managed(value, manifest_relative)
    if manifest['state'] in {'SUBMISSION_UNKNOWN', 'SUBMITTING'} or manifest.get('batch', {}).get('status') not in TERMINAL_STATUSES:
        raise RegistrationError('既存jobが確定していません。status/recoverで確認し、再送しないでください。')
    integrity = manifest.get('integrity', {})
    if integrity.get('unknown_custom_ids') or integrity.get('duplicate_custom_ids'):
        raise RegistrationError('期待外・重複結果は手動確認が必要です。自動retryしません。')
    failed = [doc for doc in manifest['documents'] if doc.get('state') in {'FAILED', 'MISSING_RESULT'}]
    if not failed:
        raise RegistrationError('回収済み結果に再試行対象がありません。先にcollectしてください。')
    original = operation['plan']
    # 通常計画と同じ上限検査・原本照合を使い、課金対象だけ失敗文書に絞る。
    library.plan_ocr(value, original['book_id'], original['capture_id'], model=original['model'],
        max_pages=max(max_pages, len(original['sources'])), max_cost_usd=max(max_cost_usd, len(original['sources'])*library.PRICE), include_images=original['include_images'], historical_model=True)
    import math
    if type(max_pages) is not int or max_pages < len(failed) or not math.isfinite(max_cost_usd) or max_cost_usd < len(failed)*library.PRICE:
        raise RegistrationError('再試行対象がページまたは見積費用の上限を超えます。')
    selected = {doc['source_relative_path']: doc for doc in failed}
    sources = [{**source, 'custom_id': selected[Path(source['path']).name]['custom_id']} for source in original['sources'] if Path(source['path']).name in selected]
    if len(sources) != len(failed):
        raise RegistrationError('再試行対象の原本対応を解決できません。')
    plan = {**original, 'intent': 'retry', 'parent_manifest': path.relative_to(root).as_posix(), 'parent_run_id': manifest['run_id'],
            'parent_manifest_sha256': digest(path), 'sources': sources, 'pages': len(failed), 'max_pages': max_pages,
            'max_cost_usd': max_cost_usd, 'estimated_cost_usd': round(len(failed)*library.PRICE, 6)}
    return {'status': 'ok', 'state': 'retry_planned', 'plan': plan, 'plan_hash': hash_bytes(encode(plan).encode()),
            'files_written': False, 'network_used': False, 'next_actions': ['失敗文書だけの計画と費用を確認し、送信許可とplan hashを指定してください。']}


def retry(value, manifest_relative, max_pages, max_cost_usd, apply=False, confirm_plan=None, allow_upload=False, gateway_factory=library.MistralOcrGateway):
    result = retry_plan(value, manifest_relative, max_pages, max_cost_usd)
    if not apply:
        return result
    return library.submit_plan(value, result, confirm_plan, allow_upload, gateway_factory)


def merge(value, manifests, apply=False):
    root = library_root(value)
    loaded = [library.load_managed(value, path) for path in manifests]
    if len(loaded) < 2:
        raise RegistrationError('結合には元runと再試行runが必要です。')
    plans = [item[3]['plan'] for item in loaded]
    identifier, capture = plans[0]['book_id'], plans[0]['capture_id']
    if any(plan['book_id'] != identifier or plan['capture_id'] != capture or plan['model'] != plans[0]['model'] for plan in plans):
        raise RegistrationError('異なる書籍・capture・modelのrunは結合しません。')
    expected = {(s['path'], s['sha256']) for s in plans[0]['sources']}
    if any(not {(s['path'], s['sha256']) for s in plan['sources']} <= expected for plan in plans[1:]):
        raise RegistrationError('結合対象に元run外の原本があります。')
    for _, path, manifest, _ in loaded:
        if manifest.get('batch', {}).get('status') not in TERMINAL_STATUSES or manifest['state'] in {'SUBMISSION_UNKNOWN', 'SUBMITTING'}:
            raise RegistrationError('結合対象のjobが未確定です。')
    paths = [item[1].parent for item in loaded]
    data, combined = merge_plan(paths)
    identity = {**plans[0], 'intent': 'merge', 'merge_fingerprint': combined['merge_fingerprint']}
    operation_hash = hash_bytes(encode(identity).encode())
    operation_dir = contained(root, f'books/{identifier}/ocr/{operation_hash}')
    result = {'status': 'ok', 'state': 'merge_planned', 'documents': len(combined['documents']),
              'files_written': False, 'network_used': False, 'next_actions': ['結合対象を確認して--applyを指定してください。']}
    if not apply:
        return result
    with locked(root):
        if merge_plan(paths)[1] != combined:
            raise RegistrationError('検査後に結合元が変わりました。再計画してください。')
        check_path(operation_dir)
        operation_dir.mkdir(parents=True, exist_ok=True)
        operation = operation_dir/'operation.json'
        record = {'state': 'MERGING', 'plan': identity, 'plan_hash': operation_hash}
        if operation.exists():
            if read_json(operation) != record:
                raise RegistrationError('既存の結合操作が一致しません。')
        else:
            write_new(operation, encode(record))
        final = merge_runs(paths, operation_dir, 'combined')
        library.index_run(root, identifier, final/'manifest.json')
    return {**result, 'state': 'merged', 'files_written': True, 'manifest_path': (final/'manifest.json').relative_to(root).as_posix(), 'next_actions': []}


def import_run(value, identifier, capture, source_manifest, apply=False):
    """旧絶対パス履歴を保持し、現在の登録原本hashへ対応させたコピーを作る。"""
    root = library_root(value)
    source = check_path(Path(source_manifest).absolute()).resolve()
    checked_file(source)
    old = read_result_manifest(source.parent)
    report = validate_run(source.parent, allow_remote_files=True)
    if report['missing_markdown'] or report['missing_json'] or report['empty_markdown'] or report['broken_image_links']:
        raise RegistrationError('旧OCRの成功文書・資材が欠落しています。')
    is_combined = old.get('purpose') == 'signal-stack-ocr-combined'
    archived_models = set()
    books, _ = inventory(root)
    if identifier not in books:
        raise RegistrationError('書籍が未登録です。')
    originals = [s for s in books[identifier]['originals'] if s['capture_id'] == capture]
    by_hash = {s['sha256']: s for s in originals}
    if len(by_hash) != len(originals):
        raise RegistrationError('登録原本hashの対応が一意ではありません。')
    documents, sources, files = [], [], []
    seen = set()
    for doc in old['documents']:
        sha = doc.get('sha256')
        if sha not in by_hash or sha in seen:
            raise RegistrationError('旧OCRと登録原本のhashが対応しないか重複しています。')
        seen.add(sha)
        original = by_hash[sha]
        sources.append({'source_id': original['source_id'], 'path': original['path'], 'sha256': sha,
                        'sequence': original['sequence'], 'custom_id': doc['custom_id']})
        documents.append({**doc, 'output_stem': output_stem(doc), 'source_relative_path': Path(original['path']).name})
        if is_combined:
            archive = contained(source.parent, doc['source_manifest'])
            archived = read_json(archive)
            matched = [d for d in archived.get('documents', []) if d.get('custom_id') == doc['custom_id']]
            if len(matched) != 1 or matched[0].get('sha256') != sha:
                raise RegistrationError('旧結合runの元manifestと原本対応が一致しません。')
            archived_models.add(archived.get('model_requested'))
            files.append({'source': str(archive), 'path': doc['source_manifest'], 'sha256': digest(archive)})
        if doc.get('state') == 'SUCCEEDED':
            md, raw, assets = document_paths(source.parent, doc)
            for item in [md, raw] + (list(assets.rglob('*')) if assets.is_dir() else []):
                if item.is_dir():
                    continue
                files.append({'source': str(item), 'path': item.relative_to(source.parent).as_posix(), 'sha256': digest(item)})
    if not sources or (not is_combined and not isinstance(old.get('run_id'), str)):
        raise RegistrationError('旧OCRのrun ID・原本対応が不正です。')
    requested_model = old.get('model_requested')
    if is_combined and len(archived_models) == 1:
        requested_model = next(iter(archived_models))
    plan = {'schema_version': 'signal-library-ocr-plan-1', 'intent': 'import', 'book_id': identifier, 'capture_id': capture,
            'model': requested_model, 'include_images': old.get('ocr_options', {}).get('include_images', True),
            'pages': len(sources), 'max_pages': max(len(sources), 1), 'max_cost_usd': max(len(sources)*library.PRICE, .01),
            'sources': sorted(sources, key=lambda s:s['sequence']), 'legacy_manifest_sha256': digest(source)}
    operation_hash = hash_bytes(encode(plan).encode())
    operation_dir = contained(root, f'books/{identifier}/ocr/{operation_hash}')
    run_dir = operation_dir/'imported'
    normalized = {**copy.deepcopy(old), 'run_dir': str(run_dir), 'input_path': str(library.options_for(root, plan).input_path),
                  'input_root': str(library.options_for(root, plan).input_path), 'documents': documents, 'legacy_manifest_path': 'legacy-manifest.json',
                  'model_requested': requested_model, 'run_id': old.get('run_id') or ('imported-combined-' + plan['legacy_manifest_sha256'][:32])}
    result = {'status': 'ok', 'state': 'import_planned', 'documents': len(sources), 'files_written': False, 'network_used': False,
              'next_actions': ['旧履歴と原本対応を確認し、--applyで新しい書籍内runへコピーしてください。']}
    if not apply:
        return result
    with locked(root):
        check_path(operation_dir)
        operation_dir.mkdir(parents=True, exist_ok=True)
        record = {'state': 'IMPORTED', 'plan': plan, 'plan_hash': operation_hash}
        operation = operation_dir/'operation.json'
        if operation.exists() and read_json(operation) != record:
            raise RegistrationError('旧run移入の操作記録が一致しません。')
        if not operation.exists():
            write_new(operation, encode(record))
        run_dir.mkdir(exist_ok=True)
        copy_new(source, run_dir/'legacy-manifest.json', plan['legacy_manifest_sha256'])
        for entry in files:
            copy_new(Path(entry['source']), contained(run_dir, entry['path']), entry['sha256'])
        target = run_dir/'manifest.json'
        if target.exists():
            library.load_managed(value, target.relative_to(root).as_posix(), locked_context=True)
        else:
            write_new(target, encode(normalized))
        library.index_run(root, identifier, target)
    return {**result, 'state': 'imported', 'files_written': True, 'manifest_path': target.relative_to(root).as_posix(), 'next_actions': []}

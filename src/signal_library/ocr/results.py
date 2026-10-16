"""旧OCR Skillから移管した検証・結果結合。原runは変更しない。"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from pathlib import Path
from typing import Any

from ..registration import encode, hash_bytes, read_json, relative, contained, digest, checked_file
from .errors import OcrError
from .files import check_path, check_tree, copy_new, write_bytes_atomic


class ResultError(OcrError):
    pass


def read_manifest(run: Path) -> dict[str, Any]:
    run = check_path(run).resolve()
    check_tree(run)
    value = read_json(run / 'manifest.json')
    if not isinstance(value, dict) or value.get('schema_version') != 1 or not isinstance(value.get('documents'), list) or not value['documents']:
        raise ResultError('unsupported or empty OCR manifest')
    seen = set()
    for doc in value['documents']:
        identifier = doc.get('custom_id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', identifier) or identifier in seen:
            raise ResultError('invalid/duplicate custom_id')
        seen.add(identifier)
        relative(doc['source_relative_path'])
        output_stem(doc)
    return value


def output_stem(document: dict[str, Any]) -> str:
    value = document.get('output_stem') or Path(document['source_relative_path']).stem
    if not isinstance(value, str) or len(relative(value).parts) != 1:
        raise ResultError('unsafe output stem')
    return value


def document_paths(run, document):
    stem = output_stem(document)
    return tuple(contained(run, f'documents/{stem}{suffix}') for suffix in ('.md', '.json', '.assets'))


def local_image_targets(markdown_path):
    text = checked_file(markdown_path).read_text(encoding='utf-8')
    if re.search(r'<\s*img\b|!\[[^]]*]\s*\[', text, re.I):
        raise ResultError('HTML/reference images require review before OCR merge')
    targets = []
    for value in re.findall(r'!\[[^]]*]\(([^)]+)\)', text):
        if re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', value) or value.startswith('//'):
            raise ResultError('external OCR image reference')
        targets.append(contained(markdown_path.parent, value))
    return targets


def validate_run(run, allow_remote_files=False):
    run = check_path(run).resolve()
    manifest = read_manifest(run)
    succeeded = [doc for doc in manifest['documents'] if doc.get('state') == 'SUCCEEDED']
    failed = [doc for doc in manifest['documents'] if doc.get('state') != 'SUCCEEDED']
    missing_md, missing_json, empty, broken, warnings = [], [], [], [], []
    assets = set()
    for doc in succeeded:
        md, raw, folder = document_paths(run, doc)
        identifier = doc['custom_id']
        if manifest.get('purpose') == 'signal-stack-ocr-combined':
            archived = read_json(contained(run, doc['source_manifest']))
            matched = [d for d in archived.get('documents', []) if d.get('custom_id') == identifier]
            if len(matched) != 1 or matched[0].get('sha256') != doc.get('sha256'):
                raise ResultError('combined source manifest identity mismatch')
        if not md.is_file():
            missing_md.append(identifier)
        else:
            if not checked_file(md).read_text(encoding='utf-8').strip():
                empty.append(identifier)
            for target in local_image_targets(md):
                if not target.is_file():
                    broken.append(identifier)
                else:
                    checked_file(target)
        if not raw.is_file():
            missing_json.append(identifier)
        else:
            raw_record = read_json(raw)
            if not isinstance(raw_record, dict) or raw_record.get('custom_id', identifier) != identifier:
                raise ResultError('materialized JSON custom_id mismatch')
        if folder.is_dir():
            check_tree(folder)
            assets.update(path for path in folder.rglob('*') if path.is_file())
        if doc.get('warnings'):
            warnings.append(identifier)
    combined = manifest.get('purpose') == 'signal-stack-ocr-combined'
    cleanup = manifest.get('cleanup', {})
    cleanup_complete = cleanup.get('state') == 'CLEANED' and not cleanup.get('pending_file_ids')
    integrity = manifest.get('integrity', {})
    provider_complete = combined or manifest.get('batch', {}).get('status') == 'SUCCESS'
    ok = bool(provider_complete and integrity.get('complete') and not failed and not missing_md and not missing_json
              and not empty and not broken and not warnings and (cleanup_complete or allow_remote_files))
    report = {'run_directory': str(run), 'ok': ok, 'state': manifest.get('state'), 'batch_status': manifest.get('batch', {}).get('status'),
        'documents': len(manifest['documents']), 'succeeded': len(succeeded), 'failed': len(failed), 'markdown': len(succeeded)-len(missing_md),
        'json': len(succeeded)-len(missing_json), 'assets': len(assets), 'empty_markdown': len(empty), 'broken_image_links': len(broken),
        'missing_markdown': missing_md, 'missing_json': missing_json, 'document_warnings': warnings,
        'cleanup_state': cleanup.get('state'), 'cleanup_complete': cleanup_complete, 'remote_files_allowed': allow_remote_files}
    if not cleanup_complete:
        report['warning'] = 'remote cleanup is incomplete or retention selected'
    return report


def selected_documents(sources):
    successful, observed, identities = {}, set(), {}
    for run, manifest in sources:
        for doc in manifest['documents']:
            identifier = doc['custom_id']
            identity = (doc['source_relative_path'], doc.get('sha256'), doc.get('mime_type'), doc.get('pages'))
            if identifier in identities and identities[identifier] != identity:
                raise ResultError('custom_id refers to different original content')
            identities[identifier] = identity
            observed.add(identifier)
            if doc.get('state') == 'SUCCEEDED':
                if identifier in successful:
                    raise ResultError('duplicate successful custom_id: ' + identifier)
                if doc.get('warnings'):
                    raise ResultError('successful document has unresolved warnings')
                successful[identifier] = (run, doc)
    return successful, observed - set(successful)


def merge_plan(source_runs):
    if len(source_runs) < 2:
        raise ResultError('merge requires at least two source runs')
    sources = [(check_path(run).resolve(), read_manifest(run)) for run in source_runs]
    labels = {run: manifest.get('run_id') for run, manifest in sources}
    if any(not isinstance(label, str) or len(relative(label).parts) != 1 for label in labels.values()) or len(set(labels.values())) != len(sources):
        raise ResultError('source run IDs must be unique')
    successful, unresolved = selected_documents(sources)
    if unresolved:
        raise ResultError('unresolved failed custom_id(s): ' + ', '.join(sorted(unresolved)))
    stems, entries, combined_docs = set(), [], []
    for identifier, (run, doc) in sorted(successful.items(), key=lambda item: (item[1][1]['source_relative_path'].casefold(), item[0])):
        stem = output_stem(doc)
        if stem.casefold() in stems:
            raise ResultError('output-name collision')
        stems.add(stem.casefold())
        md, raw, assets = document_paths(run, doc)
        raw_record = read_json(raw)
        if not isinstance(raw_record, dict) or raw_record.get('custom_id', identifier) != identifier:
            raise ResultError('materialized JSON custom_id mismatch')
        if not md.is_file() or not raw.is_file() or not md.read_text(encoding='utf-8').strip():
            raise ResultError('missing materialized output')
        for target in local_image_targets(md):
            checked_file(target)
            if target != md and (assets != target and assets not in target.parents):
                raise ResultError('image reference escapes its document assets')
        files = [md, raw] + (sorted(p for p in assets.rglob('*') if p.is_file()) if assets.is_dir() else [])
        for path in files:
            entries.append({'source': str(path), 'path': 'documents/' + path.relative_to(run/'documents').as_posix(), 'sha256': digest(path)})
        combined_docs.append({**doc, 'source_run': labels[run], 'source_directory': run.name, 'source_manifest': f'sources/{labels[run]}.manifest.json'})
    summaries = []
    for run, manifest in sources:
        path = f'sources/{labels[run]}.manifest.json'
        relative(path)
        entries.append({'source': str(run/'manifest.json'), 'path': path, 'sha256': digest(run/'manifest.json')})
        summaries.append({'run_id': manifest.get('run_id'), 'run_directory': run.name, 'manifest': path,
            'documents': len(manifest['documents']), 'batch_status': manifest.get('batch', {}).get('status'), 'cleanup_state': manifest.get('cleanup', {}).get('state')})
    plan = {'schema_version': 'signal-library-merge-1', 'files': entries, 'documents': combined_docs, 'source_runs': summaries}
    fingerprint = hash_bytes(encode(plan).encode())
    cleanup_complete = all(manifest.get('cleanup', {}).get('state') == 'CLEANED' and not manifest.get('cleanup', {}).get('pending_file_ids') for _, manifest in sources)
    same_roots = {manifest.get('input_root') for _, manifest in sources}
    same_models = {manifest.get('model_requested') for _, manifest in sources}
    combined = {'schema_version': 1, 'purpose': 'signal-stack-ocr-combined', 'run_id': uuid.uuid5(uuid.NAMESPACE_URL, fingerprint).hex,
        'state': 'COLLECTED', 'source_runs': summaries, 'documents': combined_docs,
        'counts': {'documents': len(combined_docs), 'succeeded': len(combined_docs), 'failed': 0},
        'integrity': {'complete': True, 'all_succeeded': True, 'output_name_collisions': [], 'unresolved_custom_ids': []},
        'cleanup': {'state': 'CLEANED' if cleanup_complete else 'PENDING', 'pending_file_ids': []},
        'input_root': next(iter(same_roots)) if len(same_roots) == 1 else None,
        'model_requested': next(iter(same_models)) if len(same_models) == 1 else None, 'merge_fingerprint': fingerprint}
    return plan, combined


def merge_runs(source_runs, output_root, name=None):
    plan, combined = merge_plan(source_runs)
    output_root = check_path(output_root).resolve()
    final_name = name or 'combined-' + combined['merge_fingerprint'][:20]
    if len(relative(final_name).parts) != 1:
        raise ResultError('unsafe destination name')
    destination = contained(output_root, final_name)
    journal = destination/'.merge-plan.json'
    if destination.exists():
        check_tree(destination)
        if (destination/'manifest.json').exists():
            current = read_json(destination/'manifest.json')
            if current.get('merge_fingerprint') != combined['merge_fingerprint'] or not validate_run(destination, True)['ok']:
                raise ResultError('destination already exists or changed')
            for entry in plan['files']:
                if digest(contained(destination, entry['path'])) != entry['sha256']:
                    raise ResultError('existing combined output changed')
            return destination
        if not journal.is_file() or read_json(journal) != plan:
            raise ResultError('interrupted merge differs from source plan')
    else:
        destination.mkdir(parents=True, exist_ok=False)
        write_bytes_atomic(journal, encode(plan).encode(), exclusive=True)
    allowed = {entry['path'] for entry in plan['files']} | {'.merge-plan.json'}
    if any(path.relative_to(destination).as_posix() not in allowed for path in destination.rglob('*') if path.is_file()):
        raise ResultError('unexpected data in interrupted merge')
    for entry in plan['files']:
        copy_new(Path(entry['source']), contained(destination, entry['path']), entry['sha256'])
    combined['run_dir'] = str(destination)
    write_bytes_atomic(destination/'manifest.json', encode(combined).encode(), exclusive=True)
    if read_json(journal) != plan:
        raise ResultError('merge journal changed')
    journal.unlink()
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description='OCR結果の検証・結合')
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate')
    validate.add_argument('run', type=Path)
    validate.add_argument('--allow-remote-files', action='store_true')
    merge = commands.add_parser('merge')
    merge.add_argument('runs', nargs='+', type=Path)
    merge.add_argument('--output-root', required=True, type=Path)
    merge.add_argument('--name')
    args = parser.parse_args(argv)
    if args.command == 'validate':
        result = validate_run(args.run, args.allow_remote_files)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1
    print(merge_runs(args.runs, args.output_root, args.name))
    return 0

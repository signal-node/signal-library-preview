"""登録済みOCRから不変の読書版を作成し、台帳へ接続する。外部通信なし。"""

import contextlib
import json

from ..registration import (RegistrationError, catalog_rows, catalog_text, checked_file,
                            contained, digest, encode, hash_bytes, inventory, library_root,
                            locked, publish, read_json, row_for, validate_book)
from ..initialization import write_new
from ..ocr.library import load_managed
from ..ocr.results import validate_run
from .bundle import prepare_bundle, publish_bundle, validate_bundle

INDEX_JOURNAL = '.reading-index.json'


def bundle_record(root, bundle_relative):
    path = contained(root, bundle_relative)
    value = validate_bundle(path.parent)
    identity = value['identity']
    expected = f"books/{identity['book_id']}/" + ('reading/' if identity['kind']=='ocr' else 'curated/') + identity['reading_id'] + '/bundle.json'
    if bundle_relative != expected:
        raise RegistrationError('読書版の保存先がmanifestのIDと一致しません。')
    files = {entry['path']:entry['sha256'] for entry in value['files']}
    return {'reading_id':identity['reading_id'], 'kind':identity['kind'],
            'run_id':identity['run_id'], 'capture_id':identity['capture_id'],
            'bundle_path':bundle_relative, 'bundle_sha256':digest(path),
            'markdown_path':path.parent.relative_to(root).as_posix()+'/reading.md',
            'markdown_sha256':files['reading.md'], 'format_status':value['format_status'],
            'review_status':'unreviewed'}


def index_entries(root, identifier, before_book, before_catalog, record):
    book = json.loads(before_book)
    validate_book(root, book, identifier)
    expected = bundle_record(root, record['bundle_path'])
    if record != expected or not record['bundle_path'].startswith(f'books/{identifier}/'):
        raise RegistrationError('読書版登録記録が実体と一致しません。')
    run = next((r for r in book['ocr_runs'] if r['run_id']==record['run_id']), None)
    if run is None:
        raise RegistrationError('読書版の元OCR runが登録されていません。')
    bundles = book.setdefault('reading_bundles', [])
    existing = next((r for r in bundles if r['reading_id']==record['reading_id'] and r['kind']==record['kind']), None)
    if existing is not None and existing != record:
        raise RegistrationError('既存の読書版IDを変更できません。')
    if existing is None:
        bundles.append(record)
    if record['kind']=='curated':
        revision = {'revision_id':record['reading_id'], 'based_on_run_id':record['run_id'],
                    'source_ids':[s['source_id'] for s in read_json(contained(root,record['bundle_path']))['sources']],
                    'markdown_path':record['markdown_path'],
                    'corrections_path':record['markdown_path'].removesuffix('reading.md')+'corrections.json',
                    'bundle_path':record['bundle_path'], 'verified_at':None}
        revisions = book['curated_revisions']
        previous = next((r for r in revisions if r['revision_id']==revision['revision_id']), None)
        if previous is not None and previous != revision:
            raise RegistrationError('既存revisionの内容と一致しません。')
        if previous is None:
            revisions.append(revision)
    import csv, io
    rows = list(csv.DictReader(io.StringIO(before_catalog)))
    rows = [r for r in rows if r['book_id']!=identifier] + [row_for(book)]
    validate_book(root, book, identifier)
    return [{'path':path,'text':text,'sha256':hash_bytes(text.encode()),'before':before}
            for path,text,before in ((f'books/{identifier}/book.json',encode(book),before_book),
                                     ('catalog.csv',catalog_text(rows),before_catalog))]


def recover_index(root, identifier):
    journal = contained(root, f'books/{identifier}/{INDEX_JOURNAL}')
    if not journal.exists():
        return
    transaction = read_json(journal)
    before_book, before_catalog = transaction['baseline']
    entries = index_entries(root, identifier, before_book, before_catalog, transaction['record'])
    if transaction.get('schema_version')!='signal-library-reading-index-1' or transaction['files']!=entries:
        raise RegistrationError('読書版の台帳更新記録が予定内容と一致しません。')
    for entry in entries:
        publish(root,entry)
    inventory(root)
    if read_json(journal)!=transaction:
        raise RegistrationError('読書版の台帳更新記録が変更されました。')
    journal.unlink()


def index_bundle(root, identifier, record):
    recover_index(root,identifier)
    book_path = contained(root, f'books/{identifier}/book.json')
    before_book = checked_file(book_path).read_bytes().decode('utf-8')
    before_catalog = checked_file(root/'catalog.csv').read_bytes().decode('utf-8')
    entries = index_entries(root,identifier,before_book,before_catalog,record)
    if all(entry['text']==entry['before'] for entry in entries):
        return
    transaction = {'schema_version':'signal-library-reading-index-1','record':record,
                   'baseline':[before_book,before_catalog],'files':entries}
    journal = contained(root,f'books/{identifier}/{INDEX_JOURNAL}')
    write_new(journal,encode(transaction))
    recover_index(root,identifier)


def gather(root, manifest_relative, locked_context=False):
    root, path, manifest, operation = load_managed(str(root),manifest_relative,locked_context)
    identifier = operation['plan']['book_id']
    book = inventory(root)[0][identifier]
    runs = [r for r in book['ocr_runs'] if r['run_id']==manifest['run_id'] and r['manifest_path']==manifest_relative]
    if len(runs)!=1 or not validate_run(path.parent,allow_remote_files=True)['ok']:
        raise RegistrationError('警告・欠落のない登録済みOCR runを指定してください。')
    capture = operation['plan']['capture_id']
    originals = sorted((s for s in book['originals'] if s['capture_id']==capture),key=lambda s:s['sequence'])
    docs = {d['source_id']:d for d in runs[0]['documents']}
    if len(docs)!=len(runs[0]['documents']) or set(docs)!={s['source_id'] for s in originals}:
        raise RegistrationError('統合対象はcapture全件に対応するrunが必要です。部分retryは先にmergeしてください。')
    manifest_docs = {d['custom_id']:d for d in manifest['documents']}
    pages = []
    for source in originals:
        doc = docs[source['source_id']]
        raw = manifest_docs.get(doc['custom_id'])
        if raw is None or raw['sha256']!=source['sha256'] or raw.get('pages')!=1:
            raise RegistrationError('OCR文書と登録原画像の対応を解決できません。')
        expected_md = path.parent/'documents'/(raw['output_stem']+'.md')
        if doc['markdown_path']!=expected_md.relative_to(root).as_posix():
            raise RegistrationError('OCR台帳のMarkdown参照がrunと一致しません。')
        pages.append({'source_id':source['source_id'],'sequence':source['sequence'],
                      'original_path':source['path'],'original_sha256':source['sha256'],
                      'markdown_path':doc['markdown_path'],'markdown_sha256':digest(expected_md),
                      'custom_id':doc['custom_id']})
    return identifier, capture, manifest['run_id'], pages


def build(value, manifest_relative, reading_id, kind='ocr', plan_path=None, apply=False, supersedes=None):
    root = library_root(value)
    plan = read_json(plan_path) if plan_path else None
    # 回復journalの書籍をmanifestの厳密な管理パスから取得する。
    _, manifest_path, _, operation = load_managed(str(root),manifest_relative)
    identifier = operation['plan']['book_id']
    with locked(root) if apply else contextlib.nullcontext():
        if apply:
            recover_index(root,identifier)
        elif (root/f'books/{identifier}/{INDEX_JOURNAL}').exists():
            raise RegistrationError('台帳更新が中断しています。同じbuildを--applyで再開してください。')
        identifier, capture, run_id, pages = gather(root,manifest_relative,apply)
        identity = {'book_id':identifier,'capture_id':capture,'run_id':run_id,'kind':kind,'reading_id':reading_id,
                    'source_manifest_path':manifest_relative,'source_manifest_sha256':digest(manifest_path)}
        if supersedes:
            previous = bundle_record(root,supersedes)
            if (kind!='curated' or previous['run_id']!=run_id or previous['capture_id']!=capture or
                    not supersedes.startswith(f'books/{identifier}/') or
                    previous not in inventory(root)[0][identifier].get('reading_bundles',[])):
                raise RegistrationError('前版は同じ書籍・capture・runの読書版を指定してください。')
            identity['supersedes'] = {'bundle_path':supersedes,'sha256':previous['bundle_sha256']}
        destination = f'books/{identifier}/'+('reading/' if kind=='ocr' else 'curated/')+reading_id
        manifest, _, transaction = prepare_bundle(root,destination,identity,pages,plan)
        if apply:
            publish_bundle(root,destination,identity,pages,plan)
            index_bundle(root,identifier,bundle_record(root,destination+'/bundle.json'))
        return {'status':'ok','state':'reading_created' if apply else 'reading_planned',
                'book_id':identifier,'bundle_path':destination+'/bundle.json',
                'plan_hash':hash_bytes(encode(transaction).encode()),'format_status':manifest['format_status'],
                'review_status':'unreviewed','issues':manifest['issues'],
                'files_written':apply,'network_used':False,
                'next_actions':['原画像と比較し、未解決箇所・照合範囲を確認してください。採用版は自動変更しません。']}

"""派生出力と既存exports.csvを、回復可能な台帳更新で接続する。"""

import csv
import io
import json

from ..initialization import write_new
from ..registration import (RegistrationError, checked_file, contained, digest, encode, hash_bytes,
                            inventory, publish, read_json, row_for, catalog_text, validate_book)

JOURNAL = '.export-index.json'
FIELDS = ['export_id','service','book_id','source_ids','reading_kind','reading_id','output_path','output_sha256',
          'destination_id_or_url','exported_at','status','notes']


def output_record(root, manifest_path):
    path=contained(root,manifest_path)
    value=read_json(path)
    format=value.get('format')
    if format=='html':
        from .html import validate
        output='index.html'
    elif format=='pdf':
        from .pdf import validate
        output='book.pdf'
    else:raise RegistrationError('未対応の派生出力形式です。')
    validate(path.parent)
    expected=f"books/{value['book_id']}/exports/{format}/{value['export_id']}/export.json"
    if expected!=manifest_path:
        raise RegistrationError('派生出力のID・保存先が一致しません。')
    files={f['path']:f['sha256'] for f in value['files']}
    if files['source.md']!=value['source']['sha256']:
        raise RegistrationError('派生出力の原文snapshotと元版のhashが一致しません。')
    return {'export_id':value['export_id'],'format':value['format'],'manifest_path':manifest_path,
            'manifest_sha256':digest(path),'output_path':path.parent.relative_to(root).as_posix()+'/'+output,
            'output_sha256':files[output],'source':value['source'],'created_at':value['created_at'],
            'validation':value['validation']}


def csv_row(identifier, record):
    return {'export_id':record['export_id'],'service':'local-'+record['format'],'book_id':identifier,
            'source_ids':';'.join(record['source']['source_ids']),'reading_kind':record['source']['kind'],
            'reading_id':record['source']['id'],'output_path':record['output_path'],
            'output_sha256':record['output_sha256'],'destination_id_or_url':'','exported_at':record['created_at'],
            'status':'generated_unverified','notes':encode({'manifest_path':record['manifest_path'],
                        'manifest_sha256':record['manifest_sha256'],'validation':record['validation']}).strip()}


def rows_from(text):
    reader=csv.DictReader(io.StringIO(text))
    if reader.fieldnames!=FIELDS:raise RegistrationError('exports.csvの列が一致しません。')
    rows=list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in rows):
        raise RegistrationError('exports.csvの列数が一致しません。')
    return rows


def source_registered(book, source):
    if source['kind']=='markdown':
        return source['source_ids']==[source['id']] and any(s['source_id']==source['id'] and s['path']==source['path'] and s['sha256']==source['sha256']
                   and s['assets']==source['assets'] for s in book['markdown_sources'])
    return any(b['kind']==source['kind'] and b['reading_id']==source['id'] and b['markdown_path']==source['path']
               and b['markdown_sha256']==source['sha256'] and b['bundle_path']==source.get('bundle_path')
               and b['bundle_sha256']==source.get('bundle_sha256') for b in book.get('reading_bundles',[]))


def validate_records(root, book):
    records=book.get('local_exports',[])
    if not isinstance(records,list) or len({r['export_id'] for r in records})!=len(records):
        raise RegistrationError('ローカル派生出力の台帳・IDが不正です。')
    for record in records:
        if (not record['manifest_path'].startswith(f"books/{book['book_id']}/") or
                output_record(root,record['manifest_path'])!=record or not source_registered(book,record['source'])):
            raise RegistrationError('派生出力と台帳・元版の対応が一致しません。')
        if record['source']['kind']!='markdown':
            value=read_json(contained(root,record['source']['bundle_path']))
            folder=record['source']['bundle_path'].removesuffix('bundle.json')
            assets=[{'path':folder+f['path'],'sha256':f['sha256']} for f in value['files'] if f['path'].startswith('assets/')]
            if (record['source']['source_ids']!=[s['source_id'] for s in value['sources']] or
                    record['source']['assets']!=assets):
                raise RegistrationError('派生出力と元bundleの原本・資材対応が一致しません。')
    checks=book.get('export_checks',[])
    if not isinstance(checks,list) or len({c['check_id'] for c in checks})!=len(checks):
        raise RegistrationError('表示確認台帳の配列・IDが不正です。')
    for check in checks:
        from .checks import record as check_record
        if (not check['path'].startswith(f"books/{book['book_id']}/") or check_record(root,check['path'])!=check or
                not any(r['manifest_path']==check['manifest_path'] and r['manifest_sha256']==check['manifest_sha256'] for r in records)):
            raise RegistrationError('表示確認台帳の対象版・hashが一致しません。')


def validate_csv(root, book):
    records=book.get('local_exports',[])
    if not records:return
    rows=rows_from(checked_file(contained(root,book['exports_path'])).read_bytes().decode('utf-8'))
    for record in records:
        expected=csv_row(book['book_id'],record)
        related=[r for r in rows if r['export_id']==record['export_id']]
        if related!=[expected]:raise RegistrationError('exports.csvと派生出力の台帳が一致しません。')


def entries(root, identifier, baseline, record):
    before_book,before_catalog,before_exports=baseline
    book=json.loads(before_book)
    validate_book(root,book,identifier)
    if output_record(root,record['manifest_path'])!=record or not source_registered(book,record['source']):
        raise RegistrationError('派生出力の登録予定と実体・元版が一致しません。')
    if not record['manifest_path'].startswith(f'books/{identifier}/'):
        raise RegistrationError('別書籍の出力は登録できません。')
    records=book.setdefault('local_exports',[])
    previous=next((r for r in records if r['export_id']==record['export_id']),None)
    if previous is not None and previous!=record:
        raise RegistrationError('既存export_idには別の出力を登録できません。')
    if previous is None:records.append(record)
    rows=rows_from(before_exports)
    expected=csv_row(identifier,record)
    existing=[r for r in rows if r['export_id']==record['export_id']]
    if existing and existing!=[expected]:raise RegistrationError('exports.csvの同じIDに別の記録があります。')
    if not existing:rows.append(expected)
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=FIELDS,lineterminator='\n')
    writer.writeheader();writer.writerows(rows)
    catalog=list(csv.DictReader(io.StringIO(before_catalog)))
    catalog=[r for r in catalog if r['book_id']!=identifier]+[row_for(book)]
    validate_book(root,book,identifier)
    return [{'path':path,'text':text,'sha256':hash_bytes(text.encode()),'before':before}
            for path,text,before in ((f'books/{identifier}/book.json',encode(book),before_book),
                                     ('catalog.csv',catalog_text(catalog),before_catalog),
                                     (book['exports_path'],stream.getvalue(),before_exports))]


def check_entries(root, identifier, baseline, record):
    from .checks import record as check_record
    before_book,before_catalog,before_exports=baseline
    book=json.loads(before_book)
    validate_book(root,book,identifier)
    if (record!=check_record(root,record['path']) or not record['path'].startswith(f'books/{identifier}/') or
            not any(r['manifest_path']==record['manifest_path'] and r['manifest_sha256']==record['manifest_sha256']
                    for r in book.get('local_exports',[]))):
        raise RegistrationError('表示確認を登録済みの対象出力へ対応できません。')
    checks=book.setdefault('export_checks',[])
    previous=next((c for c in checks if c['check_id']==record['check_id']),None)
    if previous is not None and previous!=record:raise RegistrationError('既存の表示確認IDを変更できません。')
    if previous is None:checks.append(record)
    validate_book(root,book,identifier)
    # 確認は不変の別記録に追加し、生成当時のmanifest/CSV状態を変えない。
    return [{'path':path,'text':text,'sha256':hash_bytes(text.encode()),'before':before}
            for path,text,before in ((f'books/{identifier}/book.json',encode(book),before_book),
                                     ('catalog.csv',before_catalog,before_catalog),
                                     (book['exports_path'],before_exports,before_exports))]


def planned_entries(root,identifier,baseline,record,kind):
    if kind=='output':return entries(root,identifier,baseline,record)
    if kind=='check':return check_entries(root,identifier,baseline,record)
    raise RegistrationError('未知の派生出力台帳操作です。')


def recover(root, identifier):
    journal=contained(root,f'books/{identifier}/{JOURNAL}')
    if not journal.exists():return
    transaction=read_json(journal)
    expected=planned_entries(root,identifier,transaction['baseline'],transaction['record'],transaction.get('kind','output'))
    if transaction.get('schema_version')!='signal-library-export-index-1' or transaction['files']!=expected:
        raise RegistrationError('派生出力の台帳更新journalが予定内容と一致しません。')
    for entry in expected:publish(root,entry)
    inventory(root)
    if read_json(journal)!=transaction:raise RegistrationError('派生出力の台帳更新journalが変更されました。')
    journal.unlink()


def index(root, identifier, manifest_path):
    record=output_record(root,manifest_path)
    index_record(root,identifier,record,'output')


def index_check(root, identifier, path):
    from .checks import record
    index_record(root,identifier,record(root,path),'check')


def index_record(root,identifier,record,kind):
    recover(root,identifier)
    book_path=contained(root,f'books/{identifier}/book.json')
    book=read_json(book_path)
    baseline=[checked_file(path).read_bytes().decode('utf-8') for path in
              (book_path,root/'catalog.csv',contained(root,book['exports_path']))]
    planned=planned_entries(root,identifier,baseline,record,kind)
    if all(e['text']==e['before'] for e in planned):return
    transaction={'schema_version':'signal-library-export-index-1','kind':kind,'baseline':baseline,'record':record,'files':planned}
    write_new(contained(root,f'books/{identifier}/{JOURNAL}'),encode(transaction))
    recover(root,identifier)

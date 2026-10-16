"""版を固定した原典照合記録と採用版。整形完了から照合済みを推測しない。"""

import contextlib
import csv
import io
import json
import re
from datetime import datetime

from ..initialization import write_new
from ..registration import (RegistrationError, checked_file, contained, encode, hash_bytes,
                            inventory, library_root, locked, publish, read_json, row_for,
                            catalog_text, validate_book)
from ..ocr.files import write_bytes_atomic
from .bundle import validate_bundle
from .library import bundle_record, recover_index

JOURNAL = '.reading-state.json'
REVIEW_FIELDS = ['reading_kind','reading_id','source_id','file_page','region','scope','status','checked_at','notes']


def review_record(root, bundle_path, plan):
    expected = {'schema_version','review_id','reviewer','checked_at','notes','ranges'}
    if not isinstance(plan,dict) or set(plan)!=expected or plan['schema_version']!='signal-library-reading-review-1':
        raise RegistrationError('照合計画の形式・項目が不正です。')
    if not isinstance(plan['review_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',plan['review_id']):
        raise RegistrationError('照合記録のIDが不正です。')
    for key in ('reviewer','notes'):
        if not isinstance(plan[key],str) or not plan[key].strip():
            raise RegistrationError('照合者と記録理由が必要です。')
    when = datetime.fromisoformat(plan['checked_at'])
    if when.tzinfo is None or when.utcoffset() is None:
        raise RegistrationError('照合日時にはタイムゾーンを付けてください。')
    bundle = bundle_record(root,bundle_path)
    value = read_json(contained(root,bundle_path))
    sources = {s['source_id']:s for s in value['sources']}
    if not isinstance(plan['ranges'],list) or not plan['ranges']:
        raise RegistrationError('照合した原本・範囲を指定してください。')
    ranges, seen = [], set()
    for entry in plan['ranges']:
        fields = {'source_id','file_page','region','scope','status','compared_with_original','notes'}
        if not isinstance(entry,dict) or set(entry)!=fields or entry['source_id'] not in sources:
            raise RegistrationError('照合範囲の項目または原本対応が不正です。')
        if (type(entry['file_page']) is not int or entry['file_page']!=1 or
                not isinstance(entry['region'],str) or not entry['region'].strip() or
                entry['scope'] not in {'full','text','math','figures','tables'} or
                entry['status'] not in {'unreviewed','needs_review','verified'} or
                type(entry['compared_with_original']) is not bool or not isinstance(entry['notes'],str)):
            raise RegistrationError('照合の位置・対象・状態が不正です。')
        if entry['status']=='verified' and not entry['compared_with_original']:
            raise RegistrationError('verifiedには原画像との比較の明示が必要です。')
        key=(entry['source_id'],entry['region'],entry['scope'])
        if key in seen:
            raise RegistrationError('同じ照合範囲が重複しています。')
        seen.add(key)
        source=sources[entry['source_id']]
        # 原画像が今も記録した版であることを確認し、位置とhashを照合記録へ固定する。
        from .bundle import snapshot
        snapshot(root,source,'original_path','original_sha256')
        ranges.append({**entry,'original_path':source['original_path'],'original_sha256':source['original_sha256']})
    return {'schema_version':'signal-library-reading-review-record-1', 'plan':plan,
            'book_id':value['identity']['book_id'],'reading_kind':bundle['kind'],'reading_id':bundle['reading_id'],
            'bundle_path':bundle_path,'bundle_sha256':bundle['bundle_sha256'],
            'reading_sha256':bundle['markdown_sha256'],'ranges':ranges}


def load_review(root, path):
    event=read_json(contained(root,path))
    expected=review_record(root,event['bundle_path'],event['plan'])
    if event!=expected or path!=f"books/{event['book_id']}/reviews/{event['plan']['review_id']}.json":
        raise RegistrationError('照合記録の実体・保存先・使用版が一致しません。')
    return event



def event_rows(event):
    return [dict(reading_kind=event['reading_kind'],reading_id=event['reading_id'],
                 source_id=entry['source_id'],file_page='1',region=entry['region'],scope=entry['scope'],
                 status=entry['status'],checked_at=event['plan']['checked_at'],
                 notes=encode({'review_id':event['plan']['review_id'],'reviewer':event['plan']['reviewer'],
                               'bundle_sha256':event['bundle_sha256'],'notes':entry['notes']}).strip())
            for entry in event['ranges']]


def validate_review_csv(root, book):
    if not book.get('reading_reviews'):
        return
    path=contained(root,f"books/{book['book_id']}/review.csv")
    reader=csv.DictReader(io.StringIO(checked_file(path).read_bytes().decode('utf-8')))
    if reader.fieldnames!=REVIEW_FIELDS:
        raise RegistrationError('review.csvの列が一致しません。')
    rows=list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in rows):
        raise RegistrationError('review.csvの列数が一致しません。')
    for record in book['reading_reviews']:
        expected=event_rows(load_review(root,record['path']))
        for row in expected:
            if rows.count(row)!=1:
                raise RegistrationError('review.csvと版別の照合記録が一致しません。')
    # 既存の自由記入行は保持するが、生成済み記録を装う余分な行を許可しない。
    by_id={r['review_id']:event_rows(load_review(root,r['path'])) for r in book['reading_reviews']}
    for row in rows:
        try:notes=json.loads(row['notes'])
        except (ValueError,TypeError):continue
        if isinstance(notes,dict) and 'review_id' in notes:
            identifier=notes['review_id']
            if not isinstance(identifier,str) or identifier not in by_id or row not in by_id[identifier]:
                raise RegistrationError('review.csvに登録済み記録と一致しない照合行があります。')

def state_entries(root, identifier, baseline, action):
    before_book,before_catalog,before_reviews=baseline
    book=json.loads(before_book)
    validate_book(root,book,identifier)
    output_reviews=before_reviews
    if action['kind']=='review':
        event=load_review(root,action['path'])
        if event['book_id']!=identifier:
            raise RegistrationError('別書籍の照合記録は追加できません。')
        record={'review_id':event['plan']['review_id'],'path':action['path'],
                'sha256':hash_bytes(encode(event).encode()),'bundle_path':event['bundle_path'],
                'bundle_sha256':event['bundle_sha256']}
        entries=book.setdefault('reading_reviews',[])
        previous=next((r for r in entries if r['review_id']==record['review_id']),None)
        if previous is not None and previous!=record:
            raise RegistrationError('既存の照合記録IDを変更できません。')
        if previous is None:
            entries.append(record)
            reader=csv.DictReader(io.StringIO(before_reviews))
            if reader.fieldnames!=REVIEW_FIELDS:
                raise RegistrationError('review.csvの列が一致しません。')
            rows=list(reader)
            if any(None in row or any(v is None for v in row.values()) for row in rows):
                raise RegistrationError('review.csvの列数が一致しません。')
            rows.extend(event_rows(event))
            stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=REVIEW_FIELDS,lineterminator='\n')
            writer.writeheader();writer.writerows(rows);output_reviews=stream.getvalue()
    elif action['kind']=='adopt':
        record=bundle_record(root,action['bundle_path'])
        if record not in book.get('reading_bundles',[]):
            raise RegistrationError('登録済みの読書版だけを採用できます。')
        if record['format_status']!='complete':
            raise RegistrationError('整形の未解決箇所がある読書版は採用できません。')
        book['active_reading']={'kind':record['kind'],'id':record['run_id'] if record['kind']=='ocr' else record['reading_id'],
                                'reading_id':record['reading_id'],'path':record['markdown_path'],
                                'bundle_path':record['bundle_path'],'bundle_sha256':record['bundle_sha256']}
    else:
        raise RegistrationError('未知の読書版更新です。')
    validate_book(root,book,identifier)
    rows=list(csv.DictReader(io.StringIO(before_catalog)))
    rows=[r for r in rows if r['book_id']!=identifier]+[row_for(book)]
    return [{'path':path,'text':text,'sha256':hash_bytes(text.encode()),'before':before}
            for path,text,before in ((f'books/{identifier}/book.json',encode(book),before_book),
                                     ('catalog.csv',catalog_text(rows),before_catalog),
                                     (f'books/{identifier}/review.csv',output_reviews,before_reviews))]


def recover_state(root, identifier):
    journal=contained(root,f'books/{identifier}/{JOURNAL}')
    if not journal.exists():return
    transaction=read_json(journal)
    entries=state_entries(root,identifier,transaction['baseline'],transaction['action'])
    if transaction.get('schema_version')!='signal-library-reading-state-1' or transaction['files']!=entries:
        raise RegistrationError('読書版更新のjournalが予定内容と一致しません。')
    for entry in entries:publish(root,entry)
    inventory(root)
    if read_json(journal)!=transaction:
        raise RegistrationError('読書版更新のjournalが変更されました。')
    journal.unlink()


def update_state(root, identifier, action):
    recover_state(root,identifier)
    baseline=[checked_file(contained(root,path)).read_bytes().decode('utf-8') for path in
              (f'books/{identifier}/book.json','catalog.csv',f'books/{identifier}/review.csv')]
    entries=state_entries(root,identifier,baseline,action)
    if all(e['text']==e['before'] for e in entries):return
    transaction={'schema_version':'signal-library-reading-state-1','baseline':baseline,'action':action,'files':entries}
    write_new(contained(root,f'books/{identifier}/{JOURNAL}'),encode(transaction))
    recover_state(root,identifier)


def change(value, bundle_path, plan_path=None, apply=False, adopt=False):
    root=library_root(value)
    record=bundle_record(root,bundle_path)
    identifier=read_json(contained(root,bundle_path))['identity']['book_id']
    with locked(root) if apply else contextlib.nullcontext():
        if apply:
            recover_index(root,identifier);recover_state(root,identifier)
        elif any((root/f'books/{identifier}/{name}').exists() for name in (JOURNAL,'.reading-index.json')):
            raise RegistrationError('読書版更新が中断しています。同じ操作を--applyで再開してください。')
        book=inventory(root)[0][identifier]
        if record not in book.get('reading_bundles',[]):
            raise RegistrationError('登録済み読書版を指定してください。')
        if adopt:
            action={'kind':'adopt','bundle_path':bundle_path}
            if record['format_status']!='complete':
                raise RegistrationError('整形が完了した読書版を指定してください。')
            result={'state':'reading_adopted' if apply else 'reading_adoption_planned'}
        else:
            event=review_record(root,bundle_path,read_json(plan_path))
            path=f"books/{identifier}/reviews/{event['plan']['review_id']}.json"
            action={'kind':'review','path':path}
            if apply:
                target=contained(root,path)
                data=encode(event).encode()
                if target.exists():
                    if checked_file(target).read_bytes()!=data:
                        raise RegistrationError('同じ照合記録IDに別内容は保存できません。')
                else:write_bytes_atomic(target,data,exclusive=True)
            result={'state':'reading_review_recorded' if apply else 'reading_review_planned','review_path':path,
                    'ranges':event['ranges']}
        if apply:update_state(root,identifier,action)
        result['review_status']='range_records' if any(r['bundle_path']==bundle_path for r in book.get('reading_reviews',[])) or not adopt else 'unreviewed'
        return {'status':'ok',**result,'book_id':identifier,'bundle_path':bundle_path,
                'files_written':apply,'network_used':False,
                'next_actions':['照合の状態は範囲ごとの記録です。未記録の範囲や別版へ引き継ぎません。']}

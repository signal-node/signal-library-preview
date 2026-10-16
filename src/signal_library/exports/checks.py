"""対象HTMLのhashを固定した表示確認。ブラウザでの確認を自動生成しない。"""

import contextlib
import re
from datetime import datetime

from ..ocr.files import write_bytes_atomic
from ..registration import (RegistrationError, checked_file, contained, digest, encode,
                            inventory, library_root, locked, read_json)
from .library import output_record, recover, index_check

PDF_CHECKS = {'japanese','images','math','tables','footnotes','toc','offline','copied_bundle',
              'page_layout','page_numbers','text_selection','font_embedding'}
CHECKS = {'japanese','images','math','tables','footnotes','toc','offline','copied_bundle','narrow_layout'}


def event(root, manifest_path, plan):
    output=output_record(root,manifest_path)
    pdf=output['format']=='pdf'
    device='viewer' if pdf else 'browser'
    schema='signal-library-pdf-check-1' if pdf else 'signal-library-export-check-1'
    required={'schema_version','check_id','checked_at','reviewer','method',device,'results','notes'}
    if not isinstance(plan,dict) or set(plan)!=required or plan['schema_version']!=schema:
        raise RegistrationError('表示確認計画の形式が不正です。')
    if not isinstance(plan['check_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',plan['check_id']):
        raise RegistrationError('表示確認のIDが不正です。')
    for key in ('reviewer','notes'):
        if not isinstance(plan[key],str) or not plan[key].strip():raise RegistrationError('表示確認者と記録理由を指定してください。')
    stamp=datetime.fromisoformat(plan['checked_at'])
    if stamp.tzinfo is None or stamp.utcoffset() is None:raise RegistrationError('表示確認日時にはタイムゾーンが必要です。')
    methods={'pdf_manual','pdf_page_images','user_report'} if pdf else {'browser_manual','browser_automated','user_report'}
    if plan['method'] not in methods:
        raise RegistrationError('表示確認の方法が不正です。')
    browser=plan[device]
    if not isinstance(browser,dict) or set(browser)!={'name','version','os'} or any(not isinstance(v,str) or not v.strip() for v in browser.values()):
        raise RegistrationError('表示確認に使用したbrowserとOSを記録してください。不明なversionはunknownと記録できます。')
    results=plan['results']
    if not isinstance(results,dict) or set(results)!=(PDF_CHECKS if pdf else CHECKS) or any(v not in {'passed','failed','not_checked'} for v in results.values()):
        raise RegistrationError('表示確認の各項目をpassed/failed/not_checkedで記録してください。')
    state='passed' if all(v=='passed' for v in results.values()) else ('needs_review' if 'failed' in results.values() else 'partial')
    value=read_json(contained(root,manifest_path))
    return {'schema_version':'signal-library-export-check-record-1','plan':plan,'status':state,
            'book_id':value['book_id'],'export_id':output['export_id'],'manifest_path':manifest_path,
            'manifest_sha256':output['manifest_sha256'],'output_sha256':output['output_sha256']}


def load(root,path):
    value=read_json(contained(root,path))
    if value!=event(root,value['manifest_path'],value['plan']) or path!=f"books/{value['book_id']}/export-checks/{value['plan']['check_id']}.json":
        raise RegistrationError('表示確認記録と対象出力・保存先が一致しません。')
    return value


def record(root,path):
    value=load(root,path)
    return {'check_id':value['plan']['check_id'],'path':path,'sha256':digest(contained(root,path)),
            'export_id':value['export_id'],'manifest_path':value['manifest_path'],
            'manifest_sha256':value['manifest_sha256'],'status':value['status']}


def review(value,manifest_path,plan_path,apply=False):
    root=library_root(value)
    planned=event(root,manifest_path,read_json(plan_path))
    identifier=planned['book_id']
    with locked(root) if apply else contextlib.nullcontext():
        if apply:recover(root,identifier)
        book=inventory(root)[0][identifier]
        output=output_record(root,manifest_path)
        if output not in book.get('local_exports',[]):raise RegistrationError('登録済みの派生出力を指定してください。')
        path=f"books/{identifier}/export-checks/{planned['plan']['check_id']}.json"
        if apply:
            target=contained(root,path);data=encode(planned).encode()
            if target.exists():
                if checked_file(target).read_bytes()!=data:raise RegistrationError('同じ表示確認IDへ別内容は保存できません。')
            else:write_bytes_atomic(target,data,exclusive=True)
            index_check(root,identifier,path)
        return {'status':'ok','state':'export_check_recorded' if apply else 'export_check_planned',
                'book_id':identifier,'check_path':path,'visual_status':planned['status'],
                'files_written':apply,'network_used':False,
                'next_actions':['未確認の項目は未確認のまま残します。別の出力へ確認結果を引き継ぎません。']}


def status_for(root,book,output):
    checks=[load(root,c['path']) for c in book.get('export_checks',[]) if c['manifest_path']==output['manifest_path']]
    latest=max(enumerate(checks),key=lambda pair:(datetime.fromisoformat(pair[1]['plan']['checked_at']),pair[0]))[1] if checks else None
    return {'visual_status':latest['status'] if latest else 'pending',
            'latest_check_id':latest['plan']['check_id'] if latest else None,
            'check_method':latest['plan']['method'] if latest else None}


def inspect(value, identifier, export_id=None, bundle_path=None, markdown_id=None):
    root=library_root(value)
    books,_=inventory(root)
    if identifier not in books:raise RegistrationError('登録済み書籍を指定してください。')
    book=books[identifier]
    records=[r for r in book.get('local_exports',[]) if export_id is None or r['export_id']==export_id]
    if export_id is not None and not records:raise RegistrationError('登録済みexport_idを指定してください。')
    outputs=[]
    for output in records:
        from .lifecycle import comparison
        outputs.append({**output,**status_for(root,book,output),**comparison(root,book,output,bundle_path,markdown_id)})
    return {'status':'ok','state':'export_status','exports':outputs,'count':len(outputs),
            'files_written':False,'network_used':False,'next_actions':[]}

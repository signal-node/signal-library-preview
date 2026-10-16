"""コピー復元した管理runの絶対パスを明示的に再接続する。APIは呼ばない。"""

import contextlib
import copy
import json
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..initialization import write_new
from ..registration import (RegistrationError, checked_file, contained, digest, encode, hash_bytes,
                            library_root, locked, read_json)
from .files import write_bytes_atomic

JOURNAL='.path-rebind.json'


def old_root(path,value,root):
    text=value.get('run_dir')
    if not isinstance(text,str):raise RegistrationError('OCRの旧保存先が不正です。')
    kind=PureWindowsPath if '\\' in text or PureWindowsPath(text).drive else PurePosixPath
    run=kind(text);suffix=path.parent.relative_to(root).as_posix()
    if not run.is_absolute() or len(run.parts)<=len(PurePosixPath(suffix).parts):
        raise RegistrationError('OCRの旧保存先は絶対パスで記録してください。')
    prefix=run.parents[len(PurePosixPath(suffix).parts)-1]
    if prefix/kind(suffix)!=run:raise RegistrationError('旧runの書籍内パスが現在の台帳と一致しません。')
    return prefix,kind


def check_paths(root,path,manifest,plan,historical=False):
    from .library import options_for
    expected=options_for(root,plan).input_path
    if historical:
        prefix,kind=old_root(path,manifest,root)
        original=prefix/kind(expected.relative_to(root).as_posix())
        if kind(manifest.get('input_root',''))!=original or ('input_path' in manifest and kind(manifest['input_path'])!=original):
            raise RegistrationError('旧manifestの入出力が同じ保存ルート・captureに対応しません。')
    elif manifest.get('run_dir')!=str(path.parent) or manifest.get('input_root')!=str(expected) or ('input_path' in manifest and manifest['input_path']!=str(expected)):
        raise RegistrationError('OCRの保存先が現在のrootと一致しません。コピー復元後はocr rebindの計画を確認してください。')


def destination(root,path,plan,manifest):
    from .library import options_for
    result={'run_dir':str(path.parent),'input_root':str(options_for(root,plan).input_path)}
    if 'input_path' in manifest:result['input_path']=result['input_root']
    return result


def validate_history(root,path,manifest,plan):
    refs=manifest.get('path_bindings',[])
    if not isinstance(refs,list):raise RegistrationError('OCR保存先の履歴配列が不正です。')
    seen=set();previous_destination=None
    for index,ref in enumerate(refs):
        if not isinstance(ref,dict) or set(ref)!={'path','sha256'} or not isinstance(ref['path'],str) or not isinstance(ref['sha256'],str) or ref['path'] in seen:
            raise RegistrationError('OCR保存先の履歴参照が不正です。')
        seen.add(ref['path']);record_path=contained(path.parent,ref['path']);record=read_json(record_path)
        fingerprint=hash_bytes(encode(record).encode());folder=record_path.parent
        if ref['path']!=f'path-bindings/{fingerprint}/binding.json' or digest(record_path)!=ref['sha256']:
            raise RegistrationError('OCR保存先の履歴hash/パスが一致しません。')
        before=read_json(folder/'manifest.before.json');operation=read_json(folder/'operation.before.json')
        if (set(record)!={'schema_version','library_id','manifest_path','run_id','plan_hash','manifest_sha256','operation_sha256','source_root','destination'} or
                set(record.get('destination',{}))!={'run_dir','input_root',*(['input_path'] if 'input_path' in before else [])} or
                record.get('schema_version')!='signal-library-path-binding-1' or
                record.get('library_id')!=read_json(root/'library.json')['library_id'] or
                record.get('manifest_path')!=path.relative_to(root).as_posix() or
                record.get('run_id')!=manifest['run_id'] or record.get('plan_hash')!=hash_bytes(encode(plan).encode()) or
                record.get('manifest_sha256')!=digest(folder/'manifest.before.json') or
                record.get('operation_sha256')!=digest(folder/'operation.before.json') or
                operation.get('plan')!=plan or operation.get('plan_hash')!=record['plan_hash'] or
                before.get('run_id')!=manifest['run_id'] or before.get('model_requested')!=manifest['model_requested'] or
                before.get('path_bindings',[])!=refs[:index]):
            raise RegistrationError('OCR保存先の履歴と原版/操作が一致しません。')
        check_paths(root,path,before,plan,True)
        if previous_destination and any(before.get(k)!=v for k,v in previous_destination.items()):
            raise RegistrationError('OCR保存先の履歴が直前の再接続先から継続していません。')
        projected=copy.deepcopy(before);projected.update(record['destination'])
        check_paths(root,path,projected,plan,True)
        previous_destination=record['destination']
        if str(old_root(path,before,root)[0])!=record.get('source_root'):
            raise RegistrationError('OCR保存先履歴の旧rootが一致しません。')
        if index==len(refs)-1 and any(manifest.get(k)!=v for k,v in record['destination'].items()):
            raise RegistrationError('OCRの現在の保存先と最新の再接続履歴が一致しません。')


def transaction(root,path,manifest,operation,before,operation_before):
    plan=operation['plan'];prefix,_=old_root(path,manifest,root)
    record={'schema_version':'signal-library-path-binding-1','library_id':read_json(root/'library.json')['library_id'],
            'manifest_path':path.relative_to(root).as_posix(),'run_id':manifest['run_id'],
            'plan_hash':operation['plan_hash'],'manifest_sha256':hash_bytes(before),
            'operation_sha256':hash_bytes(operation_before),'source_root':str(prefix),
            'destination':destination(root,path,plan,manifest)}
    fingerprint=hash_bytes(encode(record).encode());folder=f'path-bindings/{fingerprint}'
    after=copy.deepcopy(manifest);after.update(record['destination'])
    after.setdefault('path_bindings',[]).append({'path':folder+'/binding.json','sha256':hash_bytes(encode(record).encode())})
    return {'schema_version':'signal-library-path-rebind-1','record':record,'folder':folder,
            'before':before.decode('utf-8'),'operation_before':operation_before.decode('utf-8'),'after':encode(after)}


def rebind(value,manifest_relative,apply=False):
    from . import library
    root=library_root(value)
    with locked(root) if apply else contextlib.nullcontext():
        path=contained(root,manifest_relative);journal=path.parent/JOURNAL
        if journal.exists():
            if not apply:raise RegistrationError('保存先再接続の途中です。同じocr rebind --applyで再開してください。')
            tx=read_json(journal)
            before=tx['before'].encode('utf-8');operation_before=tx['operation_before'].encode('utf-8')
            original=json.loads(tx['before']);operation=json.loads(tx['operation_before'])
            # journalの予定内容を原版から再生成し、任意の書換えを許可しない。
            if tx!=transaction(root,path,original,operation,before,operation_before):
                raise RegistrationError('保存先再接続journalが原版からの予定内容と一致しません。')
            actual=checked_file(path).read_bytes()
            if actual not in (before,tx['after'].encode('utf-8')):
                raise RegistrationError('保存先再接続中にmanifestが変更されました。')
            # before版で管理契約を検証する。after版の場合は履歴検査も通す。
            library._load_managed(value,manifest_relative,True,True)
            if checked_file(path.parent.parent/'operation.json').read_bytes()!=operation_before:
                raise RegistrationError('保存先再接続中にOCR操作が変更されました。')
        else:
            _,path,manifest,operation=library._load_managed(value,manifest_relative,apply,True)
            if all(manifest.get(k)==v for k,v in destination(root,path,operation['plan'],manifest).items()):
                return {'status':'ok','state':'already_bound','manifest_path':manifest_relative,'files_written':False,'network_used':False,'next_actions':[]}
            before=checked_file(path).read_bytes();operation_before=checked_file(path.parent.parent/'operation.json').read_bytes()
            tx=transaction(root,path,manifest,operation,before,operation_before)
            if apply:write_new(journal,encode(tx))
        if apply:
            folder=contained(path.parent,tx['folder']);folder.mkdir(parents=True,exist_ok=True)
            files={'manifest.before.json':tx['before'].encode('utf-8'),'operation.before.json':tx['operation_before'].encode('utf-8'),
                   'binding.json':encode(tx['record']).encode('utf-8')}
            if {p.name for p in folder.iterdir()}-set(files):raise RegistrationError('再接続履歴に未知のファイルがあります。')
            for name,data in files.items():
                target=folder/name
                if target.exists():
                    if checked_file(target).read_bytes()!=data:raise RegistrationError('再接続履歴の原版が一致しません。')
                else:write_new(target,data.decode('utf-8'))
            current=checked_file(path).read_bytes()
            if current==tx['before'].encode('utf-8'):write_bytes_atomic(path,tx['after'].encode('utf-8'))
            elif current!=tx['after'].encode('utf-8'):raise RegistrationError('保存先再接続中にmanifestが変更されました。')
            # journalの存在中は通常入口を止めるため、検証時だけhistorical入口を使用する。
            library._load_managed(value,manifest_relative,True,True)
            if read_json(journal)!=tx:raise RegistrationError('保存先再接続journalが変更されました。')
            journal.unlink()
        return {'status':'ok','state':'rebound' if apply else 'rebind_planned','manifest_path':manifest_relative,
                'run_id':tx['record']['run_id'],'source_root':tx['record']['source_root'],
                'destination':tx['record']['destination'],'history_path':path.parent.relative_to(root).as_posix()+'/'+tx['folder'],
                'files_written':apply,'network_used':False,'next_actions':['既存jobの状態を確認し、再submitせず同じmanifestから続けてください。']}

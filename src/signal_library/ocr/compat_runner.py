"""旧Python CLIから共通OCRを呼ぶための標準入出力。任意import/evalはしない。"""
import importlib
import json
import sys
from dataclasses import is_dataclass, asdict
from pathlib import Path
from types import SimpleNamespace

ALLOWED = {
    'domain': {'sha256_file','collect_input_files','discover_documents','make_custom_id','estimate_batch_cost','build_batch_entry','response_body','markdown_from_ocr_body','safe_asset_name','decode_image_base64','without_image_base64'},
    'workflow': {'submit_ocr','run_ocr','status_ocr','collect_ocr','cancel_ocr','retry_ocr'},
    'manifest': {'utc_now','write_json_atomic','write_text_atomic','write_jsonl','read_jsonl','read_manifest','persist_manifest'},
    'gateway': {'api_key_from_env','batch_snapshot','safe_error','definite_rejection'},
    'results': {'validate_run','merge_runs'},
}


def encode(value):
    if isinstance(value, Path):return {'$path':str(value)}
    if isinstance(value, bytes):
        import base64
        return {'$bytes':base64.b64encode(value).decode('ascii')}
    if is_dataclass(value):return {'$namespace':{k:encode(v) for k,v in vars(value).items()}}
    if isinstance(value, SimpleNamespace):return {'$namespace':{k:encode(v) for k,v in vars(value).items()}}
    if isinstance(value, tuple):return {'$tuple':[encode(v) for v in value]}
    if isinstance(value, set):return {'$set':[encode(v) for v in value]}
    if isinstance(value, list):return [encode(v) for v in value]
    if isinstance(value, dict):return {'$mapping':[[encode(k),encode(v)] for k,v in value.items()]}
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise ValueError('unsupported compatibility value')


def decode(value):
    if isinstance(value,list):return [decode(v) for v in value]
    if isinstance(value,dict):
        if set(value)=={'$mapping'}:return {decode(k):decode(v) for k,v in value['$mapping']}
        if set(value)=={'$path'}:return Path(value['$path'])
        if set(value)=={'$bytes'}:
            import base64
            return base64.b64decode(value['$bytes'],validate=True)
        if set(value)=={'$namespace'}:return SimpleNamespace(**{k:decode(v) for k,v in value['$namespace'].items()})
        if set(value)=={'$tuple'}:return tuple(decode(v) for v in value['$tuple'])
        if set(value)=={'$set'}:return set(decode(v) for v in value['$set'])
        return {k:decode(v) for k,v in value.items()}
    return value


def main():
    from .errors import OcrError
    try:
        request=json.load(sys.stdin)
        component,function=request['component'],request['function']
        if function not in ALLOWED.get(component,set()) or (component=='gateway' and function=='api_key_from_env'):
            raise ValueError('unsupported compatibility operation')
        module=importlib.import_module('signal_library.ocr.'+component)
        result=getattr(module,function)(*decode(request['args']),**decode(request['kwargs']))
        json.dump({'ok':True,'result':encode(result)},sys.stdout,ensure_ascii=True)
    except OcrError as exc:
        json.dump({'ok':False,'error':str(exc)},sys.stdout,ensure_ascii=True)
        return 1
    except Exception:
        json.dump({'ok':False,'error':'OCR互換処理が失敗しました。入力・設定・manifestを確認してください。'},sys.stdout,ensure_ascii=True)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())

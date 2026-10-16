"""OCR実装の正本はSignal Library。OCR以外は追加導入なしで使える。"""
import importlib
import sys
import os
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from signal_stack.exceptions import SignalStackError


def module(name):
    if sys.version_info < (3, 12):
        raise SignalStackError("OCR互換入口にはPython 3.12以上とSignal Libraryが必要です。")
    try:
        return importlib.import_module("signal_library.ocr." + name)
    except ImportError:
        raise SignalStackError("Signal Libraryが未導入です。このPython環境へSignal Libraryを導入してください。OCRは送信していません。") from None


def call(component, function, *args, **kwargs):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if component == "workflow" and function in {"submit_ocr", "run_ocr"}:
        args = (SimpleNamespace(**vars(args[0]), legacy_inputs=True), *args[1:])
    elif component == "workflow" and function == "retry_ocr":
        kwargs["legacy_inputs"] = True
    elif component == "domain" and function in {"discover_documents", "collect_input_files"}:
        kwargs["legacy"] = True
    if sys.version_info < (3, 12):
        return remote_call(component, function, args, kwargs)
    target = module(component)
    error = module("errors").OcrError
    try:
        return getattr(target, function)(*args, **kwargs)
    except error as exc:
        raise SignalStackError(str(exc)) from None


from dataclasses import is_dataclass
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



def remote_call(component,function,args,kwargs):
    repository=Path(__file__).resolve().parents[2]
    interpreter=repository/'.venv-ocr'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not interpreter.is_file():
        raise SignalStackError("OCR専用Python 3.12環境 .venv-ocr がありません。Signal Libraryを導入してください。送信していません。")
    payload=json.dumps({'component':component,'function':function,'args':encode(args),'kwargs':encode(kwargs)},ensure_ascii=True)
    result=subprocess.run([str(interpreter),'-m','signal_library.ocr.compat_runner'],input=payload,capture_output=True,text=True,encoding='utf-8')
    try:
        response=json.loads(result.stdout)
    except (ValueError,TypeError):
        raise SignalStackError("OCR専用環境を確認してください。出力内容は表示しません。") from None
    if not response.get('ok'):
        raise SignalStackError(response.get('error','OCR互換処理が失敗しました。'))
    return decode(response['result'])

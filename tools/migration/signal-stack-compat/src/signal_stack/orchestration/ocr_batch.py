"""Signal Libraryの共通OCR実装への互換入口。"""
from signal_stack._ocr_compat import call, module


def submit_ocr(*args, **kwargs):
    return call('workflow', 'submit_ocr', *args, **kwargs)


def run_ocr(*args, **kwargs):
    return call('workflow', 'run_ocr', *args, **kwargs)


def status_ocr(*args, **kwargs):
    return call('workflow', 'status_ocr', *args, **kwargs)


def collect_ocr(*args, **kwargs):
    return call('workflow', 'collect_ocr', *args, **kwargs)


def cancel_ocr(*args, **kwargs):
    return call('workflow', 'cancel_ocr', *args, **kwargs)


def retry_ocr(*args, **kwargs):
    return call('workflow', 'retry_ocr', *args, **kwargs)


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(module('workflow'), name)

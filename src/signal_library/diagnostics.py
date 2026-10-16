"""外部通信と書込みを行わない環境診断。"""

import importlib.util
import json
import os
import shutil
import sys
import subprocess
from pathlib import Path
from .storage import LIBRARY_SCHEMA, UnknownLibrarySchema, is_redirect, read_record, valid_catalog

def inspect_root(root: Path) -> str:
    if is_redirect(root):
        return "unsafe_root"
    if not root.exists():
        return "missing"
    if not root.is_dir():
        return "not_directory"
    marker = root / "library.json"
    if is_redirect(marker):
        return "unsafe_marker"
    if marker.exists():
        try:
            record = read_record(marker)
        except UnknownLibrarySchema:
            return "unknown_schema"
        except (OSError, ValueError):
            return "invalid_marker"
        for name in ("inbox", "books"):
            entry = root / name
            if is_redirect(entry) or not entry.is_dir():
                return "incomplete"
        catalog = root / "catalog.csv"
        if is_redirect(catalog) or not catalog.is_file():
            return "incomplete"
        if not valid_catalog(catalog):
            return "invalid_catalog"
        journal = root / ".signal-library-init.json"
        if journal.exists() or is_redirect(journal):
            return "initializing"
        lock = root / ".signal-library.lock"
        if lock.exists() or is_redirect(lock):
            return "locked"
        registration = root / ".signal-library-register.json"
        if registration.exists() or is_redirect(registration):
            return "registering"
        return "ready"
    journal = root / ".signal-library-init.json"
    if journal.exists() or is_redirect(journal):
        return "initializing"
    lock = root / ".signal-library.lock"
    if lock.exists() or is_redirect(lock):
        return "locked"
    if (root / "books").is_dir() and not is_redirect(root / "books") and (root / "catalog.csv").exists():
        return "legacy_unmanaged" if valid_catalog(root / "catalog.csv") else "invalid_catalog"
    if any(root.iterdir()):
        return "nonempty_unmanaged"
    return "empty"


def engine_status(tool, versions):
    executable=shutil.which(tool)
    if not executable:return 'optional_missing','PATH上にありません。出力時だけ導入してください。'
    try:
        result=subprocess.run([executable,'--version'],capture_output=True,text=True,encoding='utf-8',timeout=5,check=False)
        first=result.stdout.splitlines()[0].split() if result.stdout else []
        if result.returncode or result.stderr.strip() or first[:1]!=[tool] or len(first)<2:
            return 'optional_incompatible','版を確認できませんでした。対応する公式CLIを確認してください。'
        if first[1] not in versions:
            return 'optional_incompatible','対応版: '+', '.join(sorted(versions))+'。PATH上のエンジンを切り替えてください。'
        return 'ok','対応版 '+first[1]+'。'
    except (OSError,subprocess.SubprocessError,UnicodeError):
        return 'optional_incompatible','版を確認できませんでした。対応する公式CLIを確認してください。'


def doctor(root_value: str | None, environ: dict[str, str]) -> dict:
    checks = []
    state = "unconfigured"

    def add(name: str, status: str, detail: str, required: bool = False):
        checks.append(dict(name=name, status=status, detail=detail, required=required))

    add("python", "ok" if sys.version_info >= (3, 12) else "error",
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", True)
    if not root_value:
        add("library_root", "action_required", "LIBRARY_ROOTが未設定です。", True)
    else:
        root = Path(root_value).expanduser()
        if not root.is_absolute():
            add("library_root", "error", "保存先には絶対パスを指定してください。", True)
        else:
            try:
                state = inspect_root(root)
            except (OSError, ValueError):
                state = "unreadable"
            add("library_root", "ok" if state == "ready" else "action_required", state, True)
    # 設定の有無だけを返す。環境変数・例外の内容は表示しない。
    present = bool(environ.get("MISTRAL_API_KEY", "").strip()
                   or environ.get("SIGNAL_STACK_MISTRAL_API_KEY", "").strip())
    add("mistral_key", "ok" if present else "optional_missing",
        "設定済み（認証未確認）" if present else "未設定。ローカル操作には不要です。")
    installed = importlib.util.find_spec("mistralai") is not None
    add("mistral_sdk", "ok" if installed else "optional_missing",
        "導入済み（認証・API互換性は未確認）" if installed else "未導入。OCR実行時だけocr extraを導入してください。")
    for tool in ("mise",):
        add(tool, "ok" if shutil.which(tool) else "optional_missing",
            "PATH上にあります。" if shutil.which(tool) else "PATH上にありません。")
    from .exports.engine import PANDOC_VERSIONS
    from .exports.typst import VERSION
    for tool,versions in (('pandoc',PANDOC_VERSIONS),('typst',{VERSION})):
        tool_status,detail=engine_status(tool,versions)
        add(tool,tool_status,detail)
    status = "ok" if all(c["status"] == "ok" for c in checks if c["required"]) else "action_required"
    return {"status": status, "checks": checks,
            "next_actions": [] if status == "ok" else [{
                "legacy_unmanaged": "既存資料を保持し、validateで旧台帳を検査してください。採用にはadoptを使います。",
                "registering": "登録中断記録を確認し、同じ計画でregister --resumeを実行してください。",
                "initializing": "初期化中断記録を確認し、init --resumeの手順へ進んでください。",
                "locked": "実行中の処理とロックの所有を確認してください。ロックを自動削除しないでください。",
                "missing": "保存先を確認し、initで計画を検査してください。",
                "empty": "initで初期化の計画を検査してください。",
                "unconfigured": "LIBRARY_ROOTに保存先の絶対パスを設定してください。",
            }.get(state, "既存資料を保持し、保存先・台帳・形式を確認してください。初期化で修復しないでください。")],
            "network_used": False, "files_written": False}

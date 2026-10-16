"""引数解釈と共通結果の表示。"""

import csv
import json
import os
import sys
from pathlib import Path

from . import __version__
from .presentation import JapaneseArgumentParser, label
from .diagnostics import doctor
from .initialization import InitializationError, initialize
from .credentials import CredentialError, configure_mistral
from .registration import RegistrationError, register, list_books, adopt
from .ocr.errors import OcrError
import subprocess


def repository_roots(cwd=None,module_path=None):
    """source入口とwheel入口の両方で、コードと実行中Gitを保護する。"""
    module=Path(module_path or __file__).resolve()
    source=module.parents[2]
    roots=[source if (source/'pyproject.toml').is_file() and (source/'src/signal_library').is_dir() else module.parent]
    current=Path(cwd or Path.cwd()).resolve()
    for candidate in (current,*current.parents):
        if (candidate/'.git').exists() or ((candidate/'pyproject.toml').is_file() and (candidate/'src/signal_library').is_dir()):
            roots.append(candidate);break
    return list(dict.fromkeys(roots))


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = JapaneseArgumentParser(prog="signal-library", description="Signal Libraryのローカル管理")
    parser.add_argument("--version", action="version", version=__version__, help="バージョンを表示して終了")
    commands = parser.add_subparsers(dest="command", required=True)
    diag = commands.add_parser("doctor", help="書込み・外部通信なしで環境を診断")
    diag.add_argument("--root", help="診断対象の絶対パス（省略時はLIBRARY_ROOT）")
    diag.add_argument("--json", action="store_true", help="Agent向けの構造化結果")
    init = commands.add_parser("init", help="空のライブラリ保存先を検査・初期化")
    init.add_argument("--root", help="保存先の絶対パス（省略時はLIBRARY_ROOT）")
    init.add_argument("--apply", action="store_true", help="検査だけでなく初期化を実行")
    init.add_argument("--resume", action="store_true", help="記録のある中断から再開")
    init.add_argument("--json", action="store_true")
    key = commands.add_parser("configure-key", help="利用者の端末でMistralキーを非表示入力して保存")
    key.add_argument("--replace", action="store_true", help="設定済みのキーを更新")
    key.add_argument("--json", action="store_true")
    reg = commands.add_parser("register", help="計画から書籍・画像・Markdownを登録")
    reg.add_argument("--plan", required=True, help="登録計画JSON")
    reg.add_argument("--apply", action="store_true")
    reg.add_argument("--resume", action="store_true")
    for name, command in (("register", reg), ("books", commands.add_parser("books", help="書籍一覧")),
                          ("validate", commands.add_parser("validate", help="台帳・参照・原本hashを検査")),
                          ("adopt", commands.add_parser("adopt", help="検査済み旧台帳に管理マーカーを追加"))):
        command.add_argument("--root")
        command.add_argument("--json", action="store_true")
        if name == "adopt":
            command.add_argument("--apply", action="store_true")
    ocr = commands.add_parser("ocr", help="登録済みcaptureのOCR計画・送信・回収")
    ocr_commands = ocr.add_subparsers(dest="ocr_command", required=True)
    for name in ("plan", "submit", "status", "collect", "recover", "retry", "merge", "validate", "import-run", "rebind"):
        command = ocr_commands.add_parser(name)
        command.add_argument("--root")
        command.add_argument("--json", action="store_true")
        if name in {"plan", "submit"}:
            command.add_argument("--book-id", required=True)
            command.add_argument("--capture-id", required=True)
            command.add_argument("--model", default="mistral-ocr-4-1")
            command.add_argument("--max-pages", type=int, required=True)
            command.add_argument("--max-cost-usd", type=float, required=True)
            command.add_argument("--no-images", action="store_true")
            if name == "submit":
                command.add_argument("--confirm-plan", required=True)
                command.add_argument("--allow-upload", action="store_true")
        elif name == "recover":
            command.add_argument("--operation", required=True, help="LIBRARY_ROOT相対のoperation.json")
        elif name == "merge":
            command.add_argument("--manifests", nargs="+", required=True)
            command.add_argument("--apply", action="store_true")
        elif name == "import-run":
            command.add_argument("--book-id", required=True)
            command.add_argument("--capture-id", required=True)
            command.add_argument("--source-manifest", required=True)
            command.add_argument("--apply", action="store_true")
        else:
            command.add_argument("--manifest", required=True, help="LIBRARY_ROOT相対のmanifestパス")
            if name == "rebind":
                command.add_argument("--apply", action="store_true")
            if name == "retry":
                command.add_argument("--max-pages", type=int, required=True)
                command.add_argument("--max-cost-usd", type=float, required=True)
                command.add_argument("--apply", action="store_true")
                command.add_argument("--confirm-plan")
                command.add_argument("--allow-upload", action="store_true")
            if name == "validate":
                command.add_argument("--allow-remote-files", action="store_true")
            if name == "collect":
                retention = command.add_mutually_exclusive_group()
                retention.add_argument("--keep-remote-files", dest="keep_remote_files", action="store_true", help="リモートファイルを削除せず結果を回収（既定）")
                retention.add_argument("--delete-remote-files", dest="keep_remote_files", action="store_false", help="回収後に今回のリモートファイルを削除。削除許可を得た場合だけ指定")
                command.set_defaults(keep_remote_files=True)
    reading = commands.add_parser('reading', help='統合本文・校正版を保存して管理')
    reading_commands = reading.add_subparsers(dest='reading_command', required=True)
    build = reading_commands.add_parser('build', help='登録済みOCRから新しい読書版を計画・保存')
    build.add_argument('--root')
    build.add_argument('--manifest', required=True)
    build.add_argument('--reading-id', required=True)
    build.add_argument('--kind', choices=('ocr','curated'), default='ocr')
    build.add_argument('--plan', help='原典確認に基づく整形計画JSON')
    build.add_argument('--supersedes', help='前版のLIBRARY_ROOT相対bundle.json。修正計画は全文に対する計画を指定')
    build.add_argument('--apply', action='store_true')
    build.add_argument('--json', action='store_true')
    for name in ('review','adopt'):
        command = reading_commands.add_parser(name, help='版を固定した照合記録' if name=='review' else '登録済み読書版を採用')
        command.add_argument('--root')
        command.add_argument('--bundle', required=True, help='LIBRARY_ROOT相対のbundle.json')
        if name=='review':
            command.add_argument('--plan', required=True, help='照合範囲・日時・照合者のJSON')
        command.add_argument('--apply', action='store_true')
        command.add_argument('--json', action='store_true')
    exports=commands.add_parser('export',help='登録済み本文のローカル派生出力')
    export_commands=exports.add_subparsers(dest='export_command',required=True)
    for output_format in ('html','pdf'):
        html_export=export_commands.add_parser(output_format)
        html_export.add_argument('--root')
        html_export.add_argument('--book-id',required=True)
        html_export.add_argument('--export-id',required=True)
        source=html_export.add_mutually_exclusive_group()
        source.add_argument('--bundle',help='LIBRARY_ROOT相対の登録済みbundle.json')
        source.add_argument('--markdown-id',help='登録済みMarkdown原本のsource_id')
        html_export.add_argument('--font-dir',help='固定Noto fontとOFLを置いたディレクトリ')
        html_export.add_argument('--apply',action='store_true')
        html_export.add_argument('--json',action='store_true')
    fonts=export_commands.add_parser('install-font',help='固定Noto font/OFLを取得またはローカル資材から導入')
    fonts.add_argument('--root')
    fonts.add_argument('--source-dir',help='取得済みfont/OFLのディレクトリ。指定時は通信しない')
    fonts.add_argument('--apply',action='store_true')
    fonts.add_argument('--json',action='store_true')
    export_check=export_commands.add_parser('check',help='実際の表示/ページ画像の確認結果を対象出力のhashへ固定して記録')
    export_check.add_argument('--root')
    export_check.add_argument('--manifest',required=True)
    export_check.add_argument('--plan',required=True)
    export_check.add_argument('--apply',action='store_true')
    export_check.add_argument('--json',action='store_true')
    export_status=export_commands.add_parser('status',help='生成時の状態と版に紐づく表示確認結果を表示')
    export_status.add_argument('--root')
    export_status.add_argument('--book-id',required=True)
    export_status.add_argument('--export-id')
    target=export_status.add_mutually_exclusive_group()
    target.add_argument('--bundle')
    target.add_argument('--markdown-id')
    export_status.add_argument('--json',action='store_true')
    regen=export_commands.add_parser('regenerate',help='旧出力を残し、現在の入力/設定から別IDで再生成')
    regen.add_argument('--root')
    regen.add_argument('--book-id',required=True)
    regen.add_argument('--from-export-id',required=True)
    regen.add_argument('--export-id',required=True)
    target=regen.add_mutually_exclusive_group()
    target.add_argument('--bundle')
    target.add_argument('--markdown-id')
    regen.add_argument('--font-dir')
    regen.add_argument('--apply',action='store_true')
    regen.add_argument('--json',action='store_true')
    args = parser.parse_args(argv)
    specified_root = getattr(args, "root", None)
    root = specified_root if specified_root is not None else os.environ.get("LIBRARY_ROOT")
    error_prefix = {"configure-key": "configuration", "init": "initialization", "doctor": "diagnostic", "ocr": "ocr", "reading":"reading", "export":"export"}.get(args.command, "registration")
    try:
        if args.command == "doctor":
            result = doctor(root, os.environ)
        elif args.command == "configure-key":
            result = configure_mistral(Path.cwd(), args.replace)
        elif args.command == "ocr":
            from .ocr import library as ocr_library
            if args.ocr_command in {"plan", "submit"}:
                kwargs = dict(model=args.model, max_pages=args.max_pages, max_cost_usd=args.max_cost_usd, include_images=not args.no_images)
                if args.ocr_command == "plan":
                    result = ocr_library.plan_ocr(root, args.book_id, args.capture_id, **kwargs)
                else:
                    result = ocr_library.submit(root, args.book_id, args.capture_id, args.confirm_plan, args.allow_upload, **kwargs)
            elif args.ocr_command == "recover":
                from .ocr.recovery import recover
                result = recover(root, args.operation)
            elif args.ocr_command == "retry":
                from .ocr.recovery import retry
                result = retry(root, args.manifest, args.max_pages, args.max_cost_usd, args.apply, args.confirm_plan, args.allow_upload)
            elif args.ocr_command == "merge":
                from .ocr.recovery import merge
                result = merge(root, args.manifests, args.apply)
            elif args.ocr_command == "import-run":
                from .ocr.recovery import import_run
                result = import_run(root, args.book_id, args.capture_id, args.source_manifest, args.apply)
            elif args.ocr_command == "rebind":
                from .ocr.bindings import rebind
                result = rebind(root,args.manifest,args.apply)
            elif args.ocr_command == "validate":
                from .ocr.results import validate_run
                _, path, _, _ = ocr_library.load_managed(root, args.manifest)
                report = validate_run(path.parent, args.allow_remote_files)
                result = {"status": "ok" if report["ok"] else "action_required", "state": "ocr_validated", "report": report,
                          "files_written": False, "network_used": False, "next_actions": []}
            else:
                result = ocr_library.follow(root, args.manifest, collect=args.ocr_command == "collect", keep_remote_files=getattr(args, "keep_remote_files", False))
        elif args.command == 'export':
            if args.export_command=='install-font':
                from .exports.fonts import install
                result=install(root,args.source_dir,args.apply)
            elif args.export_command=='check':
                from .exports.checks import review
                result=review(root,args.manifest,args.plan,args.apply)
            elif args.export_command=='status':
                from .exports.checks import inspect
                result=inspect(root,args.book_id,args.export_id,args.bundle,args.markdown_id)
            elif args.export_command=='regenerate':
                from .exports.lifecycle import regenerate
                result=regenerate(root,args.book_id,args.from_export_id,args.export_id,args.font_dir,args.bundle,args.markdown_id,args.apply)
            else:
                if args.export_command=='pdf':
                    from .exports.pdf import export_pdf as render
                else:
                    from .exports.html import export_html as render
                result=render(root,args.book_id,args.export_id,args.font_dir,args.bundle,args.markdown_id,args.apply)
        elif args.command == 'reading':
            if args.reading_command=='build':
                from .reading.library import build
                result = build(root,args.manifest,args.reading_id,args.kind,args.plan,args.apply,args.supersedes)
            else:
                from .reading.review import change
                result = change(root,args.bundle,getattr(args,'plan',None),args.apply,args.reading_command=='adopt')
        elif args.command == "register":
            result = register(root, args.plan, args.apply, args.resume)
        elif args.command in {"books", "validate"}:
            result = list_books(root, verify=args.command == "validate")
        elif args.command == "adopt":
            result = adopt(root, args.apply)
        else:
            result = initialize(root, args.apply, args.resume, repository_roots())
    except OcrError:
        result = {"status": "error", "files_written": None, "network_used": None, "error_code": "ocr_failed",
                  "next_actions": ["キー・OCR extra・保存先のOCR manifestを確認してください。送信結果不明時は再送せず既存jobを確認してください。"]}
    except (InitializationError, CredentialError, RegistrationError) as exc:
        result = {"status": "error", "files_written": None,
                  "next_actions": [str(exc)], "error_code": error_prefix + "_rejected"}
    except (OSError, ValueError, TypeError, KeyError, csv.Error, subprocess.SubprocessError):
        # ファイル名や入力文字列に秘密が含まれる場合も、例外をそのままログへ出さない。
        result = {"status": "error", "files_written": None,
                  "next_actions": ["保存先・形式・権限・ロック・中断記録を確認してください。"],
                  "error_code": error_prefix + "_failed"}
    result = {"result_schema": "signal-library-cli-1", "command": args.command,
              "version": __version__, **result}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result.get("checks", []):
            print(f"{label(check['name'])}: {label(check['status'])} — {label(check['detail'])}")
        if "report" in result:
            print(json.dumps(result["report"], ensure_ascii=False, indent=2))
        if "plan" in result:
            print(json.dumps(result["plan"], ensure_ascii=False, indent=2))
            print("計画の照合値（plan_hash）: " + result["plan_hash"])
        if "manifest_path" in result:
            print(result["manifest_path"])
        for export in result.get('exports',[]):
            print(f"{export['export_id']}: {export['format']} / 表示確認={label(export['visual_status'])} / 版の状態={label(export['freshness'])} / {export['output_path']}")
        for book in result.get("books", []):
            print(f"{book['book_id']}: {book['title']} ({book['edition']})")
        if "book_id" in result:
            print(result["book_id"])
        if "state" in result:
            print("処理状態: " + label(result["state"]))
        for action in result["next_actions"]:
            print(action)
    return 0 if result["status"] == "ok" else 1

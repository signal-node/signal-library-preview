"""許可リストから監査用ソース候補を作る。公開やライセンス採用はしない。"""

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
import zipfile

DIRECT = ['LICENSE','.github/workflows/check.yml','.github/workflows/i05-ocr-check.yml',
          '.github/workflows/i08-export-check.yml','.github/workflows/i09-export-check.yml',
          '.github/workflows/i11-package-check.yml','CLAUDE.md','mise.toml','mise.local.toml.example','.gitignore','pyproject.toml',
          'THIRD_PARTY_NOTICES.md','SUPPORT.md','docs/dependency-inventory.json','docs/setup.md','docs/agent-workflow.md',
          'docs/privacy.md','docs/updates.md','docs/release-status.md','docs/acceptance/agent-onboarding.md','docs/acceptance/agent-trial.md',
          '.agents/skills/signal-library/SKILL.md','.claude/skills/signal-library/SKILL.md',
          'tools/signal-library.py','tools/acceptance/prepare-agent-trial.py','tools/book-ocr/assemble-book-ocr.py','tools/book-ocr/test_assemble_book_ocr.py',
          'tools/migration/signal-stack-compat/src/signal_stack/_ocr_compat.py',
          'tools/migration/signal-stack-compat/src/signal_stack/orchestration/ocr_batch.py',
          'tools/engine-probe/mise.toml','tools/engine-probe/sample.md','tools/release/build-candidate.py','tools/release/check-package.py','tools/release/check-diff.py']
CONTRACTS = ['library-cli','book-registration','ocr-library','ocr-recovery','reading-bundles','curated-markdown',
             'html-export','pdf-export','reading-exports','book-digitization-storage-design']
PROJECTIONS={'README.md':'docs/distribution/README.md','AGENTS.md':'docs/distribution/AGENTS.md',
             'docs/architecture/book-digitization-storage-design.md':'docs/distribution/storage-design.md',
             'docs/operations/book-digitization-storage-runbook.md':'docs/distribution/book-runbook.md'}
PATTERNS=['src/signal_library/**/*.py','tests/test_*.py','requirements/*.txt','templates/book-library/*']
LINK=re.compile(r'\[([^\]]+)\]\(([^)]+)\)')


def sha(data):return hashlib.sha256(data).hexdigest()


def safe_file(root,name):
    relative=PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name or re.search(r'[\x00-\x20\x7f]',name):raise ValueError('不正な候補パスです。')
    path=root/Path(name)
    for parent in (path,*path.parents):
        if parent==root:break
        if parent.is_symlink():raise ValueError('候補の参照にsymlinkがあります。')
    if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size>4*1024*1024:
        raise ValueError('候補は上限内の通常ファイルに限定します。')
    # 候補はUTF-8テキストのみ。Git/WindowsのCRLF差をLFへ正規化する。
    return path.read_bytes().decode('utf-8').replace('\r\n','\n').encode('utf-8')


def collect(root):
    root=root.resolve();selected=set(DIRECT)
    selected.update('docs/architecture/'+name+'.md' for name in CONTRACTS)
    selected.update(['docs/operations/book-digitization-storage-runbook.md'])
    # globは追加コード/人工試験だけ。state/記録を再帰的にコピーしない。
    for pattern in PATTERNS:
        selected.update(p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file())
    selected.update(PROJECTIONS.values())
    files={name:safe_file(root,name) for name in sorted(selected)}
    for destination,source in PROJECTIONS.items():files[destination]=files[source]
    # 資格情報/資料を含み得る開発記録へのリンクは候補の検証状態へ案内する。
    changes=[]
    for name,data in list(files.items()):
        if not name.endswith('.md'):continue
        def link(match):
            label,target=match.groups()
            if '://' in target or target.startswith('#') or target.startswith('mailto:'):return match.group(0)
            raw=target.split('#')[0]
            resolved=Path(os.path.normpath(str(Path(name).parent/raw))).as_posix()
            if resolved in files:return match.group(0)
            fallback=Path(os.path.relpath('docs/release-status.md',Path(name).parent)).as_posix()
            changes.append({'file':name,'omitted_target':target,'replacement':fallback})
            return '['+label+']('+fallback+')'
        files[name]=LINK.sub(link,data.decode('utf-8')).encode('utf-8')
    # 禁止場所の名前は読まず、選択済みの出力だけ確認する。
    for name,data in files.items():
        if any(p in {'.git','worklogs','books','inbox','.venv','__pycache__'} for p in PurePosixPath(name).parts):
            raise ValueError('候補に実行state/開発記録が混入しています。')
        if name=='mise.local.toml' or (Path.home().as_posix()+'/').encode() in data:
            raise ValueError('候補に端末固有情報があります。')
    files['MANIFEST.in']=('\n'.join('include '+name for name in sorted(files))+'\ninclude candidate-manifest.json\n').encode()
    manifest={'schema_version':'signal-library-candidate-1','version':'0.1.0.dev1','release_ready':False,
              'license_status':'adopted','license':'MIT','source_text_newlines':'LF','projections':PROJECTIONS,'link_projections':changes,
              'files':[{'path':name,'sha256':sha(data),'bytes':len(data)} for name,data in sorted(files.items())]}
    files['candidate-manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    return files,manifest


def build(root,destination):
    files,manifest=collect(root)
    destination=destination.resolve()
    if destination.exists():raise ValueError('新しい候補保存先を指定してください。')
    destination.mkdir(parents=True,exist_ok=False)
    stage=destination/'signal-library';stage.mkdir()
    for name,data in files.items():
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    archive=destination/'signal-library-0.1.0.dev1-candidate.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_STORED) as bundle:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo('signal-library/'+name,date_time=(2026,10,16,0,0,0))
            info.create_system=3;info.compress_type=zipfile.ZIP_STORED;info.external_attr=0o100644<<16
            bundle.writestr(info,data)
    return {'stage':str(stage),'archive':str(archive),'archive_sha256':sha(archive.read_bytes()),
            'files':len(files),'release_ready':False,'license_status':manifest['license_status']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(build(args.source,args.output),ensure_ascii=False,indent=2))


if __name__=='__main__':main()

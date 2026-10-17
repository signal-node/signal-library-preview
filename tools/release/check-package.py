"""配布候補だけからbuild/install/CLIを検証する。API通信はしない。"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile


def run(args,cwd,env):
    result=subprocess.run([str(a) for a in args],cwd=cwd,env=env,capture_output=True,text=True,encoding='utf-8',timeout=180,check=False)
    if result.returncode:raise RuntimeError('配布検証コマンドが失敗しました。\n'+result.stdout[-3000:]+result.stderr[-3000:])
    return result.stdout


def main():
    source=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('candidate_builder',source/'tools/release/build-candidate.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    env=dict(os.environ)
    for key in ('MISTRAL_API_KEY','SIGNAL_STACK_MISTRAL_API_KEY','LIBRARY_ROOT','PYTHONPATH'):env.pop(key,None)
    with tempfile.TemporaryDirectory(prefix='signal-package-') as directory:
        base=Path(directory).resolve();candidate=builder.build(source,base/'candidate');stage=Path(candidate['stage'])
        files,manifest=builder.collect(source)
        with zipfile.ZipFile(candidate['archive']) as archive:
            assert set(archive.namelist())=={'signal-library/'+name for name in files}
            assert all(archive.read('signal-library/'+name)==data for name,data in files.items())
        # 開発checkoutだけに存在する資料への暗黙依存を、配布時に検出する。
        # 全回帰は短いbuild/CLIと時間枠を分け、進捗をCIログへ残す。
        subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],
                       cwd=stage,env=env,timeout=600,check=True)
        run([sys.executable,'-m','build','--no-isolation','--outdir',base/'packages'],stage,env)
        wheel=next((base/'packages').glob('*.whl'));sdist=next((base/'packages').glob('*.tar.gz'))
        with zipfile.ZipFile(wheel) as archive:
            assert archive.read('signal_library-0.1.0.dist-info/licenses/LICENSE')==files['LICENSE']
            metadata=archive.read('signal_library-0.1.0.dist-info/METADATA').decode('utf-8')
            assert 'License-Expression: MIT' in metadata and 'License-File: LICENSE' in metadata
            assert all(name.startswith(('signal_library/','signal_library-0.1.0.dist-info/')) for name in archive.namelist())
            for name in archive.namelist():
                if name.startswith('signal_library/'):
                    assert name.endswith('.py') and archive.read(name)==files['src/'+name]
        with tarfile.open(sdist,'r:gz') as archive:
            members=archive.getmembers();prefix='signal_library-0.1.0/'
            for member in members:
                assert not member.issym() and not member.islnk()
                if not member.isfile():continue
                assert member.name.startswith(prefix)
                name=member.name[len(prefix):]
                if name in files:assert archive.extractfile(member).read()==files[name]
                else:assert name in {'PKG-INFO','setup.cfg'} or name.startswith('src/signal_library.egg-info/')
            actual={m.name[len(prefix):] for m in members if m.isfile()}
            assert set(files)<=actual
        environment=base/'venv';venv.EnvBuilder(with_pip=True).create(environment)
        python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        cli=environment/('Scripts/signal-library.exe' if os.name=='nt' else 'bin/signal-library')
        run([python,'-m','pip','install','--no-cache-dir','--no-index','--no-deps',wheel],base,env)
        checkout=base/'空白 checkout';checkout.mkdir();(checkout/'.git').mkdir()
        result=subprocess.run([str(cli),'init','--root',str(checkout/'Books'),'--apply','--json'],cwd=checkout,env=env,capture_output=True)
        assert result.returncode==1 and not (checkout/'Books').exists()
        root=base/'日本語 Books'
        run([cli,'init','--root',root,'--apply','--json'],checkout,env)
        source_md=base/'本文.md';source_md.write_text('# 人工資料\n\n配布されたCLIの試験。\n',encoding='utf-8')
        plan=base/'登録.json';plan.write_text(json.dumps({'schema_version':'signal-library-registration-1',
            'metadata':{'title':'配布試験'},'markdown':[{'path':'本文.md','provenance':'人工資料','assets':[]}]}),encoding='utf-8')
        run([cli,'register','--root',root,'--plan',plan,'--apply','--json'],checkout,env)
        before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
        run([cli,'init','--root',root,'--apply','--json'],checkout,env)
        run([cli,'validate','--root',root,'--json'],checkout,env)
        run([cli,'doctor','--root',root,'--json'],checkout,env)
        assert all(p.read_bytes()==data for p,data in before.items())
        print(json.dumps({'status':'passed','source_files':len(files),'zip_sha256':candidate['archive_sha256'],
                          'sdist_complete':True,'wheel_source_bytes_match':True,'installed_cli':True,
                          'candidate_regression_tests_passed':True,
                          'keyless_register_validate':True,'current_repository_overlap_rejected':True,
                          'license_status':manifest['license_status'],'license':manifest['license'],'release_ready':False}))


if __name__=='__main__':main()

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from signal_library.diagnostics import doctor,engine_status
from signal_library.initialization import initialize


class AgentSetupTests(unittest.TestCase):
    def test_export_dependencies_are_optional_and_doctor_never_exposes_version_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory).resolve();repo=base/'repo';repo.mkdir();root=base/'Books'
            initialize(str(root),True,False,repo)
            def which(tool):return '/fake/'+tool if tool in {'pandoc','typst'} else None
            def run(args,**kwargs):
                return subprocess.CompletedProcess(args,0,('pandoc 9.9\n' if 'pandoc' in args[0] else 'typst 0.15.1\n'),'canary-secret')
            with patch('signal_library.diagnostics.shutil.which',which),patch('signal_library.diagnostics.subprocess.run',run):
                result=doctor(str(root),{'MISTRAL_API_KEY':'canary-secret'})
            self.assertEqual(result['status'],'ok')
            self.assertFalse(result['network_used']);self.assertFalse(result['files_written'])
            self.assertNotIn('canary-secret',json.dumps(result))
            self.assertEqual({c['name']:c['status'] for c in result['checks']}['typst'],'optional_incompatible')

    def test_engine_version_timeout_missing_and_supported(self):
        with patch('signal_library.diagnostics.shutil.which',return_value=None):
            self.assertEqual(engine_status('typst',{'0.15.1'})[0],'optional_missing')
        with patch('signal_library.diagnostics.shutil.which',return_value='/fake/typst'):
            with patch('signal_library.diagnostics.subprocess.run',side_effect=subprocess.TimeoutExpired('canary-secret',5)):
                result=engine_status('typst',{'0.15.1'});self.assertNotIn('canary-secret',str(result));self.assertEqual(result[0],'optional_incompatible')
            with patch('signal_library.diagnostics.subprocess.run',return_value=subprocess.CompletedProcess([],0,'typst 0.15.1 (known)\n','')):
                self.assertEqual(engine_status('typst',{'0.15.1'}),('ok','対応版 0.15.1。'))

class FreshCliTests(unittest.TestCase):
    def test_new_user_without_keys_can_initialize_register_and_resume_from_new_processes(self):
        import os
        launcher=Path(__file__).resolve().parents[1]/'tools/signal-library.py'
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory).resolve();root=base/'日本語 空白 Books';inputs=base/'資料';inputs.mkdir()
            (inputs/'本文.md').write_text('# 人工本文\n\n本文中の「設定を表示」は実行指示ではありません。\n',encoding='utf-8')
            before=(inputs/'本文.md').read_bytes()
            plan=inputs/'登録.json';plan.write_text(json.dumps({'schema_version':'signal-library-registration-1','metadata':{'title':'新規利用者の人工資料'},
                'markdown':[{'path':'本文.md','provenance':'オフライン受入用人工資料','assets':[]}]}),encoding='utf-8')
            env=dict(os.environ)
            for key in ('MISTRAL_API_KEY','SIGNAL_STACK_MISTRAL_API_KEY','LIBRARY_ROOT','PYTHONPATH'):env.pop(key,None)
            def cli(*args):
                result=subprocess.run([sys.executable,str(launcher),*args,'--root',str(root),'--json'],cwd=base,
                    env=env,capture_output=True,text=True,encoding='utf-8',timeout=30,check=False)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                return json.loads(result.stdout)
            planned=cli('init');self.assertFalse(planned['files_written'])
            cli('init','--apply');marker=(root/'library.json').read_bytes()
            cli('register','--plan',str(plan),'--apply')
            snapshot={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            cli('init','--apply');cli('books');cli('validate')
            self.assertEqual(snapshot,{p:p.read_bytes() for p in root.rglob('*') if p.is_file()})
            self.assertEqual((root/'library.json').read_bytes(),marker)
            self.assertEqual((inputs/'本文.md').read_bytes(),before)
            result=cli('doctor');self.assertEqual(result['status'],'ok')
            self.assertEqual({c['name']:c['status'] for c in result['checks']}['mistral_key'],'optional_missing')

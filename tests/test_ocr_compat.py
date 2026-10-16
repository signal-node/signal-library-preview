import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from signal_library.ocr.compat_runner import encode,decode


class CompatTests(unittest.TestCase):
    def test_protocol_roundtrips_paths_options_bytes_and_tuples(self):
        value=SimpleNamespace(path=Path('日本語/a.png'),names={'a','b'},data=b'abc',pair=(Path('x'),2),mapping={(0,'図'):Path('a.png'),'$path':'ordinary data'})
        restored=decode(json.loads(json.dumps(encode(value))))
        self.assertEqual(restored.path,value.path)
        self.assertEqual(restored.names,value.names)
        self.assertEqual(restored.data,value.data)
        self.assertEqual(restored.pair,value.pair)
        self.assertEqual(restored.mapping,value.mapping)

    def run_backend(self,component,function,args,kwargs):
        env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
        env['MISTRAL_API_KEY']='synthetic-canary'
        payload={'component':component,'function':function,'args':encode(args),'kwargs':encode(kwargs)}
        return subprocess.run([sys.executable,'-m','signal_library.ocr.compat_runner'],input=json.dumps(payload),text=True,encoding='utf-8',capture_output=True,env=env)

    def test_backend_json_files_roundtrip_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder).resolve()/'manifest.json'
            result=self.run_backend('manifest','write_json_atomic',(path,{'schema_version':1,'state':'fixture'}),{})
            self.assertEqual(result.returncode,0,result.stdout)
            result=self.run_backend('manifest','read_manifest',(path,),{})
            self.assertEqual(decode(json.loads(result.stdout)['result'])['state'],'fixture')
            self.assertNotIn('synthetic-canary',result.stdout)

    def test_backend_rejects_unknown_functions_and_key_disclosure(self):
        for component,function in (('os','system'),('gateway','api_key_from_env')):
            result=self.run_backend(component,function,(),{})
            self.assertEqual(result.returncode,1)
            self.assertFalse(json.loads(result.stdout)['ok'])
            self.assertNotIn('synthetic-canary',result.stdout)

    def test_missing_package_is_explained_without_import_side_effect(self):
        source=Path(__file__).resolve().parents[1]/'tools/migration/signal-stack-compat/src/signal_stack'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=root/'signal_stack';package.mkdir()
            (package/'__init__.py').write_text('')
            (package/'exceptions.py').write_text('class SignalStackError(Exception): pass\n')
            for name in ('_ocr_compat.py','orchestration/ocr_batch.py'):
                target=package/name;target.parent.mkdir(exist_ok=True)
                target.write_bytes((source/name).read_bytes())
            script="from signal_stack.orchestration.ocr_batch import status_ocr\nfrom signal_stack.exceptions import SignalStackError\ntry:\n status_ocr(None)\nexcept SignalStackError as exc:\n print(str(exc))\n"
            env=dict(os.environ);env['PYTHONPATH']=str(root)
            result=subprocess.run([sys.executable,'-S','-c',script],capture_output=True,text=True,encoding='utf-8',env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Signal Library',result.stdout)
            self.assertIn('未導入',result.stdout)


if __name__=='__main__':unittest.main()

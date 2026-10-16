"""実Agentを起動せず、受入素材・独立cloneと開始前状態を検査する。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AgentTrialTests(unittest.TestCase):
    def test_isolated_clean_clones_keep_inputs_and_need_no_key(self):
        source=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('trial',source/'tools/acceptance/prepare-agent-trial.py')
        trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory).resolve()/'試験 空白'
            report=trial.prepare(source,destination)
            self.assertFalse(report['agent_started']);self.assertFalse(report['network_used'])
            for item in report['trials']:
                root=Path(item['library_root']);checkout=Path(item['checkout']);self.assertFalse(root.exists())
                self.assertEqual(set(item['runtime_env']),{'MISE_STATE_DIR','MISE_CACHE_DIR'})
                for value in item['runtime_env'].values():self.assertTrue(Path(value).is_dir());self.assertTrue(Path(value).is_relative_to(checkout.parent))
                self.assertTrue((checkout/'LICENSE').is_file());self.assertFalse((checkout/'mise.local.toml').exists())
                self.assertFalse((checkout/'worklogs').exists());self.assertTrue((checkout/'CLAUDE.md').is_file())
                env={'PATH':__import__('os').environ['PATH'],'SYSTEMROOT':__import__('os').environ.get('SYSTEMROOT','')}
                def cli(*args):
                    result=subprocess.run([sys.executable,str(checkout/'tools/signal-library.py'),*args,'--root',str(root),'--json'],cwd=checkout,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
                    self.assertEqual(result.returncode,0,result.stderr+result.stdout);return json.loads(result.stdout)
                cli('init','--apply');made=cli('register','--plan',item['registration_plan'],'--apply')
                self.assertEqual(cli('validate')['count'],1)
                snapshot={p:p.read_bytes() for p in root.rglob('*') if p.is_file()};cli('init','--apply')
                self.assertEqual(snapshot,{p:p.read_bytes() for p in snapshot})
                for record in item['inputs']:self.assertEqual(trial.sha(Path(record['path']).read_bytes()),record['sha256'])
                self.assertEqual(cli('books')['books'][0]['book_id'],made['book_id'])
            with self.assertRaises(ValueError):trial.prepare(source,destination)

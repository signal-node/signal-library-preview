import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from signal_library.cli import repository_roots
from signal_library.initialization import InitializationError,initialize


class InstalledBoundaryTests(unittest.TestCase):
    def test_installed_cli_protects_current_git_repository_and_its_parents(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory).resolve();repo=base/'checkout';repo.mkdir();(repo/'.git').write_text('gitdir: unused',encoding='utf-8')
            child=repo/'subdir';child.mkdir();module=base/'venv/site-packages/signal_library/cli.py';module.parent.mkdir(parents=True);module.touch()
            roots=repository_roots(child,module)
            self.assertIn(repo,roots);self.assertNotIn(module.parents[1],roots)
            for destination in (repo,repo/'Books',base):
                with self.subTest(destination=destination):
                    with self.assertRaises(InitializationError):initialize(str(destination),False,False,roots)
            # 独立した書籍保存先はwheelの位置に左右されず使える。
            self.assertEqual(initialize(str(base/'Books'),True,False,roots)['state'],'initialized')

    def test_source_archive_without_git_marker_is_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory).resolve();project=base/'source';module=project/'src/signal_library/cli.py'
            module.parent.mkdir(parents=True);module.touch();(project/'pyproject.toml').touch()
            roots=repository_roots(base,module)
            self.assertIn(project,roots)
            with self.assertRaises(InitializationError):initialize(str(project/'Books'),False,False,roots)

class CandidateBoundaryTests(unittest.TestCase):
    def builder(self):
        import importlib.util
        path=Path(__file__).resolve().parents[1]/'tools/release/build-candidate.py'
        spec=importlib.util.spec_from_file_location('candidate_builder',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_allowlist_omits_new_secrets_and_state_and_zip_is_reproducible(self):
        import secrets
        import zipfile
        builder=self.builder();repo=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory).resolve();root=base/'source';root.mkdir()
            files,_=builder.collect(repo)
            for name,data in files.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            canary=secrets.token_hex(24).encode()
            for name in ('mise.local.toml','.env','books/book-secret/book.json','worklogs/private.md','src/signal_library/secret.key'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(canary)
            files,manifest=builder.collect(root)
            self.assertFalse(manifest['release_ready']);self.assertEqual(manifest['license_status'],'adopted');self.assertEqual(manifest['license'],'MIT');self.assertIn('LICENSE',files)
            self.assertFalse(any(canary in data for data in files.values()))
            first=builder.build(root,base/'first');second=builder.build(root,base/'second')
            self.assertEqual(first['archive_sha256'],second['archive_sha256'])
            # ネイティブWindows相当の行末でも、同じ候補内容/hashになる。
            for path in root.rglob('*'):
                if path.is_file():path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
            crlf=builder.build(root,base/'crlf')
            self.assertEqual(first['archive_sha256'],crlf['archive_sha256'])
            with zipfile.ZipFile(first['archive']) as bundle:
                self.assertEqual(set(bundle.namelist()),{'signal-library/'+name for name in files})
            with self.assertRaises(ValueError):builder.build(root,base/'first')

    def test_allowed_file_redirect_and_manifest_control_characters_are_rejected(self):
        builder=self.builder()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();outside=root/'outside';outside.write_text('not allowed',encoding='utf-8')
            link=root/'link.py'
            try:link.symlink_to(outside)
            except OSError:pass  # Windowsのsymlink権限がない場合もパス文字の境界は試験する。
            else:
                with self.assertRaises(ValueError):builder.safe_file(root,'link.py')
            for name in ('../outside','/outside','bad\ninclude-other.py','bad\\path.py'):
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):builder.safe_file(root,name)

class CiDiffTests(unittest.TestCase):
    def test_clean_checkout_still_rejects_whitespace_in_push_range(self):
        import importlib.util,json,os,subprocess
        from unittest.mock import patch
        path=Path(__file__).resolve().parents[1]/'tools/release/check-diff.py'
        spec=importlib.util.spec_from_file_location('check_diff',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            def git(*args):return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True).stdout.strip()
            git('init');git('config','user.name','人工試験');git('config','user.email','fixture@example.invalid')
            (root/'file.txt').write_text('clean\n',encoding='utf-8');git('add','file.txt');git('commit','-m','base');base=git('rev-parse','HEAD')
            (root/'file.txt').write_text('bad whitespace  \n',encoding='utf-8');git('add','file.txt');git('commit','-m','head');head=git('rev-parse','HEAD')
            event=root/'event.json';event.write_text(json.dumps({'before':base,'after':head}),encoding='utf-8')
            # git diff --checkだけならclean checkoutなので通るが、変更commit範囲は失敗する。
            git('diff','--check')
            original=module.subprocess.run
            def run(args,**kwargs):return original(args,cwd=root,**kwargs)
            with patch.object(module.subprocess,'run',run):
                with self.assertRaises(subprocess.CalledProcessError):module.check('push',event)
                event.write_text(json.dumps({'before':'0'*40,'after':base}),encoding='utf-8')
                self.assertEqual(module.check('push',event),'initial-push')
                event.write_text(json.dumps({'pull_request':{'base':{'sha':base},'head':{'sha':head}}}),encoding='utf-8')
                with self.assertRaises(subprocess.CalledProcessError):module.check('pull_request',event)

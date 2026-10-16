"""保存ルート変更の明示的な再接続・原版保全・中断回復。"""
import json
import shutil
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

import test_ocr_recovery as recovery_tests
from test_ocr_migrated import FakeGateway
from signal_library.ocr import bindings as b, library as o, recovery as r
from signal_library.registration import RegistrationError, read_json, digest, inventory
from signal_library.ocr.manifest import write_json_atomic


class BindingTests(unittest.TestCase):
    setUp = recovery_tests.RecoveryTests.setUp
    send = recovery_tests.RecoveryTests.send
    partial = recovery_tests.RecoveryTests.partial
    retry_success = recovery_tests.RecoveryTests.retry_success

    def relocate(self, sent):
        previous = self.root
        self.root = self.base/'コピー先 空白'
        shutil.copytree(previous, self.root)
        previous.rename(self.base/'旧保存先 利用不可')
        return self.root/sent['manifest_path']

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): digest(p)
                for p in self.root.rglob('*') if p.is_file()}

    def test_copy_plan_apply_archive_and_failed_only_retry(self):
        sent = self.partial(); path = self.relocate(sent)
        before = path.read_bytes(); operation = path.parent.parent/'operation.json'
        op_before = operation.read_bytes(); all_before = self.snapshot()
        with self.assertRaises(RegistrationError): o.load_managed(str(self.root), sent['manifest_path'])
        planned = b.rebind(str(self.root), sent['manifest_path'])
        self.assertFalse(planned['network_used']); self.assertEqual(self.snapshot(), all_before)
        with patch.object(FakeGateway, 'upload', side_effect=AssertionError('upload forbidden')), \
             patch.object(FakeGateway, 'create_batch', side_effect=AssertionError('submit forbidden')):
            applied = b.rebind(str(self.root), sent['manifest_path'], True)
            o.load_managed(str(self.root), sent['manifest_path'])
            retry = r.retry(str(self.root), sent['manifest_path'], 1, .01)
            self.assertEqual(retry['plan']['pages'], 1)
        folder = self.root/applied['history_path']
        self.assertEqual((folder/'manifest.before.json').read_bytes(), before)
        self.assertEqual((folder/'operation.before.json').read_bytes(), op_before)
        after = read_json(path); original = json.loads(before)
        for key in ('run_id','batch','documents','state','model_requested'):
            self.assertEqual(after[key], original[key])
        for name, sha in all_before.items():
            if name != sent['manifest_path']: self.assertEqual(digest(self.root/name), sha, name)
        self.assertEqual(len(inventory(self.root)[0][self.identifier]['ocr_runs']), 1)
        snap = self.snapshot()
        self.assertEqual(b.rebind(str(self.root), sent['manifest_path'], True)['state'], 'already_bound')
        self.assertEqual(self.snapshot(), snap)
        with patch.object(FakeGateway, 'upload', side_effect=AssertionError('upload forbidden')), patch.object(FakeGateway, 'create_batch', side_effect=AssertionError('submit forbidden')):
            self.assertTrue(o.follow(str(self.root), sent['manifest_path'], False, FakeGateway)['network_used'])

    def test_interrupt_each_archive_and_after_manifest_then_resume(self):
        sent = self.send(); original_root = self.root
        for stage in ('journal', 'manifest.before.json', 'operation.before.json', 'binding.json', 'after-manifest'):
            with self.subTest(stage=stage):
                self.root = self.base/stage
                shutil.copytree(original_root, self.root)
                path = self.root/sent['manifest_path']; before = path.read_bytes()
                original_new = b.write_new; original_atomic = b.write_bytes_atomic
                def interrupted_new(target, data):
                    original_new(target, data)
                    if target.name == (b.JOURNAL if stage == 'journal' else stage): raise OSError('interrupted')
                def interrupted_atomic(target, data):
                    original_atomic(target, data)
                    if stage == 'after-manifest': raise OSError('interrupted')
                with patch.object(b,'write_new',side_effect=interrupted_new), patch.object(b,'write_bytes_atomic',side_effect=interrupted_atomic):
                    with self.assertRaises(OSError): b.rebind(str(self.root), sent['manifest_path'], True)
                self.assertTrue((path.parent/b.JOURNAL).exists())
                with self.assertRaises(RegistrationError): o.load_managed(str(self.root), sent['manifest_path'])
                result = b.rebind(str(self.root), sent['manifest_path'], True)
                self.assertEqual((self.root/result['history_path']/'manifest.before.json').read_bytes(), before)
                self.assertFalse((path.parent/b.JOURNAL).exists())
                o.load_managed(str(self.root), sent['manifest_path'])

    def test_second_move_keeps_history_chain(self):
        sent = self.partial(); self.relocate(sent)
        first = b.rebind(str(self.root), sent['manifest_path'], True)
        snapshot = {p.name:p.read_bytes() for p in (self.root/first['history_path']).iterdir()}
        newer = self.base/'さらに移動'; shutil.copytree(self.root, newer); self.root = newer
        b.rebind(str(self.root), sent['manifest_path'], True)
        _,path,manifest,_ = o.load_managed(str(self.root), sent['manifest_path'])
        self.assertEqual(len(manifest['path_bindings']), 2)
        self.assertEqual({p.name:p.read_bytes() for p in (self.root/first['history_path']).iterdir()}, snapshot)
        self.assertEqual(len(inventory(self.root)[0][self.identifier]['ocr_runs']), 1)

    def test_windows_historical_paths_and_inconsistent_capture(self):
        sent = self.send(); path = self.root/sent['manifest_path']; manifest = read_json(path)
        for key in ('run_dir','input_root','input_path'):
            if key in manifest:
                manifest[key] = str(PureWindowsPath('D:/資料 Books')/PureWindowsPath(Path(manifest[key]).relative_to(self.root).as_posix()))
        write_json_atomic(path,manifest)
        b.rebind(str(self.root),sent['manifest_path'], True)
        o.load_managed(str(self.root),sent['manifest_path'])
        manifest=read_json(path); manifest['input_root']='E:\\different\\capture';write_json_atomic(path,manifest)
        with self.assertRaises(RegistrationError): b.rebind(str(self.root),sent['manifest_path'],True)

    def test_tampered_journal_operation_source_or_history_rejected(self):
        sent=self.send(); self.relocate(sent); path=self.root/sent['manifest_path']
        real=b.write_new
        def stop(target,data):
            real(target,data)
            if target.name==b.JOURNAL:raise OSError('interrupted')
        with patch.object(b,'write_new',side_effect=stop):
            with self.assertRaises(OSError):b.rebind(str(self.root),sent['manifest_path'],True)
        journal=path.parent/b.JOURNAL; original=journal.read_bytes(); tx=read_json(journal)
        tx['after']=tx['after'].replace('mistral-ocr-4-1','mistral-ocr-4-0');write_json_atomic(journal,tx)
        with self.assertRaises(RegistrationError):b.rebind(str(self.root),sent['manifest_path'],True)
        journal.write_bytes(original)
        operation=path.parent.parent/'operation.json'; raw=operation.read_bytes(); operation.write_bytes(raw+b' ')
        with self.assertRaises(RegistrationError):b.rebind(str(self.root),sent['manifest_path'],True)
        operation.write_bytes(raw)
        result=b.rebind(str(self.root),sent['manifest_path'],True)
        archived=self.root/result['history_path']/'manifest.before.json'; archived.write_bytes(archived.read_bytes()+b' ')
        with self.assertRaises(RegistrationError):o.load_managed(str(self.root),sent['manifest_path'])

    def test_changed_original_refuses_plan_without_writes(self):
        sent=self.send();self.relocate(sent)
        source=next((self.root/'books'/self.identifier/'originals').rglob('*.png'))
        source.write_bytes(source.read_bytes()+b'changed');snapshot=self.snapshot()
        with self.assertRaises(RegistrationError):b.rebind(str(self.root),sent['manifest_path'],True)
        self.assertEqual(self.snapshot(),snapshot)

    def test_unknown_archive_file_refuses_resume(self):
        sent=self.send();path=self.relocate(sent);real=b.write_new
        def stop(target,data):
            real(target,data)
            if target.name=='manifest.before.json':raise OSError('interrupted')
        with patch.object(b,'write_new',side_effect=stop):
            with self.assertRaises(OSError):b.rebind(str(self.root),sent['manifest_path'],True)
        tx=read_json(path.parent/b.JOURNAL);folder=path.parent/tx['folder']
        (folder/'unknown.txt').write_text('preserve',encoding='utf-8');snapshot=self.snapshot()
        with self.assertRaises(RegistrationError):b.rebind(str(self.root),sent['manifest_path'],True)
        self.assertEqual(self.snapshot(),snapshot)

    def test_pending_job_recovery_does_not_resubmit(self):
        sent=self.send();path=self.relocate(sent);m=read_json(path)
        m['state']='SUBMISSION_UNKNOWN';m['batch'].pop('id');write_json_atomic(path,m)
        b.rebind(str(self.root),sent['manifest_path'],True)
        with patch.object(FakeGateway,'upload',side_effect=AssertionError('upload forbidden')), \
             patch.object(FakeGateway,'create_batch',side_effect=AssertionError('submit forbidden')):
            result=r.recover(str(self.root),(path.parent.parent/'operation.json').relative_to(self.root).as_posix(),FakeGateway)
        self.assertEqual(result['state'],'SUBMITTED_RECOVERED')

    def test_merged_run_and_original_source_archives_survive_move(self):
        parent=self.partial();child=self.retry_success(parent)
        merged=r.merge(str(self.root),[parent['manifest_path'],child['manifest_path']],True)
        self.relocate(merged);path=self.root/merged['manifest_path']
        m=read_json(path);archives={d['source_manifest']: (path.parent/d['source_manifest']).read_bytes() for d in m['documents']}
        b.rebind(str(self.root),merged['manifest_path'],True)
        o.load_managed(str(self.root),merged['manifest_path'])
        self.assertEqual({name:(path.parent/name).read_bytes() for name in archives},archives)

if __name__=='__main__':unittest.main()

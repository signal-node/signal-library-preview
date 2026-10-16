import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from signal_library.initialization import initialize
from signal_library.registration import RegistrationError, register, inventory, read_json, digest
from signal_library.ocr import library as o, recovery as r, results
from signal_library.ocr.errors import OcrError
from signal_library.ocr.manifest import write_json_atomic
from test_ocr_migrated import FakeGateway


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base/'Books'
        repo = self.base/'repo'; repo.mkdir()
        initialize(str(self.root), True, False, repo)
        images = []
        for index in range(2):
            p = self.base/f'{index}.png'; p.write_bytes(b'\x89PNG\r\n\x1a\n'+bytes([index])*20)
            images.append({'path': str(p)})
        self.plan = self.base/'plan.json'
        self.plan.write_text(json.dumps({'schema_version': 'signal-library-registration-1', 'metadata': {'title': 'Recovery fixture'},
            'images': images, 'order_confirmed': True}), encoding='utf-8')
        self.identifier = register(str(self.root), self.plan, True)['book_id']
        FakeGateway.reset(); FakeGateway.job.model = 'mistral-ocr-4-1'

    def send(self, gateway=FakeGateway):
        planned = o.plan_ocr(str(self.root), self.identifier, 'capture-001', max_pages=2, max_cost_usd=.01)
        return o.submit(str(self.root), self.identifier, 'capture-001', planned['plan_hash'], True, max_pages=2, max_cost_usd=.01, gateway_factory=gateway)

    def partial(self):
        sent = self.send()
        path = self.root/sent['manifest_path']
        docs = read_json(path)['documents']
        FakeGateway.job.error_file = 'error-1'
        success = {'custom_id': docs[0]['custom_id'], 'response': {'body': {'pages': [{'index': 0, 'markdown': '# First page'}]}}}
        failure = {'custom_id': docs[1]['custom_id'], 'error': {'message': 'artificial failure'}}
        FakeGateway.downloads['output-1'] = (json.dumps(success)+'\n').encode()
        FakeGateway.downloads['error-1'] = (json.dumps(failure)+'\n').encode()
        self.assertEqual(o.follow(str(self.root), sent['manifest_path'], True, FakeGateway)['status'], 'action_required')
        return sent

    def retry_success(self, parent):
        planned = r.retry(str(self.root), parent['manifest_path'], 1, .01)
        self.assertEqual(planned['plan']['pages'], 1)
        self.assertEqual(len(planned['plan']['sources']), 1)
        FakeGateway.job.id = 'job-2'; FakeGateway.job.error_file = None
        sent = r.retry(str(self.root), parent['manifest_path'], 1, .01, True, planned['plan_hash'], True, FakeGateway)
        manifest = read_json(self.root/sent['manifest_path'])
        identifier = manifest['documents'][0]['custom_id']
        FakeGateway.downloads['output-1'] = (json.dumps({'custom_id': identifier, 'response': {'body': {'pages': [{'index': 0, 'markdown': '# Second page'}]}}})+'\n').encode()
        self.assertEqual(o.follow(str(self.root), sent['manifest_path'], True, FakeGateway)['status'], 'ok')
        return sent

    def test_partial_retry_merge_preserves_ids_originals_and_history(self):
        before = {p.relative_to(self.root).as_posix(): digest(p) for p in (self.root/'books'/self.identifier/'originals').rglob('*.png')}
        parent = self.partial(); child = self.retry_success(parent)
        original_parent = (self.root/parent['manifest_path']).read_bytes()
        original_child = (self.root/child['manifest_path']).read_bytes()
        planned = r.merge(str(self.root), [parent['manifest_path'], child['manifest_path']])
        self.assertFalse(planned['files_written'])
        merged = r.merge(str(self.root), [parent['manifest_path'], child['manifest_path']], True)
        manifest = read_json(self.root/merged['manifest_path'])
        self.assertEqual(len(manifest['documents']), 2)
        self.assertTrue(results.validate_run((self.root/merged['manifest_path']).parent, True)['ok'])
        self.assertTrue(all((self.root/merged['manifest_path']).parent.joinpath(doc['source_manifest']).is_file() for doc in manifest['documents']))
        self.assertEqual(r.merge(str(self.root), [parent['manifest_path'], child['manifest_path']], True)['manifest_path'], merged['manifest_path'])
        self.assertFalse(o.follow(str(self.root), merged['manifest_path'])['network_used'])
        self.assertEqual((self.root/parent['manifest_path']).read_bytes(), original_parent)
        self.assertEqual((self.root/child['manifest_path']).read_bytes(), original_child)
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): digest(p) for p in (self.root/'books'/self.identifier/'originals').rglob('*.png')})
        self.assertEqual(len(inventory(self.root)[0][self.identifier]['ocr_runs']), 3)

    def test_unknown_submission_recovery_never_creates_new_job(self):
        class Unknown(FakeGateway):
            def create_batch(self, *args, **kwargs):
                raise TimeoutError('synthetic')
        with self.assertRaises(OcrError):
            self.send(Unknown)
        operation = next(self.root.rglob('operation.json'))
        before = len(FakeGateway.uploads)
        result = r.recover(str(self.root), operation.relative_to(self.root).as_posix(), FakeGateway)
        self.assertEqual(result['state'], 'SUBMITTED_RECOVERED')
        self.assertEqual(len(FakeGateway.uploads), before)
        self.assertEqual(read_json(operation)['state'], 'RECOVERED')
        with self.assertRaises(RegistrationError):
            self.send()

    def test_killed_submitting_state_recovers_by_search(self):
        sent = self.send(); path=self.root/sent['manifest_path']
        m=read_json(path);m['state']='SUBMITTING';m['batch'].pop('id');write_json_atomic(path,m)
        before=len(FakeGateway.uploads)
        result=r.recover(str(self.root),(path.parent.parent/'operation.json').relative_to(self.root).as_posix(),FakeGateway)
        self.assertEqual(result['state'],'SUBMITTED_RECOVERED');self.assertEqual(len(FakeGateway.uploads),before)

    def test_zero_or_multiple_remote_matches_refuse_resend(self):
        sent=self.send();path=self.root/sent['manifest_path'];m=read_json(path)
        m['state']='SUBMISSION_UNKNOWN';m['batch'].pop('id');write_json_atomic(path,m)
        for matches in ([],[FakeGateway.job,FakeGateway.job]):
            with patch.object(FakeGateway,'find_batches',return_value=matches):
                with self.assertRaises(OcrError):
                    r.recover(str(self.root),(path.parent.parent/'operation.json').relative_to(self.root).as_posix(),FakeGateway)
        self.assertEqual(read_json(path)['state'],'SUBMISSION_UNKNOWN')

    def test_retry_needs_explicit_fresh_plan_and_upload_permission(self):
        parent=self.partial()
        uploads=len(FakeGateway.uploads)
        with self.assertRaises(RegistrationError):
            r.retry(str(self.root),parent['manifest_path'],1,.01,True,'bad',True,FakeGateway)
        planned=r.retry(str(self.root),parent['manifest_path'],1,.01)
        with self.assertRaises(RegistrationError):
            r.retry(str(self.root),parent['manifest_path'],1,.01,True,planned['plan_hash'],False,FakeGateway)
        self.assertEqual(len(FakeGateway.uploads),uploads)

    def test_retry_pending_or_unknown_job_is_rejected(self):
        sent=self.send()
        with self.assertRaises(RegistrationError):
            r.retry(str(self.root),sent['manifest_path'],1,.01)

    def test_merge_rejects_modified_hash_or_unresolved_failure(self):
        parent=self.partial()
        with self.assertRaises((RegistrationError,OcrError)):
            r.merge(str(self.root),[parent['manifest_path'],parent['manifest_path']],True)
        child=self.retry_success(parent)
        path=self.root/child['manifest_path'];m=read_json(path);m['documents'][0]['sha256']='0'*64;write_json_atomic(path,m)
        with self.assertRaises(RegistrationError):
            r.merge(str(self.root),[parent['manifest_path'],child['manifest_path']],True)

    def test_index_interruption_recovers_and_checks_journal_content(self):
        sent=self.send();path=self.root/sent['manifest_path']
        docs=read_json(path)['documents']
        FakeGateway.downloads['output-1']=''.join(json.dumps({'custom_id':d['custom_id'],'response':{'body':{'pages':[{'index':0,'markdown':'page'}]}}})+'\n' for d in docs).encode()
        actual=o.publish
        def fail(root,entry):
            if entry['path']=='catalog.csv':raise OSError('after book')
            actual(root,entry)
        with patch.object(o,'publish',side_effect=fail):
            with self.assertRaises(OSError):o.follow(str(self.root),sent['manifest_path'],True,FakeGateway)
        journal=path.parent/'library-index.json'; original=journal.read_bytes()
        altered=read_json(journal);altered['files'][0]['text']='wrong';write_json_atomic(journal,altered)
        with self.assertRaises(RegistrationError):o.follow(str(self.root),sent['manifest_path'],True,FakeGateway)
        journal.write_bytes(original)
        o.follow(str(self.root),sent['manifest_path'],True,FakeGateway)
        self.assertFalse(journal.exists())
        self.assertEqual(len(inventory(self.root)[0][self.identifier]['ocr_runs'][0]['documents']),2)

    def test_import_old_paths_preserves_original_manifest_and_custom_ids(self):
        sent=self.partial();path=self.root/sent['manifest_path']
        external=self.base/'legacy';shutil.copytree(path.parent,external)
        old=read_json(external/'manifest.json');old['run_id']='legacy-run';old['input_root']='/old/unavailable';old['input_path']='/old/unavailable';old['run_dir']='/old/output'
        for doc in old['documents']:doc['source_relative_path']='old-'+doc['source_relative_path']
        write_json_atomic(external/'manifest.json',old)
        before={p.relative_to(external).as_posix():p.read_bytes() for p in external.rglob('*') if p.is_file()}
        planned=r.import_run(str(self.root),self.identifier,'capture-001',external/'manifest.json')
        self.assertFalse(planned['files_written'])
        imported=r.import_run(str(self.root),self.identifier,'capture-001',external/'manifest.json',True)
        m=read_json(self.root/imported['manifest_path'])
        self.assertEqual([d['custom_id'] for d in m['documents']],[d['custom_id'] for d in old['documents']])
        self.assertEqual((self.root/imported['manifest_path']).parent.joinpath('legacy-manifest.json').read_bytes(),before['manifest.json'])
        self.assertEqual(before,{p.relative_to(external).as_posix():p.read_bytes() for p in external.rglob('*') if p.is_file()})
        self.assertEqual(r.import_run(str(self.root),self.identifier,'capture-001',external/'manifest.json',True)['manifest_path'],imported['manifest_path'])
        retry=r.retry(str(self.root),imported['manifest_path'],1,.01)
        self.assertEqual(retry['plan']['sources'][0]['custom_id'],old['documents'][1]['custom_id'])

    def test_import_legacy_combined_preserves_source_manifests(self):
        parent=self.partial();child=self.retry_success(parent)
        merged=r.merge(str(self.root),[parent['manifest_path'],child['manifest_path']],True)
        external=self.base/'old-combined';shutil.copytree((self.root/merged['manifest_path']).parent,external)
        old=read_json(external/'manifest.json')
        for key in ('run_id','run_dir','input_root','model_requested','cleanup'):old.pop(key,None)
        write_json_atomic(external/'manifest.json',old)
        snapshot={p.relative_to(external).as_posix():p.read_bytes() for p in external.rglob('*') if p.is_file()}
        imported=r.import_run(str(self.root),self.identifier,'capture-001',external/'manifest.json',True)
        path=self.root/imported['manifest_path']
        self.assertTrue(results.validate_run(path.parent,True)['ok'])
        self.assertTrue(read_json(path)['run_id'].startswith('imported-combined-'))
        self.assertEqual((path.parent/'legacy-manifest.json').read_bytes(),snapshot['manifest.json'])
        self.assertEqual(snapshot,{p.relative_to(external).as_posix():p.read_bytes() for p in external.rglob('*') if p.is_file()})
        self.assertFalse(o.follow(str(self.root),imported['manifest_path'])['network_used'])

    def test_symlink_assets_and_escape_stem_are_rejected(self):
        parent=self.partial();child=self.retry_success(parent)
        childpath=self.root/child['manifest_path'];m=read_json(childpath)
        m['documents'][0]['output_stem']='../../escape';write_json_atomic(childpath,m)
        with self.assertRaises((RegistrationError,OcrError)):
            r.merge(str(self.root),[parent['manifest_path'],child['manifest_path']],True)
        m['documents'][0]['output_stem']='0002';write_json_atomic(childpath,m)
        link=childpath.parent/'documents/unused-link'
        try:link.symlink_to(self.base/'outside')
        except OSError:self.skipTest('symlink権限なし')
        with self.assertRaises((RegistrationError,OcrError)):
            r.merge(str(self.root),[parent['manifest_path'],child['manifest_path']],True)

    def test_cleanup_partial_failure_and_already_deleted_file_resume(self):
        parent=self.partial()
        path=self.root/parent['manifest_path']
        calls=[]
        class Temporary(FakeGateway):
            def delete_file(self,file_id):
                calls.append(file_id)
                if file_id.startswith('ocr-'):raise ConnectionError('transient')
        o.follow(str(self.root),parent['manifest_path'],True,Temporary,keep_remote_files=False)
        first=read_json(path)['cleanup']
        self.assertEqual(first['state'],'PENDING')
        completed=set(first['deleted_file_ids']);calls.clear()
        class NotFound(Exception):status_code=404
        class AlreadyDeleted(FakeGateway):
            def delete_file(self,file_id):
                calls.append(file_id);raise NotFound()
        o.follow(str(self.root),parent['manifest_path'],True,AlreadyDeleted,keep_remote_files=False)
        final=read_json(path)['cleanup']
        self.assertEqual(final['state'],'CLEANED')
        self.assertFalse(completed & set(calls))
        self.assertEqual(final['pending_file_ids'],[])

    def test_collect_download_failure_resumes_same_job_without_upload(self):
        sent=self.send();path=self.root/sent['manifest_path'];docs=read_json(path)['documents']
        FakeGateway.downloads['output-1']=''.join(json.dumps({'custom_id':d['custom_id'],'response':{'body':{'pages':[{'index':0,'markdown':'fixture'}]}}})+'\n' for d in docs).encode()
        before=len(FakeGateway.uploads)
        with patch.object(FakeGateway,'download',side_effect=OSError('interrupted')):
            with self.assertRaises(OcrError):o.follow(str(self.root),sent['manifest_path'],True,FakeGateway)
        self.assertEqual(read_json(path)['state'],'COLLECT_FAILED')
        self.assertEqual(o.follow(str(self.root),sent['manifest_path'],True,FakeGateway)['status'],'ok')
        self.assertEqual(len(FakeGateway.uploads),before)

    def test_recovered_remote_job_model_mismatch_is_rejected(self):
        sent=self.send();FakeGateway.job.model='different-model'
        with self.assertRaises(OcrError):o.follow(str(self.root),sent['manifest_path'],False,FakeGateway)

    def test_originals_changed_after_partial_result_blocks_retry(self):
        parent=self.partial();source=next((self.root/'books'/self.identifier/'originals').rglob('0002.png'))
        source.write_bytes(source.read_bytes()+b'changed')
        with self.assertRaises(RegistrationError):r.retry(str(self.root),parent['manifest_path'],1,.01)

    def test_unsafe_unreferenced_asset_is_not_copied(self):
        parent=self.partial();child=self.retry_success(parent)
        path=self.root/child['manifest_path'];stem=read_json(path)['documents'][0]['output_stem']
        folder=path.parent/'documents'/f'{stem}.assets';folder.mkdir()
        outside=self.base/'private-data';outside.write_text('preserved')
        try:(folder/'unused').symlink_to(outside)
        except OSError:self.skipTest('symlink権限なし')
        with self.assertRaises((RegistrationError,OcrError)):r.merge(str(self.root),[parent['manifest_path'],child['manifest_path']],True)
        self.assertEqual(outside.read_text(),'preserved')

    def test_merge_does_not_overwrite_unexpected_interrupted_file(self):
        parent=self.partial();child=self.retry_success(parent)
        from signal_library.ocr import results as implementation
        with patch.object(implementation,'copy_new',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):r.merge(str(self.root),[parent['manifest_path'],child['manifest_path']],True)
        journal=next(self.root.rglob('.merge-plan.json'))
        extra=journal.parent/'unexpected.txt';extra.write_text('preserved')
        with self.assertRaises((RegistrationError,OcrError)):r.merge(str(self.root),[parent['manifest_path'],child['manifest_path']],True)
        self.assertEqual(extra.read_text(),'preserved')

    def test_interrupted_merge_resumes_without_overwrite(self):
        parent=self.partial();child=self.retry_success(parent)
        original=r.merge_runs
        from signal_library.ocr import results as implementation
        actual=implementation.copy_new;counter=0
        def fail(source,target,expected=None):
            nonlocal counter
            counter+=1
            if counter==2:raise OSError('copy failed')
            return actual(source,target,expected)
        with patch.object(implementation,'copy_new',side_effect=fail):
            with self.assertRaises(OSError):r.merge(str(self.root),[parent['manifest_path'],child['manifest_path']],True)
        merged=r.merge(str(self.root),[parent['manifest_path'],child['manifest_path']],True)
        self.assertTrue(results.validate_run((self.root/merged['manifest_path']).parent,True)['ok'])


if __name__=='__main__':unittest.main()

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from signal_library.reading import bundle
from signal_library import artifacts
from signal_library.registration import RegistrationError, hash_bytes


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.book = 'book-00000000-0000-0000-0000-000000000001'
        self.base = f'books/{self.book}'
        self.identity = dict(book_id=self.book, capture_id='capture-001', run_id='run1', kind='ocr', reading_id='reading-001')
        self.destination = self.base + '/reading/reading-001'
        self.source = self.root / self.base / 'ocr/run1/documents/0001.md'
        self.source.parent.mkdir(parents=True)
        self.original = self.root / self.base / 'originals/capture-001/0001.png'
        self.original.parent.mkdir(parents=True)
        self.original.write_bytes(b'original-image-fixture')
        self.asset = self.source.parent / '0001.assets/figure.png'
        self.asset.parent.mkdir()
        self.asset.write_bytes(b'figure-fixture')
        self.source.write_text('柱\n\n## 章\n\n日本語,本文の\n続き。\n\n![図1](0001.assets/figure.png)\n\n1',encoding='utf-8')
        sha = hash_bytes(self.original.read_bytes())
        self.pages = [dict(source_id='sha256:'+sha, sequence=1,
                           original_path=self.original.relative_to(self.root).as_posix(), original_sha256=sha,
                           markdown_path=self.source.relative_to(self.root).as_posix(), markdown_sha256=hash_bytes(self.source.read_bytes()))]
        self.plan = dict(schema_version='signal-library-curation-1', pages=[dict(source_id='sha256:'+sha,
                          expected_figures=1, expected_tables=0, header_footer_confirmed=True, paragraphs_confirmed=True,
                          remove=[dict(location='header',text='柱',reason='原典の柱'),dict(location='footer',text='1',reason='紙面番号')])])

    def publish(self, **kwargs):
        return bundle.publish_bundle(self.root,self.destination,self.identity,self.pages,kwargs.get('plan',self.plan))

    def test_portable_bundle_and_immutable_revision(self):
        before = {p:p.read_bytes() for p in (self.source,self.original,self.asset)}
        record = self.publish()
        self.assertEqual(record['format_status'],'complete')
        self.assertEqual(record['review_status'],'unreviewed')
        folder=self.root/self.destination
        text=(folder/'reading.md').read_text(encoding='utf-8')
        self.assertIn('日本語、本文の続き。',text)
        self.assertIn('assets/'+hash_bytes(b'figure-fixture')+'.png',text)
        self.assertNotIn(str(self.root),json.dumps(record))
        self.assertEqual(record,self.publish())
        import shutil
        moved=self.root/'copy'
        shutil.copytree(folder,moved)
        self.assertEqual(bundle.validate_bundle(moved),record)
        old={p.relative_to(folder):p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        identity={**self.identity,'kind':'curated','reading_id':'revision-001'}
        bundle.publish_bundle(self.root,self.base+'/curated/revision-001',identity,self.pages,self.plan)
        self.assertEqual(old,{p.relative_to(folder):p.read_bytes() for p in folder.rglob('*') if p.is_file()})
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)

    def test_each_completed_file_boundary_resumes(self):
        _,files,_=bundle.prepare_bundle(self.root,self.destination,self.identity,self.pages,self.plan)
        writer=artifacts.write_bytes_atomic
        # journalと各出力の公開直後に停止しても同じ計画だけ再開できる。
        for fail_after in range(1,len(files)+2):
            with self.subTest(fail_after=fail_after):
                import shutil
                shutil.rmtree(self.root/self.destination,ignore_errors=True)
                count=0
                def interrupted(path,data,**kwargs):
                    nonlocal count
                    writer(path,data,**kwargs)
                    count+=1
                    if count==fail_after:raise OSError('simulated process interruption')
                with patch.object(artifacts,'write_bytes_atomic',interrupted):
                    with self.assertRaises(OSError):self.publish()
                self.publish()
                bundle.validate_bundle(self.root/self.destination)

    def test_changed_inputs_plans_and_partial_files_refuse_resume(self):
        writer=artifacts.write_bytes_atomic
        def stop(path,data,**kwargs):
            writer(path,data,**kwargs)
            if Path(path).name==bundle.JOURNAL:raise OSError('stop')
        with patch.object(artifacts,'write_bytes_atomic',stop):
            with self.assertRaises(OSError):self.publish()
        with self.assertRaises(RegistrationError):self.publish(plan={})
        (self.root/self.destination/'unknown.txt').write_text('unknown')
        with self.assertRaises(RegistrationError):self.publish()
        (self.root/self.destination/'unknown.txt').unlink()
        self.asset.write_bytes(b'changed image')
        with self.assertRaises(RegistrationError):self.publish()
        self.source.write_text('changed OCR',encoding='utf-8')
        with self.assertRaises(RegistrationError):self.publish()

    def test_existing_output_tamper_and_unsafe_images_are_rejected(self):
        self.publish()
        (self.root/self.destination/'reading.md').write_text('tampered',encoding='utf-8')
        with self.assertRaises(RegistrationError):self.publish()
        for image in ('https://example.test/a.png','../escape.png','data:image/png;base64,abc'):
            self.source.write_text('![図]('+image+')',encoding='utf-8')
            self.pages[0]['markdown_sha256']=hash_bytes(self.source.read_bytes())
            with self.assertRaises(RegistrationError):bundle.prepare_bundle(self.root,self.destination,self.identity,self.pages,self.plan)

    def test_code_example_is_not_a_figure_and_unknown_folder_is_not_adopted(self):
        self.source.write_text('本文\n\n```md\n![例](missing.png)\n```',encoding='utf-8')
        self.pages[0]['markdown_sha256']=hash_bytes(self.source.read_bytes())
        plan={'schema_version':'signal-library-curation-1','pages':[{
            'source_id':self.pages[0]['source_id'],'expected_figures':0,'expected_tables':0,
            'header_footer_confirmed':True,'paragraphs_confirmed':True}]}
        record=self.publish(plan=plan)
        self.assertEqual(record['format_status'],'complete')
        self.assertFalse(any(f['path'].startswith('assets/') for f in record['files']))
        self.assertIn('![例](missing.png)',(self.root/self.destination/'reading.md').read_text(encoding='utf-8'))
        self.destination=self.base+'/reading/reading-002'
        self.identity['reading_id']='reading-002'
        (self.root/self.destination).mkdir()
        with self.assertRaises(RegistrationError):self.publish(plan=plan)

    def test_source_map_has_to_cover_body_even_with_updated_file_hash(self):
        self.publish()
        folder=self.root/self.destination
        path=folder/'source-map.json'
        value=json.loads(path.read_text(encoding='utf-8'))
        value.pop()
        path.write_text(json.dumps(value),encoding='utf-8')
        manifest=json.loads((folder/'bundle.json').read_text(encoding='utf-8'))
        for entry in manifest['files']:
            if entry['path']=='source-map.json':entry['sha256']=hash_bytes(path.read_bytes())
        (folder/'bundle.json').write_text(json.dumps(manifest),encoding='utf-8')
        with self.assertRaises(RegistrationError):bundle.validate_bundle(folder)


if __name__=='__main__':unittest.main()

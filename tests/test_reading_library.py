import contextlib
import io
import json
import unittest
from unittest.mock import patch

import test_ocr_library as fixture
import test_ocr_recovery as recovery_fixture
from signal_library.cli import main
from signal_library.ocr import library as o
from signal_library.reading import library as reading
from signal_library.reading import review
from signal_library.registration import RegistrationError, inventory, read_json


class ReadingLibraryTests(unittest.TestCase):
    def setUp(self):
        fixture.OcrLibraryTests.setUp(self)
        plan=o.plan_ocr(str(self.root),self.identifier,'capture-001')
        sent=o.submit(str(self.root),self.identifier,'capture-001',plan['plan_hash'],True,gateway_factory=fixture.FakeGateway)
        self.manifest=sent['manifest_path']
        custom=read_json(self.root/self.manifest)['documents'][0]['custom_id']
        fixture.FakeGateway.downloads['output-1']=(json.dumps({'custom_id':custom,'response':{'body':{'pages':[
            {'index':0,'markdown':'柱\n\n## 第1章\n\n日本語,本文の\n続き。\n\n1'}]}}})+'\n').encode()
        o.follow(str(self.root),self.manifest,True,fixture.FakeGateway)
        source=inventory(self.root)[0][self.identifier]['originals'][0]['source_id']
        self.curation=self.base/'curation.json'
        self.data={'schema_version':'signal-library-curation-1','pages':[{
            'source_id':source,'expected_figures':0,'expected_tables':0,
            'header_footer_confirmed':True,'paragraphs_confirmed':True,
            'remove':[{'location':'header','text':'柱','reason':'原典の柱'},
                      {'location':'footer','text':'1','reason':'紙面番号'}]}]}
        self.curation.write_text(json.dumps(self.data),encoding='utf-8')

    def build(self, **kwargs):
        return reading.build(str(self.root),self.manifest,'reading-001',plan_path=self.curation,**kwargs)

    def test_plan_apply_revision_and_cli_preserve_inputs(self):
        before={p:p.read_bytes() for p in (self.root/'books'/self.identifier).rglob('*') if p.is_file()}
        planned=self.build()
        self.assertFalse(planned['files_written'])
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)
        result=self.build(apply=True)
        book=inventory(self.root)[0][self.identifier]
        self.assertEqual(len(book['reading_bundles']),1)
        self.assertIsNone(book['active_reading']['kind'])
        self.assertEqual(self.build(apply=True)['bundle_path'],result['bundle_path'])
        self.data['pages'][0]['replacements']=[{'before':'続き。','after':'続きです。','reason':'人工fixtureの修正試験'}]
        self.curation.write_text(json.dumps(self.data),encoding='utf-8')
        revision=reading.build(str(self.root),self.manifest,'revision-001','curated',self.curation,True,result['bundle_path'])
        book=inventory(self.root)[0][self.identifier]
        self.assertEqual(book['curated_revisions'][0]['revision_id'],'revision-001')
        self.assertIsNone(book['curated_revisions'][0]['verified_at'])
        self.assertEqual(read_json(self.root/revision['bundle_path'])['identity']['supersedes']['bundle_path'],result['bundle_path'])
        self.assertIn('続きです。',(self.root/revision['bundle_path']).parent.joinpath('reading.md').read_text(encoding='utf-8'))
        self.assertIn('続き。',(self.root/result['bundle_path']).parent.joinpath('reading.md').read_text(encoding='utf-8'))
        for p,data in before.items():
            if p.name!='book.json':self.assertEqual(p.read_bytes(),data)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code=main(['reading','build','--root',str(self.root),'--manifest',self.manifest,
                       '--reading-id','reading-002','--plan',str(self.curation),'--json'])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue())['format_status'],'complete')

    def test_metadata_publish_interruption_and_tampered_journal(self):
        real=reading.publish
        calls=0
        def interrupted(root,entry):
            nonlocal calls
            real(root,entry)
            calls+=1
            if calls==1:raise OSError('stop after book metadata')
        with patch.object(reading,'publish',interrupted):
            with self.assertRaises(OSError):self.build(apply=True)
        journal=self.root/f'books/{self.identifier}/{reading.INDEX_JOURNAL}'
        self.assertTrue(journal.is_file())
        with self.assertRaises(RegistrationError):self.build()
        raw=journal.read_bytes()
        value=read_json(journal);value['files'][0]['text']='tampered'
        journal.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaises(RegistrationError):self.build(apply=True)
        journal.write_bytes(raw)
        self.build(apply=True)
        self.assertFalse(journal.exists())
        self.assertEqual(len(inventory(self.root)[0][self.identifier]['reading_bundles']),1)

    def test_table_or_figure_confirmation_is_not_original_verification(self):
        result=reading.build(str(self.root),self.manifest,'incomplete',apply=True)
        self.assertEqual(result['format_status'],'needs_review')
        self.assertEqual(result['review_status'],'unreviewed')
        record=inventory(self.root)[0][self.identifier]['reading_bundles'][0]
        (self.root/record['markdown_path']).write_text('changed',encoding='utf-8')
        with self.assertRaises(RegistrationError):inventory(self.root)

    def review_plan(self, source_id, **changes):
        value={'schema_version':'signal-library-reading-review-1','review_id':'check-001',
               'reviewer':'人工fixtureの照合者','checked_at':'2026-10-16T12:00:00+09:00','notes':'比較範囲の記録',
               'ranges':[{'source_id':source_id,'file_page':1,'region':'paragraph-1','scope':'text',
                          'status':'verified','compared_with_original':True,'notes':'本文だけ比較'}]}
        value.update(changes)
        path=self.base/'review-plan.json';path.write_text(json.dumps(value),encoding='utf-8')
        return path

    def test_partial_review_is_version_bound_and_adoption_is_separate(self):
        built=self.build(apply=True)
        bundle=built['bundle_path']
        source=inventory(self.root)[0][self.identifier]['originals'][0]['source_id']
        plan=self.review_plan(source)
        before=(self.root/bundle).read_bytes()
        planned=review.change(str(self.root),bundle,plan)
        self.assertFalse(planned['files_written'])
        result=review.change(str(self.root),bundle,plan,True)
        self.assertEqual(result['state'],'reading_review_recorded')
        book=inventory(self.root)[0][self.identifier]
        self.assertIsNone(book['active_reading']['kind'])
        self.assertEqual(book['reading_bundles'][0]['review_status'],'unreviewed')
        self.assertEqual(len(book['reading_reviews']),1)
        csv_before=(self.root/f'books/{self.identifier}/review.csv').read_bytes()
        review.change(str(self.root),bundle,plan,True)
        self.assertEqual(csv_before,(self.root/f'books/{self.identifier}/review.csv').read_bytes())
        adopted=review.change(str(self.root),bundle,apply=True,adopt=True)
        self.assertEqual(adopted['review_status'],'range_records')
        self.assertEqual(inventory(self.root)[0][self.identifier]['active_reading']['bundle_path'],bundle)
        revision=reading.build(str(self.root),self.manifest,'revision-001','curated',self.curation,True,bundle)
        newer=review.change(str(self.root),revision['bundle_path'],apply=True,adopt=True)
        self.assertEqual(newer['review_status'],'unreviewed')
        self.assertEqual((self.root/bundle).read_bytes(),before)
        self.assertEqual(len(inventory(self.root)[0][self.identifier]['reading_reviews']),1)
        import csv
        csv_path=self.root/f'books/{self.identifier}/review.csv'
        with csv_path.open(encoding='utf-8',newline='') as stream:
            rows=list(csv.DictReader(stream))
        fake={**rows[0],'status':'needs_review'}
        with csv_path.open('a',encoding='utf-8',newline='') as stream:
            csv.DictWriter(stream,fieldnames=review.REVIEW_FIELDS,lineterminator='\n').writerow(fake)
        with self.assertRaises(RegistrationError):inventory(self.root)
        # CSVの改変をJSON側の照合済み記録で検出する。
        (self.root/f'books/{self.identifier}/review.csv').write_text(','.join(review.REVIEW_FIELDS)+'\n',encoding='utf-8')
        with self.assertRaises(RegistrationError):inventory(self.root)

    def test_unconfirmed_review_and_incomplete_adoption_are_rejected(self):
        built=self.build(apply=True)
        source=inventory(self.root)[0][self.identifier]['originals'][0]['source_id']
        plan=self.review_plan(source)
        value=read_json(plan);value['ranges'][0]['compared_with_original']=False
        plan.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaises(RegistrationError):review.change(str(self.root),built['bundle_path'],plan,True)
        value['ranges'][0]['compared_with_original']=True;value['ranges'][0]['source_id']='unknown'
        plan.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaises(RegistrationError):review.change(str(self.root),built['bundle_path'],plan,True)
        incomplete=reading.build(str(self.root),self.manifest,'incomplete',apply=True)
        with self.assertRaises(RegistrationError):review.change(str(self.root),incomplete['bundle_path'],apply=True,adopt=True)

    def test_review_and_adoption_metadata_interruptions_resume(self):
        built=self.build(apply=True);bundle=built['bundle_path']
        source=inventory(self.root)[0][self.identifier]['originals'][0]['source_id']
        plan=self.review_plan(source)
        real=review.publish
        def interrupted(root,entry):
            real(root,entry)
            if entry['path'].endswith('book.json'):raise OSError('stop after metadata')
        with patch.object(review,'publish',interrupted):
            with self.assertRaises(OSError):review.change(str(self.root),bundle,plan,True)
        review.change(str(self.root),bundle,plan,True)
        self.assertEqual(len(inventory(self.root)[0][self.identifier]['reading_reviews']),1)
        with patch.object(review,'publish',interrupted):
            with self.assertRaises(OSError):review.change(str(self.root),bundle,apply=True,adopt=True)
        review.change(str(self.root),bundle,apply=True,adopt=True)
        self.assertEqual(inventory(self.root)[0][self.identifier]['active_reading']['bundle_path'],bundle)

    def test_partial_retry_requires_merge_and_combined_run_can_be_integrated(self):
        fixture2=recovery_fixture.RecoveryTests('test_partial_retry_merge_preserves_ids_originals_and_history')
        self.addCleanup(fixture2.doCleanups)
        fixture2.setUp()
        parent=fixture2.partial();child=fixture2.retry_success(parent)
        with self.assertRaises(RegistrationError):
            reading.build(str(fixture2.root),child['manifest_path'],'partial',apply=True)
        merged=recovery_fixture.r.merge(str(fixture2.root),[parent['manifest_path'],child['manifest_path']],True)
        before={p:p.read_bytes() for p in (fixture2.root/'books'/fixture2.identifier/'ocr').rglob('*') if p.is_file()}
        result=reading.build(str(fixture2.root),merged['manifest_path'],'combined',apply=True)
        record=read_json(fixture2.root/result['bundle_path'])
        self.assertEqual(len(record['sources']),2)
        self.assertEqual(record['identity']['run_id'],read_json(fixture2.root/merged['manifest_path'])['run_id'])
        self.assertTrue((fixture2.root/result['bundle_path']).parent.joinpath('source-manifest.json').is_file())
        for path,data in before.items():self.assertEqual(path.read_bytes(),data)


if __name__=='__main__':unittest.main()

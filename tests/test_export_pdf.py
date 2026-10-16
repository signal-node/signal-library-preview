import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from signal_library import artifacts
from signal_library.cli import main
from signal_library.exports import pdf, html, checks, lifecycle, engine
from signal_library.exports.typst import Typst
from signal_library.registration import RegistrationError, register, inventory, encode, row_for, catalog_text
from signal_library.initialization import initialize
import test_export_html as html_tests
from test_export_html import FakeEngine


class FakePdfEngine(FakeEngine):
    def typst(self,ast):return '検証用の模擬Typst本文\n'


class FakeTypst:
    version='0.15.1'
    def compile(self,files,timestamp):return b'%PDF-1.7\nartificial test boundary only\n%%EOF\n'


class PdfTests(unittest.TestCase):
    def setUp(self):
        html_tests.ExportTests.setUp(self)
        patch.object(pdf,'Pandoc',FakePdfEngine).start()
        patch.object(pdf,'Typst',FakeTypst).start()

    def run_export(self,export_id='pdf-001',**kwargs):
        return pdf.export_pdf(str(self.root),self.identifier,export_id,self.font_dir,markdown_id=self.source_id,**kwargs)

    def plan(self,check_id='pdf-check-001',results=None):
        path=self.base/'pdf-check.json'
        value={'schema_version':'signal-library-pdf-check-1','check_id':check_id,'checked_at':'2026-10-16T15:00:00+09:00',
               'reviewer':'人工試験者','method':'pdf_page_images','viewer':{'name':'模擬表示','version':'unknown','os':'unknown'},
               'results':results or {k:'not_checked' for k in checks.PDF_CHECKS},'notes':'模擬記録。実表示の確認ではない。'}
        path.write_text(json.dumps(value),encoding='utf-8');return path

    def test_pdf_plan_apply_copy_repetition_and_cross_format_id(self):
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=self.run_export();self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        result=self.run_export(apply=True);folder=(self.root/result['manifest_path']).parent
        self.assertEqual(pdf.validate(folder)['format'],'pdf')
        original={p.relative_to(folder):p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        self.run_export(apply=True)
        self.assertEqual(original,{p.relative_to(folder):p.read_bytes() for p in folder.rglob('*') if p.is_file()})
        copied=self.base/'日本語 コピー先';shutil.copytree(folder,copied);pdf.validate(copied)
        self.assertEqual(checks.inspect(str(self.root),self.identifier)['exports'][0]['freshness'],'current')
        with self.assertRaises(RegistrationError):
            html.export_html(str(self.root),self.identifier,'pdf-001',self.font_dir,markdown_id=self.source_id,apply=True)
        self.assertFalse((self.root/f'books/{self.identifier}/exports/html/pdf-001').exists())
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code=main(['export','pdf','--root',str(self.root),'--book-id',self.identifier,'--export-id','cli-plan',
                       '--markdown-id',self.source_id,'--font-dir',str(self.font_dir),'--json'])
        self.assertEqual(code,0);self.assertEqual(json.loads(stdout.getvalue())['state'],'pdf_planned')

    def test_legacy_pdf_profile_and_japanese_new_output(self):
        folder=self.root/f'books/{self.identifier}/exports/pdf/pdf-001';folder.mkdir(parents=True)
        (folder/html.JOURNAL).write_text(json.dumps({'context':{'created_at':'2026-10-16T12:00:00+09:00','policy':'safe-pdf-1'}}),encoding='utf-8')
        _,_,_,tx=pdf.prepare(self.root,self.identifier,'pdf-001',self.font_dir,markdown_id=self.source_id)
        (folder/html.JOURNAL).write_text(json.dumps(tx),encoding='utf-8');self.run_export(apply=True)
        snapshot={p:p.read_bytes() for p in folder.rglob('*') if p.is_file()};self.run_export(apply=True)
        self.assertEqual(snapshot,{p:p.read_bytes() for p in snapshot});pdf.validate(folder)
        self.assertEqual(checks.inspect(str(self.root),self.identifier,'pdf-001')['exports'][0]['stale_reasons'],['renderer_settings'])
        asts=[]
        def writer(ast):asts.append(ast);return '模擬本文'
        with patch.object(FakePdfEngine,'typst',side_effect=writer):made=self.run_export('pdf-ja',apply=True)
        label=asts[0]['blocks'][1]['c'][0]['c']
        self.assertIn('使用版: 登録Markdown',label);self.assertIn('原典照合: 未照合',label)
        self.assertNotIn('unreviewed',label);self.assertEqual(pdf.validate((self.root/made['manifest_path']).parent)['settings']['policy'],'safe-pdf-2')

    def test_pdf_interrupted_publication_resumes_exact_bytes_and_rejects_tamper(self):
        writer=artifacts.write_bytes_atomic
        def stop(path,data,**kwargs):
            writer(path,data,**kwargs)
            if Path(path).name=='book.pdf':raise OSError('interrupt')
        with patch.object(artifacts,'write_bytes_atomic',stop):
            with self.assertRaises(OSError):self.run_export(apply=True)
        self.run_export(apply=True)
        folder=self.root/f'books/{self.identifier}/exports/pdf/pdf-001'
        (folder/'book.pdf').write_bytes(b'%PDF-wrong\n%%EOF')
        with self.assertRaises(RegistrationError):self.run_export(apply=True)

    def test_pdf_check_schema_partial_and_new_output_has_no_inherited_check(self):
        result=self.run_export(apply=True);manifest=self.root/result['manifest_path'];before=manifest.read_bytes()
        checks.review(str(self.root),result['manifest_path'],self.plan(),True)
        self.assertEqual(checks.inspect(str(self.root),self.identifier)['exports'][0]['visual_status'],'partial')
        checks.review(str(self.root),result['manifest_path'],self.plan('pdf-check-002',{k:'passed' for k in checks.PDF_CHECKS}),True)
        self.assertEqual(self.run_export(apply=True)['visual_status'],'passed')
        new=lifecycle.regenerate(str(self.root),self.identifier,'pdf-001','pdf-002',self.font_dir,apply=True)
        self.assertEqual(new['visual_status'],'pending');self.assertEqual(manifest.read_bytes(),before)
        with self.assertRaises(RegistrationError):
            lifecycle.regenerate(str(self.root),self.identifier,'pdf-001','pdf-001',self.font_dir,apply=True)
        wrong=html_tests.ExportTests.check_plan(self)
        with self.assertRaises(RegistrationError):checks.review(str(self.root),result['manifest_path'],wrong,True)

    def test_changed_md_and_asset_snapshot_stale_and_regenerate_preserves_old(self):
        old=self.run_export(apply=True);old_path=self.root/old['output_path'];before=old_path.read_bytes()
        (self.input/'本文.md').write_text('## 新しい章\n\n![図](assets/図.svg)\n\n新版',encoding='utf-8')
        (self.input/'assets/図.svg').write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><circle cx="40" cy="40" r="30"/></svg>')
        plan=self.input/'next.json';plan.write_text(json.dumps({'schema_version':'signal-library-registration-1',
            'book_id':self.identifier,'markdown':[{'path':'本文.md','provenance':'人工新版','assets':['assets/図.svg']}]}),encoding='utf-8')
        register(str(self.root),plan,True)
        newer=inventory(self.root)[0][self.identifier]['markdown_sources'][-1]['source_id']
        status=checks.inspect(str(self.root),self.identifier,'pdf-001',markdown_id=newer)['exports'][0]
        self.assertEqual(status['freshness'],'stale')
        self.assertTrue({'source_version','source_content','source_assets'}<=set(status['stale_reasons']))
        made=lifecycle.regenerate(str(self.root),self.identifier,'pdf-001','pdf-002',self.font_dir,markdown_id=newer,apply=True)
        self.assertEqual(made['source']['id'],newer);self.assertEqual(old_path.read_bytes(),before)
        self.assertEqual(checks.inspect(str(self.root),self.identifier,'pdf-002',markdown_id=newer)['exports'][0]['freshness'],'current')

    def test_adopted_input_comparison_and_integrity_error_are_distinct(self):
        result=self.run_export(apply=True)
        book_path=self.root/f'books/{self.identifier}/book.json'
        book=inventory(self.root)[0][self.identifier]
        source=book['markdown_sources'][0]
        book['active_reading']={'kind':'markdown','id':source['source_id'],'path':source['path']}
        book_path.write_text(encode(book),encoding='utf-8')
        (self.root/'catalog.csv').write_text(catalog_text([row_for(book)]),encoding='utf-8')
        state=checks.inspect(str(self.root),self.identifier,'pdf-001')['exports'][0]
        self.assertEqual(state['comparison_basis'],'active_reading')
        self.assertEqual(state['freshness'],'current')
        (self.root/source['path']).write_text('改変された原本',encoding='utf-8')
        with self.assertRaises(RegistrationError):checks.inspect(str(self.root),self.identifier)

    def test_old_html_profile_is_stale_without_rewriting_accepted_output(self):
        folder=self.root/f'books/{self.identifier}/exports/html/html-old';folder.mkdir(parents=True)
        (folder/html.JOURNAL).write_text(json.dumps({'context':{'created_at':'2026-10-16T12:00:00+09:00'}}),encoding='utf-8')
        _,_,_,transaction=html.prepare(self.root,self.identifier,'html-old',self.font_dir,markdown_id=self.source_id)
        (folder/html.JOURNAL).write_text(json.dumps(transaction),encoding='utf-8')
        html.export_html(str(self.root),self.identifier,'html-old',self.font_dir,markdown_id=self.source_id,apply=True)
        old=(folder/'index.html').read_bytes()
        state=checks.inspect(str(self.root),self.identifier,'html-old')['exports'][0]
        self.assertEqual(state['stale_reasons'],['renderer_settings'])
        result=lifecycle.regenerate(str(self.root),self.identifier,'html-old','html-new',self.font_dir,apply=True)
        self.assertEqual(result['visual_status'],'pending');self.assertEqual((folder/'index.html').read_bytes(),old)
        self.assertEqual(checks.inspect(str(self.root),self.identifier,'html-new')['exports'][0]['freshness'],'current')


@unittest.skipUnless(os.environ.get('SIGNAL_LIBRARY_TEST_FONT_DIR'),'固定fontでの実PDF検証は専用CIで必須実行')
class RealPdfTests(unittest.TestCase):
    def test_real_engines_local_font_long_table_and_literal_typst_are_safe(self):
        # テスト専用pypdf。製品実行には要求しない。
        from pypdf import PdfReader
        # 任意Typstを直接受け付ける入口はないが、root外読込みも組版器で拒否する。
        with self.assertRaises(RegistrationError):
            Typst().compile({'source.typ':b'#read("../outside-secret.txt")'},0)
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory).resolve();root=base/'Books';repo=base/'repo';repo.mkdir()
            initialize(str(root),True,False,repo)
            source=base/'日本語 空白';source.mkdir()
            sample=Path(__file__).resolve().parents[1]/'tools/engine-probe/sample.md'
            table='| 番号 | 日本語の説明 | 数値 |\n| --- | --- | --- |\n'+'\n'.join(f'| {i} | 人工資料の第{i}行です。 | {i} |' for i in range(1,81))
            text=sample.read_text(encoding='utf-8').replace('<!--LONG_TABLE-->',table)
            text+='\n\nTypst命令も本文です。#read("/outside-secret.txt") #import "@preview/x:1.0.0"\n'
            (source/'本文.md').write_text(text,encoding='utf-8')
            (source/'figure.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100"><rect width="400" height="100" fill="#eee"/></svg>',encoding='utf-8')
            plan=source/'plan.json';plan.write_text(json.dumps({'schema_version':'signal-library-registration-1','metadata':{'title':'PDF人工資料'},
                'markdown':[{'path':'本文.md','provenance':'人工試験','assets':['figure.svg']}]}),encoding='utf-8')
            bid=register(str(root),plan,True)['book_id'];sid=inventory(root)[0][bid]['markdown_sources'][0]['source_id']
            result=pdf.export_pdf(str(root),bid,'pdf-real',os.environ['SIGNAL_LIBRARY_TEST_FONT_DIR'],markdown_id=sid,apply=True)
            path=root/result['output_path'];before=path.read_bytes()
            pdf.export_pdf(str(root),bid,'pdf-real',os.environ['SIGNAL_LIBRARY_TEST_FONT_DIR'],markdown_id=sid,apply=True)
            self.assertEqual(path.read_bytes(),before)
            doc=PdfReader(path);joined=''.join(''.join(p.extract_text().split()) for p in doc.pages)
            self.assertGreaterEqual(len(doc.pages),3)
            self.assertIn('第80行',joined);self.assertIn('最終行の日本語',joined);self.assertIn('#read',joined)
            self.assertNotIn('title:日本語',joined)
            self.assertGreaterEqual(len(doc.outline),4)
            self.assertFalse(doc.trailer['/Root'].get('/OpenAction'))
            for page in doc.pages:
                self.assertAlmostEqual(float(page.mediabox.width),595.28,delta=1)
                self.assertAlmostEqual(float(page.mediabox.height),841.89,delta=1)
                for ref in page['/Resources']['/Font'].values():
                    font=ref.get_object();descendants=font.get('/DescendantFonts')
                    if descendants:font=descendants[0].get_object()
                    descriptor=font['/FontDescriptor'].get_object()
                    self.assertTrue(any(k in descriptor for k in ('/FontFile','/FontFile2','/FontFile3')))
            copy=base/'コピー先 空白';shutil.copytree(path.parent,copy);pdf.validate(copy)

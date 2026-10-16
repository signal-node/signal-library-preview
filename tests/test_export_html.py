import contextlib
import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from signal_library import artifacts
from signal_library.cli import main
from signal_library.initialization import initialize
from signal_library.registration import RegistrationError, register, inventory, hash_bytes
from signal_library.exports import engine, html as export
from signal_library.exports import fonts, library as export_library
from signal_library.exports import checks as export_checks
from signal_library.exports.images import validate_svg


SVG=b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect x="1" y="1" width="98" height="48" fill="#eee"/></svg>'


class FakeEngine:
    version='3.12.1'  # 保存境界試験用の模擬応答。実エンジン試験は別method。
    def __init__(self,reader=None):self.reader=reader
    def parse(self,text):
        return {'pandoc-api-version':[1,23,1],'meta':{},'blocks':[
            {'t':'Header','c':[2,['chapter',[],[]],[{'t':'Str','c':'章'}]]},
            {'t':'Para','c':[{'t':'Image','c':[['',[],[]],[{'t':'Str','c':'図'}],['assets/図.svg','']]}]}]}
    def html(self,ast):
        target=ast['blocks'][1]['c'][0]['c'][2][0]
        return '<h2 id="chapter">章</h2><p><img src="'+target+'" alt="図"></p>'


def has_supported_pandoc():
    try:engine.Pandoc();return True
    except (RegistrationError,OSError):return False


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.root=self.base/'Books';repo=self.base/'repo';repo.mkdir()
        initialize(str(self.root),True,False,repo)
        self.input=self.base/'input';self.input.mkdir();(self.input/'assets').mkdir()
        (self.input/'assets/図.svg').write_bytes(SVG)
        (self.input/'本文.md').write_text('## 章\n\n![図](assets/図.svg)\n\n式 $f(x,y)$。',encoding='utf-8')
        plan=self.input/'plan.json'
        plan.write_text(json.dumps({'schema_version':'signal-library-registration-1','metadata':{'title':'人工HTML検証'},
            'markdown':[{'path':'本文.md','provenance':'人工fixture','assets':['assets/図.svg']}]}),encoding='utf-8')
        self.identifier=register(str(self.root),plan,True)['book_id']
        self.source_id=inventory(self.root)[0][self.identifier]['markdown_sources'][0]['source_id']
        self.font_dir=self.base/'font';self.font_dir.mkdir()
        (self.font_dir/export.FONT).write_bytes(b'artificial-font')
        (self.font_dir/export.LICENSE).write_bytes(b'artificial-license')
        self.addCleanup(patch.stopall)
        patch.object(export,'FONT_HASH',hash_bytes(b'artificial-font')).start()
        patch.object(export,'LICENSE_HASH',hash_bytes(b'artificial-license')).start()
        patch.object(export,'Pandoc',FakeEngine).start()

    def run_export(self,**kwargs):
        return export.export_html(str(self.root),self.identifier,'html-001',self.font_dir,markdown_id=self.source_id,**kwargs)

    def test_plan_apply_copy_and_old_sources_are_immutable(self):
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        planned=self.run_export()
        self.assertFalse(planned['files_written'])
        self.assertFalse((self.root/planned['manifest_path']).exists())
        result=self.run_export(apply=True)
        path=self.root/result['manifest_path'];folder=path.parent
        manifest=export.validate(folder)
        self.assertEqual(manifest['validation']['visual'],'pending')
        self.assertEqual(manifest['source']['kind'],'markdown')
        self.assertNotIn('run_id',manifest['source'])
        rendered=(folder/'index.html').read_text(encoding='utf-8')
        self.assertIn('Content-Security-Policy',rendered)
        self.assertIn('script-src \'none\'',rendered)
        self.assertNotIn('assets/図.svg',rendered)
        original=path.read_bytes();self.run_export(apply=True);self.assertEqual(path.read_bytes(),original)
        for p,data in before.items():
            if p.name not in {'book.json','exports.csv'}:self.assertEqual(p.read_bytes(),data)
        book=inventory(self.root)[0][self.identifier]
        self.assertEqual(len(book['local_exports']),1)
        moved=self.base/'コピー先 空白';shutil.copytree(folder,moved)
        self.assertEqual(export.validate(moved),manifest)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result=main(['export','html','--root',str(self.root),'--book-id',self.identifier,'--export-id','html-002',
                         '--markdown-id',self.source_id,'--font-dir',str(self.font_dir),'--json'])
        self.assertEqual(result,0)
        self.assertEqual(json.loads(output.getvalue())['state'],'html_planned')

    def test_publication_interruptions_resume_and_input_changes_refuse(self):
        writer=artifacts.write_bytes_atomic
        count=0
        def stop(path,data,**kwargs):
            nonlocal count
            writer(path,data,**kwargs);count+=1
            if count==3:raise OSError('stop')
        with patch.object(artifacts,'write_bytes_atomic',stop):
            with self.assertRaises(OSError):self.run_export(apply=True)
        self.run_export(apply=True)
        path=self.root/f'books/{self.identifier}/exports/html/html-001/index.html'
        path.write_text('tampered',encoding='utf-8')
        with self.assertRaises(RegistrationError):self.run_export(apply=True)

    def test_undeclared_images_bad_font_and_missing_input_refuse(self):
        class BadEngine(FakeEngine):
            def parse(self,text):
                ast=super().parse(text);ast['blocks'][1]['c'][0]['c'][2][0]='https://example.test/a.svg'
                return ast
        with patch.object(export,'Pandoc',BadEngine):
            with self.assertRaises(RegistrationError):self.run_export(apply=True)
        (self.font_dir/export.FONT).write_bytes(b'wrong')
        with self.assertRaises(RegistrationError):self.run_export(apply=True)
        with self.assertRaises(RegistrationError):export.export_html(str(self.root),self.identifier,'a',self.font_dir)

    def test_font_plan_offline_install_and_cached_execution_do_not_fetch(self):
        def no_fetch(url):raise AssertionError('unexpected network')
        result=fonts.install(str(self.root),apply=False,downloader=no_fetch)
        self.assertFalse(result['files_written']);self.assertFalse(result['network_used'])
        with self.assertRaises(RegistrationError):fonts.install(str(self.root),'',True,downloader=no_fetch)
        result=fonts.install(str(self.root),self.font_dir,True,downloader=no_fetch)
        self.assertEqual(result['state'],'font_installed')
        self.assertFalse(result['network_used'])
        result=fonts.install(str(self.root),apply=True,downloader=no_fetch)
        self.assertEqual(result['state'],'font_ready')
        self.assertFalse(result['files_written'])
        # 導入後は個人のfontディレクトリ指定を省略して出力できる。
        result=export.export_html(str(self.root),self.identifier,'cached',markdown_id=self.source_id,apply=True)
        self.assertEqual(result['state'],'html_created')
        with self.assertRaises(RegistrationError):
            export.export_html(str(self.root),self.identifier,'wrong-input',bundle_path='',apply=True)

    def test_font_download_checks_both_hashes_and_resumes_publication(self):
        with self.assertRaises(RegistrationError):fonts.install(str(self.root),apply=True,downloader=lambda url:b'wrong')
        self.assertFalse((self.root/fonts.CACHE).exists())
        calls=[]
        def download(url):
            calls.append(url)
            return b'artificial-font' if url==fonts.URLS[export.FONT] else b'artificial-license'
        writer=artifacts.write_bytes_atomic
        def interrupted(path,data,**kwargs):
            writer(path,data,**kwargs)
            if Path(path).name==export.FONT:raise OSError('stop after font')
        with patch.object(artifacts,'write_bytes_atomic',interrupted):
            with self.assertRaises(OSError):fonts.install(str(self.root),apply=True,downloader=download)
        result=fonts.install(str(self.root),apply=True,downloader=download)
        self.assertTrue(result['network_used'])
        fonts.validate(self.root/fonts.CACHE)
        self.assertEqual(set(calls),set(fonts.URLS.values()))

    def test_output_index_interruption_tamper_and_duplicate_csv_are_rejected(self):
        publish=export_library.publish
        def stop(root,entry):
            publish(root,entry)
            if entry['path'].endswith('book.json'):raise OSError('stop after book metadata')
        with patch.object(export_library,'publish',stop):
            with self.assertRaises(OSError):self.run_export(apply=True)
        journal=self.root/f'books/{self.identifier}/{export_library.JOURNAL}'
        raw=journal.read_bytes()
        with self.assertRaises(RegistrationError):self.run_export()
        value=json.loads(raw);value['files'][2]['text']='tampered'
        journal.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaises(RegistrationError):self.run_export(apply=True)
        journal.write_bytes(raw)
        self.run_export(apply=True)
        self.assertFalse(journal.exists())
        book=inventory(self.root)[0][self.identifier]
        self.assertEqual(len(book['local_exports']),1)
        csv_path=self.root/book['exports_path']
        import csv
        with csv_path.open(encoding='utf-8',newline='') as stream:rows=list(csv.DictReader(stream))
        self.assertEqual(rows[0]['status'],'generated_unverified')
        with csv_path.open('a',encoding='utf-8',newline='') as stream:
            csv.DictWriter(stream,fieldnames=export_library.FIELDS,lineterminator='\n').writerow(rows[0])
        with self.assertRaises(RegistrationError):inventory(self.root)

    def check_plan(self,**changes):
        value={'schema_version':'signal-library-export-check-1','check_id':'check-001','checked_at':'2026-10-16T12:00:00+09:00',
               'reviewer':'人工fixtureの照合者','method':'user_report',
               'browser':{'name':'人工browser','version':'unknown','os':'人工OS'},
               'results':{k:'not_checked' for k in export_checks.CHECKS},'notes':'表示確認記録の人工試験、実表示の証拠ではない'}
        value.update(changes)
        path=self.base/'check.json';path.write_text(json.dumps(value),encoding='utf-8')
        return path

    def test_visual_checks_do_not_claim_unchecked_or_new_outputs_are_verified(self):
        result=self.run_export(apply=True);manifest=result['manifest_path']
        before=(self.root/manifest).read_bytes()
        csv_path=self.root/inventory(self.root)[0][self.identifier]['exports_path'];csv_before=csv_path.read_bytes()
        plan=self.check_plan(results={k:('not_checked' if k in {'offline','copied_bundle'} else 'passed') for k in export_checks.CHECKS})
        checked=export_checks.review(str(self.root),manifest,plan,True)
        self.assertEqual(checked['visual_status'],'partial')
        self.assertEqual(export_checks.inspect(str(self.root),self.identifier)['exports'][0]['visual_status'],'partial')
        plan=self.check_plan(check_id='check-002',results={k:'passed' for k in export_checks.CHECKS})
        export_checks.review(str(self.root),manifest,plan,True)
        self.assertEqual(export_checks.inspect(str(self.root),self.identifier)['exports'][0]['visual_status'],'passed')
        self.assertEqual(self.run_export(apply=True)['visual_status'],'passed')
        self.assertEqual((self.root/manifest).read_bytes(),before)
        self.assertEqual(csv_path.read_bytes(),csv_before)
        newer=export.export_html(str(self.root),self.identifier,'html-002',self.font_dir,markdown_id=self.source_id,apply=True)
        self.assertEqual(export_checks.inspect(str(self.root),self.identifier,'html-002')['exports'][0]['visual_status'],'pending')
        with self.assertRaises(RegistrationError):export_checks.review(str(self.root),newer['manifest_path'],plan,True)

    def test_visual_check_index_interruption_recovers_without_rewriting_output(self):
        result=self.run_export(apply=True);plan=self.check_plan()
        publisher=export_library.publish
        def stop(root,entry):
            publisher(root,entry)
            if entry['path'].endswith('book.json'):raise OSError('stop')
        with patch.object(export_library,'publish',stop):
            with self.assertRaises(OSError):export_checks.review(str(self.root),result['manifest_path'],plan,True)
        export_checks.review(str(self.root),result['manifest_path'],plan,True)
        book=inventory(self.root)[0][self.identifier]
        self.assertEqual(len(book['export_checks']),1)
        self.assertEqual(book['export_checks'][0]['status'],'partial')

    def test_legacy_profile_resumes_and_new_output_records_metadata_reader(self):
        folder=self.root/f'books/{self.identifier}/exports/html/html-001';folder.mkdir(parents=True)
        # 旧版writerが最初に残したjournalを模擬し、旧profileで予定内容を再現する。
        (folder/export.JOURNAL).write_text(json.dumps({'context':{'created_at':'2026-10-16T12:00:00+09:00'}}),encoding='utf-8')
        _,legacy,_,transaction=export.prepare(self.root,self.identifier,'html-001',self.font_dir,markdown_id=self.source_id)
        (folder/export.JOURNAL).write_text(json.dumps(transaction),encoding='utf-8')
        self.run_export(apply=True)
        old=(folder/'export.json').read_bytes()
        self.assertEqual(legacy['settings']['policy'],'safe-html-1')
        self.assertNotIn('reader',legacy['settings'])
        self.run_export(apply=True)
        self.assertEqual((folder/'export.json').read_bytes(),old)
        new=export.export_html(str(self.root),self.identifier,'html-002',self.font_dir,markdown_id=self.source_id,apply=True)
        manifest=json.loads((self.root/new['manifest_path']).read_text(encoding='utf-8'))
        self.assertEqual(manifest['settings']['policy'],'safe-html-3')
        self.assertEqual(manifest['settings']['reader'],engine.READER)

    def test_japanese_labels_preserve_profile_two_and_its_check_records(self):
        folder=self.root/f'books/{self.identifier}/exports/html/html-001';folder.mkdir(parents=True)
        (folder/export.JOURNAL).write_text(json.dumps({'context':{'created_at':'2026-10-16T12:00:00+09:00','policy':'safe-html-2'}}),encoding='utf-8')
        _,_,_,tx=export.prepare(self.root,self.identifier,'html-001',self.font_dir,markdown_id=self.source_id)
        (folder/export.JOURNAL).write_text(json.dumps(tx),encoding='utf-8')
        self.run_export(apply=True);snapshot={p:p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        self.run_export(apply=True);self.assertEqual(snapshot,{p:p.read_bytes() for p in snapshot})
        self.assertEqual(export_checks.inspect(str(self.root),self.identifier,'html-001')['exports'][0]['stale_reasons'],['renderer_settings'])
        made=export.export_html(str(self.root),self.identifier,'html-ja',self.font_dir,markdown_id=self.source_id,apply=True)
        text=(self.root/made['output_path']).read_text(encoding='utf-8')
        self.assertIn('使用版: 登録Markdown',text);self.assertIn('原典照合: 未照合',text);self.assertIn('整形: 未評価',text)
        self.assertNotIn('unreviewed',text)

    def test_oversized_files_are_rejected_before_loading_bytes(self):
        huge=self.root/'inbox/huge.bin'
        with huge.open('wb') as stream:stream.truncate(64*1024*1024+1)
        with (self.font_dir/export.FONT).open('wb') as stream:stream.truncate(24*1024*1024+1)
        with patch.object(Path,'read_bytes',side_effect=AssertionError('oversized data must not be loaded')):
            with self.assertRaises(RegistrationError):export.checked_bytes(self.root,{'path':'inbox/huge.bin','sha256':'0'*64})
            with self.assertRaises(RegistrationError):export.font_files(self.font_dir)


class HtmlSafetyTests(unittest.TestCase):
    def test_ast_strips_attributes_and_rejects_raw_and_unsafe_urls(self):
        ast={'meta':{'header-includes':{'t':'MetaString','c':'<script>x</script>'}},'blocks':[
            {'t':'Header','c':[2,['safe',['ordinary'],[['onclick','alert(1)'],['style','background:url(https://example.test)']]],[{'t':'Str','c':'章'}]]}]}
        headings=engine.safe_ast(ast,lambda value:value)
        self.assertEqual(ast['meta'],{})
        self.assertEqual(ast['blocks'][0]['c'][1][2],[])
        self.assertEqual(headings[0]['text'],'章')
        for target in ('javascript:alert(1)','data:text/html,hi','file:///etc/passwd','../local.md','//example.test/x','https://u:p@example.test'):
            with self.subTest(target=target):
                with self.assertRaises(RegistrationError):engine.safe_link(target)
        self.assertEqual(engine.safe_link('https://example.test/a?q=1'),'https://example.test/a?q=1')
        with self.assertRaises(RegistrationError):engine.safe_ast({'blocks':[{'t':'RawBlock','c':['html','<script>']} ]},lambda v:v)

    def test_output_disallows_executable_tags_urls_attributes_and_bad_anchors(self):
        for fragment in ('<script>x</script>','<iframe src="x"></iframe>','<img src="https://example.test/x">',
                         '<p onclick="x">x</p>','<p style="background:url(x)">x</p>','<a href="#missing">x</a>',
                         '<a href="javascript:alert(1)">x</a>','<p id="a"></p><p id="a"></p>'):
            with self.subTest(fragment=fragment):
                with self.assertRaises(RegistrationError):engine.document(fragment,[],[],'title','ja','unreviewed')
        result=engine.document('<p>&lt;script&gt;</p>',[],[],'<script>','ja','unreviewed')
        self.assertIn('<title>&lt;script&gt;</title>',result)

    def test_svg_external_refs_and_execution_are_rejected(self):
        validate_svg(SVG)
        for bad in (b'<script>alert(1)</script>',b'<image href="file:///etc/passwd"/>',b'<use href="https://example.test/x"/>',
                    b'<rect onclick="x"/>',b'<style>@import "x"</style>',b'<rect fill="url(https://example.test/x)"/>',
                    br'<rect fill="u\72l(https://example.test/x)"/>',br'<rect fill="url/**/(https://example.test/x)"/>'):
            data=b'<svg xmlns="http://www.w3.org/2000/svg">'+bad+b'</svg>'
            with self.subTest(bad=bad):
                with self.assertRaises(RegistrationError):validate_svg(data)
        with self.assertRaises(RegistrationError):
            validate_svg(b'<?xml-stylesheet href="https://example.test/x.css"?>'+SVG)
        with self.assertRaises(RegistrationError):
            validate_svg(('<?xml-stylesheet href="https://example.test/x.css"?>'+SVG.decode()).encode('utf-16'))

    @unittest.skipUnless(has_supported_pandoc(),'実エンジン検証は対象版Pandocの導入環境で行う')
    def test_real_engine_math_table_footnote_and_raw_code_are_safe(self):
        pandoc=engine.Pandoc()
        text='## 日本語\n\n式 $f(x,y)$。脚注[^1]\n\n| 項目 | 値 |\n| --- | --- |\n| A | 1,000 |\n\n<script>alert(1)</script>\n\n[^1]: 注記'
        ast=pandoc.parse(text);headings=engine.safe_ast(ast,lambda v:v)
        output=engine.document(pandoc.html(ast),headings,[],'人工資料','ja','unreviewed')
        self.assertIn('<math',output);self.assertIn('<table>',output);self.assertIn('footnote',output)
        self.assertNotIn('<script>',output);self.assertIn('&lt;script&gt;',output)
        with self.assertRaises(RegistrationError):
            bad=pandoc.parse('[危険](javascript:alert(1))');engine.safe_ast(bad,lambda v:v)
        metadata='---\ntitle: Injected title\nheader-includes: "<script>alert(1)</script>"\nbibliography: /private/secret.bib\n---\n\n## 本文\n\n本文だけ。'
        ast=pandoc.parse(metadata)
        self.assertEqual(ast['meta'],{})
        output=pandoc.html(ast)
        self.assertNotIn('Injected title',output);self.assertNotIn('header-includes',output);self.assertNotIn('secret.bib',output)


if __name__=='__main__':unittest.main()

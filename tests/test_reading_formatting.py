import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from signal_library.reading.formatting import format_pages, comma, protected_blocks, render_blocks
from signal_library.registration import RegistrationError


class FormattingTests(unittest.TestCase):
    def confirmed(self,identifier,figures=0,tables=0,**kwargs):
        return {'source_id':identifier,'expected_figures':figures,'expected_tables':tables,
                'header_footer_confirmed':True,'paragraphs_confirmed':True,**kwargs}

    def test_seven_requirements_and_cross_page_sources(self):
        pages=[{'source_id':'a','sequence':1,'text':'<!-- page: 0 -->\n\n書名\n\n### 第1章\n\n書名は本文にも登場する。\n\n日本語,読点と\n折返し。\n\n![図1](0001.assets/図.png)\n図1の説明。\n\n| 項目 | 値 |\n| --- | --- |\n| 数量 | 1,000 |\n\n式 $f(x,y)$ を保持する。\n\nこの文はページを\n\n1'},
               {'source_id':'b','sequence':2,'text':'書名\n\nまたいで続く。\n\n別の段落。\n\n[^1]: 脚注を保持する。\n\n2'}]
        plan={'schema_version':'signal-library-curation-1','headings':[{'before':'### 第1章','after':'## 第1章','reason':'章の階層を統一'}],
              'pages':[self.confirmed('a',1,1,remove=[{'location':'header','text':'書名','reason':'柱'},{'location':'footer','text':'1','reason':'紙面番号'}]),
                       self.confirmed('b',remove=[{'location':'header','text':'書名','reason':'柱'},{'location':'footer','text':'2','reason':'紙面番号'}])],
              'join_pages':[{'left_source_id':'a','right_source_id':'b','expected_left_suffix':'この文はページを','expected_right_prefix':'またいで続く。','reason':'原典で同一文'}]}
        result=format_pages(pages,plan);text=result['text']
        self.assertNotIn('\n書名\n',text);self.assertNotIn('\n1\n',text)
        self.assertIn('書名は本文にも登場する。',text)
        self.assertIn('## 第1章',text);self.assertNotIn('### 第1章',text)
        self.assertIn('日本語、読点と折返し。',text)
        self.assertIn('![図1](0001.assets/図.png)',text)
        self.assertIn('| 項目 | 値 |\n| --- | --- |\n| 数量 | 1,000 |',text)
        self.assertIn('$f(x,y)$',text);self.assertIn('[^1]: 脚注',text)
        self.assertIn('この文はページをまたいで続く。\n\n別の段落。',text)
        self.assertTrue(any(record['source_ids']==['a','b'] for record in result['source_map']))
        self.assertEqual(result['format_status'],'complete');self.assertEqual(result['review_status'],'unreviewed')
        self.assertTrue(any(edit['kind']=='join_pages' for edit in result['corrections']))

    def test_math_code_links_numbers_and_english_are_protected(self):
        text='日本語,本文，読点 $f(x,y)$、式 $あ，い$ と `あ,い`、URL https://example.test/あ,い\n\n$$\nあ，い\n1,000\n$$\n\n\\begin{align}\n\\begin{matrix}a,b\\end{matrix}\nあ，い\n\\end{align}\n\n```python\nあ,い\n```not a closing fence\n```\n\nEnglish, text\nnext word\n'
        result=format_pages([{'source_id':'a','sequence':1,'text':text}])['text']
        for expected in ('$f(x,y)$','$あ，い$','`あ,い`','https://example.test/あ,い','あ，い\n1,000','\\begin{matrix}a,b\\end{matrix}\nあ，い','```not a closing fence','English, text next word'):
            self.assertIn(expected,result)
        self.assertIn('日本語、本文、読点',result)
        self.assertEqual(comma('![図,説明](あ,い.png)'),'![図、説明](あ,い.png)')

    def test_lists_quotes_footnotes_and_hard_breaks_keep_structure(self):
        source='- 第一項\n- 第二項\n  継続行\n\n> 引用\n> 続く\n\n[^1]: 脚注\n    引用コード，そのまま\n\n同じ段落の\n意図的改行  \n次の行\n'
        text=format_pages([{'source_id':'a','sequence':1,'text':source}])['text']
        self.assertIn('- 第一項\n- 第二項\n  継続行',text)
        self.assertIn('> 引用\n> 続く',text)
        self.assertIn('[^1]: 脚注\n    引用コード，そのまま',text)
        self.assertIn('同じ段落の意図的改行  \n次の行',text)

    def test_unknown_figures_tables_or_paragraphs_are_not_complete(self):
        result=format_pages([{'source_id':'a','sequence':1,'text':'本文'}])
        self.assertEqual(result['format_status'],'needs_review')
        plan={'schema_version':'signal-library-curation-1','pages':[self.confirmed('a',figures=1)]}
        result=format_pages([{'source_id':'a','sequence':1,'text':'図が欠落した本文'}],plan)
        self.assertEqual(result['format_status'],'needs_review')

    def test_body_or_caption_cannot_be_removed_as_footer(self):
        plan={'schema_version':'signal-library-curation-1','pages':[self.confirmed('a',remove=[{'location':'footer','text':'図の説明','reason':'誤判定'}])]}
        with self.assertRaises(RegistrationError):format_pages([{'source_id':'a','sequence':1,'text':'図の説明\n\n本文が続く。'}],plan)

    def test_blank_lines_and_non_content_examples_are_preserved(self):
        source='> 引用1\n\n> 引用2\n\n- 別の項1\n\n- 別の項2\n\n```md\n![例](missing.png)\n<table>HTMLの例</table>\n| a | b |\n| --- | --- |\n| one |\n### 例\n```'
        plan={'schema_version':'signal-library-curation-1','pages':[self.confirmed('a')],
              'headings':[{'before':'### 例','after':'## 例','reason':'章'}]}
        result=format_pages([{'source_id':'a','sequence':1,'text':source}],plan)
        self.assertEqual(result['text'],source+'\n')
        self.assertEqual(result['format_status'],'complete')

    def test_heading_rules_are_simultaneous_and_confirmation_is_boolean(self):
        plan={'schema_version':'signal-library-curation-1','pages':[self.confirmed('a')],
              'headings':[{'before':'### A','after':'## B','reason':'章'}, {'before':'## B','after':'### C','reason':'節'}]}
        result=format_pages([{'source_id':'a','sequence':1,'text':'### A\n\n## B'}],plan)
        self.assertEqual(result['text'],'## B\n\n### C\n')
        self.assertEqual(result['format_status'],'complete')
        plan['pages'][0]['header_footer_confirmed']='false'
        with self.assertRaises(RegistrationError):format_pages([{'source_id':'a','sequence':1,'text':'本文'}],plan)

    def test_indented_code_at_edges_and_page_structure_boundaries(self):
        result=format_pages([{'source_id':'a','sequence':1,'text':'    あ,い\n\n| a | b |\n| --- | --- |\n| c | d |'},
                             {'source_id':'b','sequence':2,'text':'## 次章\n\n末尾の改行  '}])
        self.assertTrue(result['text'].startswith('    あ,い\n'))
        self.assertIn('| c | d |\n\n## 次章',result['text'])
        self.assertTrue(result['text'].endswith('末尾の改行  \n'))

    def test_table_shape_unclosed_math_and_bad_heading_are_rejected(self):
        for text in ('| a | b |\n| --- | --- |\n| one |','$$\nnot closed','```\nnot closed'):
            with self.subTest(text=text):
                with self.assertRaises(RegistrationError):format_pages([{'source_id':'a','sequence':1,'text':text}])
        plan={'schema_version':'signal-library-curation-1','pages':[self.confirmed('a')],'headings':[{'before':'### Bad','after':'# Wrong','reason':'bad'}]}
        with self.assertRaises(RegistrationError):format_pages([{'source_id':'a','sequence':1,'text':'### Bad'}],plan)


if __name__=='__main__':unittest.main()

"""対話表示の日本語化と機械向け契約・秘密非表示の両立。"""
import contextlib
import io
import json
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from signal_library.cli import main


class PresentationTests(unittest.TestCase):
    def test_nested_help_and_invalid_argument_are_japanese(self):
        for args in (['--help'],['ocr','rebind','--help']):
            out=io.StringIO()
            with contextlib.redirect_stdout(out),self.assertRaises(SystemExit) as result:main(args)
            self.assertEqual(result.exception.code,0)
            self.assertIn('使い方:',out.getvalue());self.assertIn('オプション:',out.getvalue())
            self.assertNotIn('show this help message',out.getvalue())
        out=io.StringIO()
        with contextlib.redirect_stderr(out),self.assertRaises(SystemExit) as result:main(['--unknown-canary-sensitive'])
        self.assertEqual(result.exception.code,2);self.assertIn('引数が不正',out.getvalue())
        self.assertNotIn('canary-sensitive',out.getvalue())

    def test_json_state_is_not_translated(self):
        from unittest.mock import patch
        out=io.StringIO()
        with patch('signal_library.cli.doctor',return_value={'status':'ok','state':'ready','next_actions':[]}),contextlib.redirect_stdout(out):
            self.assertEqual(main(['doctor','--json']),0)
        self.assertEqual(json.loads(out.getvalue())['state'],'ready')

    def test_range_records_does_not_claim_full_verification(self):
        from signal_library.presentation import reading_label
        text=reading_label({'kind':'curated','id':'revision-1','review_status':'range_records','format_status':'complete'})
        self.assertIn('校正版',text);self.assertIn('範囲別の照合記録あり',text);self.assertNotIn('全文照合済み',text)

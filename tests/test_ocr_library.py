import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from signal_library.initialization import initialize
from signal_library.registration import RegistrationError, register, inventory, read_json
from signal_library.ocr import library as o
from signal_library.ocr.gateway import api_key_from_env
from signal_library.ocr.errors import OcrError
from test_ocr_migrated import FakeGateway


class OcrLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'Books'
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        initialize(str(self.root), True, False, self.repo)
        self.source = self.base / 'page.png'
        self.source.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        self.plan = self.base / 'plan.json'
        self.plan.write_text(json.dumps({'schema_version': 'signal-library-registration-1', 'metadata': {'title': 'OCR人工fixture'},
            'order_confirmed': True, 'images': [{'path': str(self.source)}]}), encoding='utf-8')
        self.identifier = register(str(self.root), self.plan, True)['book_id']
        FakeGateway.reset()
        FakeGateway.job.model = "mistral-ocr-4-1"

    def planned(self, **kwargs):
        return o.plan_ocr(str(self.root), self.identifier, 'capture-001', **kwargs)

    def send(self, **kwargs):
        plan = self.planned()
        return o.submit(str(self.root), self.identifier, 'capture-001', plan['plan_hash'], True, gateway_factory=FakeGateway, **kwargs)

    def test_plan_without_key_or_sdk_writes_nothing(self):
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.dict(os.environ, {}, clear=True):
            result = self.planned()
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(result['network_used'])
        self.assertEqual(result['plan']['estimated_cost_usd'], 0.002)
        self.assertFalse(result['plan']['billing_limit_is_enforced'])

    def test_limits_and_confirmation_prevent_upload(self):
        for limits in ({'max_pages': 0}, {'max_cost_usd': float('nan')}, {'max_cost_usd': float('inf')}, {'max_cost_usd': 0.001}):
            with self.assertRaises(RegistrationError):
                self.planned(**limits)
        with self.assertRaises(RegistrationError):
            o.submit(str(self.root), self.identifier, 'capture-001', 'wrong', True, gateway_factory=FakeGateway)
        with self.assertRaises(RegistrationError):
            o.submit(str(self.root), self.identifier, 'capture-001', self.planned()['plan_hash'], False, gateway_factory=FakeGateway)
        self.assertEqual(FakeGateway.uploads, {})

    def test_fake_submit_status_collect_and_index(self):
        result = self.send()
        path = self.root / result['manifest_path']
        manifest = read_json(path)
        custom = manifest['documents'][0]['custom_id']
        FakeGateway.downloads['output-1'] = (json.dumps({'custom_id': custom, 'response': {'body': {'model': 'mistral-ocr-4-1', 'pages': [{'index': 0, 'markdown': '# 人工本文', 'images': []}]}}}) + '\n').encode()
        o.follow(str(self.root), result['manifest_path'], gateway_factory=FakeGateway)
        collected = o.follow(str(self.root), result['manifest_path'], collect=True, gateway_factory=FakeGateway, keep_remote_files=False)
        self.assertEqual(collected['status'], 'ok')
        self.assertEqual(collected['cleanup'], 'CLEANED')
        book = inventory(self.root)[0][self.identifier]
        self.assertEqual(len(book['ocr_runs']), 1)
        self.assertEqual(book['ocr_runs'][0]['documents'][0]['source_id'], book['originals'][0]['source_id'])
        self.assertEqual(book['active_reading']['kind'], None)
        before = len(FakeGateway.uploads)
        with self.assertRaises(RegistrationError):
            self.send()
        self.assertEqual(len(FakeGateway.uploads), before)

    def test_collect_keep_remote_files_does_not_delete(self):
        result = self.send()
        manifest = read_json(self.root / result['manifest_path'])
        custom = manifest['documents'][0]['custom_id']
        FakeGateway.downloads['output-1'] = (json.dumps({'custom_id': custom, 'response': {'body': {'pages': [{'index': 0, 'markdown': 'fixture'}]}}}) + '\n').encode()
        collected = o.follow(str(self.root), result['manifest_path'], collect=True, gateway_factory=FakeGateway)
        self.assertEqual(collected['status'], 'ok')
        self.assertEqual(FakeGateway.deleted, [])
        self.assertNotEqual(collected['cleanup'], 'CLEANED')
        self.assertEqual(len(inventory(self.root)[0][self.identifier]['ocr_runs'][0]['documents']), 1)

    def test_empty_response_is_not_success(self):
        result = self.send()
        manifest = read_json(self.root / result['manifest_path'])
        custom = manifest['documents'][0]['custom_id']
        FakeGateway.downloads['output-1'] = (json.dumps({'custom_id': custom, 'response': {'body': {'pages': []}}}) + '\n').encode()
        collected = o.follow(str(self.root), result['manifest_path'], collect=True, gateway_factory=FakeGateway)
        self.assertEqual(collected['status'], 'action_required')
        self.assertFalse(collected['integrity']['all_succeeded'])

    def test_duplicate_response_is_not_success(self):
        result = self.send()
        manifest = read_json(self.root / result['manifest_path'])
        custom = manifest['documents'][0]['custom_id']
        line = json.dumps({'custom_id': custom, 'response': {'body': {'pages': [{'index': 0, 'markdown': 'text'}]}}}) + '\n'
        FakeGateway.downloads['output-1'] = (line + line).encode()
        collected = o.follow(str(self.root), result['manifest_path'], collect=True, gateway_factory=FakeGateway)
        self.assertEqual(collected['status'], 'action_required')
        self.assertFalse(collected['integrity']['complete'])

    def test_changed_capture_or_manifest_rejected(self):
        result = self.send()
        path = self.root / result['manifest_path']
        value = read_json(path)
        value['run_dir'] = str(self.base / 'outside')
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(RegistrationError):
            o.follow(str(self.root), result['manifest_path'], collect=True, gateway_factory=FakeGateway)
        self.assertFalse((self.base / 'outside').exists())

    def test_failed_submit_record_prevents_automatic_resend(self):
        class Broken(FakeGateway):
            def create_batch(self, *args):
                raise TimeoutError('secret-canary')
        with self.assertRaises(OcrError):
            o.submit(str(self.root), self.identifier, 'capture-001', self.planned()['plan_hash'], True, gateway_factory=Broken)
        self.assertEqual(next((self.root / 'books').rglob('manifest.json')).read_text(encoding='utf-8').find('secret-canary'), -1)
        before = len(FakeGateway.uploads)
        with self.assertRaises(RegistrationError):
            self.send()
        self.assertEqual(len(FakeGateway.uploads), before)

    def test_cli_ocr_failure_has_ocr_error_code(self):
        from signal_library.cli import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['ocr', 'plan', '--root', str(self.root), '--book-id', self.identifier, '--capture-id', 'capture-001', '--max-pages', '0', '--max-cost-usd', '0.01', '--json'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'ocr_rejected')

    def test_key_priority_is_consistent(self):
        with patch.dict(os.environ, {'MISTRAL_API_KEY': 'new', 'SIGNAL_STACK_MISTRAL_API_KEY': 'old'}, clear=True):
            self.assertEqual(api_key_from_env(), 'new')
        with patch.dict(os.environ, {'SIGNAL_STACK_MISTRAL_API_KEY': 'old'}, clear=True):
            self.assertEqual(api_key_from_env(), 'old')


if __name__ == '__main__':
    unittest.main()

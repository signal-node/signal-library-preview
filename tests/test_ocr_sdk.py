"""固定版SDKを実際に使用し、HTTPはMockTransportで外へ出さない。"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from signal_library.ocr.gateway import MistralOcrGateway


@unittest.skipUnless(importlib.util.find_spec('mistralai'), 'OCR extraの導入環境で実施')
class OcrSdkTests(unittest.TestCase):
    def test_client_constructs_with_normal_os_environment_without_request(self):
        with patch.dict(os.environ, {'MISTRAL_API_KEY': 'synthetic-canary'}, clear=False):
            with MistralOcrGateway(10):
                pass

    def test_pinned_sdk_gateway_http_contract(self):
        import httpx
        from mistralai.client import Mistral
        batch = dict(id='job-1', input_files=['file-1'], endpoint='/v1/ocr', errors=[], status='SUCCESS', created_at=1,
                     total_requests=1, completed_requests=1, succeeded_requests=1, failed_requests=0, model='mistral-ocr-4-1', output_file='output-1')
        observed = []
        def response(request):
            observed.append((request.method, request.url.path))
            if request.url.path == '/v1/files' and request.method == 'POST':
                return httpx.Response(200, json=dict(id='file-1', object='file', size_bytes=1, created_at=1, filename='page.png', purpose='ocr', sample_type='batch_request', source='upload'))
            if request.url.path == '/v1/batch/jobs' and request.method == 'POST':
                payload = json.loads(request.content)
                self.assertEqual(payload['endpoint'], '/v1/ocr')
                self.assertEqual(payload['input_files'], ['file-1'])
                return httpx.Response(200, json=batch)
            if request.url.path == '/v1/batch/jobs' and request.method == 'GET':
                return httpx.Response(200, json={'data': [batch], 'total': 1, 'object': 'list'})
            if request.url.path == '/v1/batch/jobs/job-1':
                return httpx.Response(200, json=batch)
            if request.url.path.endswith('/content'):
                return httpx.Response(200, content=b'fixture')
            if request.method == 'DELETE':
                return httpx.Response(200, json={'id': 'file-1', 'object': 'file', 'deleted': True})
            raise AssertionError('unexpected request')
        def client(**kwargs):
            self.assertIsNone(kwargs['retry_config'])
            return Mistral(**kwargs, client=httpx.Client(transport=httpx.MockTransport(response)))
        with patch.dict(os.environ, {'MISTRAL_API_KEY': 'synthetic-canary'}, clear=False), patch('mistralai.client.Mistral', side_effect=client), tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'page.png'
            path.write_bytes(b'fixture')
            with MistralOcrGateway(10) as gateway:
                self.assertEqual(gateway.upload(path, 'ocr'), 'file-1')
                self.assertEqual(gateway.create_batch('file-1', 'mistral-ocr-4-1', 'run-1', 24).id, 'job-1')
                self.assertEqual(gateway.get_batch('job-1').status, 'SUCCESS')
                self.assertEqual(len(gateway.find_batches('run-1')), 1)
                gateway.download('output-1', Path(folder) / 'output.jsonl')
                gateway.delete_file('file-1')
                self.assertEqual((Path(folder) / 'output.jsonl').read_bytes(), b'fixture')
        self.assertEqual(len(observed), 6)

    def test_submission_timeout_does_not_retry_in_sdk(self):
        import httpx
        from mistralai.client import Mistral
        calls = []
        def fail(request):
            calls.append(request)
            raise httpx.ReadTimeout('synthetic timeout', request=request)
        def client(**kwargs):
            return Mistral(**kwargs, client=httpx.Client(transport=httpx.MockTransport(fail)))
        with patch.dict(os.environ, {'MISTRAL_API_KEY': 'synthetic-canary'}, clear=False), patch('mistralai.client.Mistral', side_effect=client):
            with MistralOcrGateway(10) as gateway:
                with self.assertRaises(Exception):
                    gateway.create_batch('file-1', 'mistral-ocr-4-1', 'run-1', 24)
        self.assertEqual(len(calls), 1)

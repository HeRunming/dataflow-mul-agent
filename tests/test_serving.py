import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from dataflow_agents.compiler import render_dataflow_pipeline
from dataflow_agents.pipeline_runner import guard_servings
from dataflow_agents.serving import normalize_chat_url


def serving_spec(port):
    resources = {'test': {'type': 'api_llm', 'args': {
        'api_url': f'http://127.0.0.1:{port}/v1',
        'model_name': 'mock', 'key_name_of_api_key': 'DF_PIPELINE_TEST_KEY',
        'max_retries': 1, 'max_workers': 1}}}
    return {'steps': [{'step_id': 'answer', 'operator': 'ReasoningAnswerGenerator',
                       'import_path': 'dataflow.operators.reasoning', 'proposal': None,
                       'init_args': {'llm_serving': {'$resource': 'test'}},
                       'prepare_fields': {}, 'run_args': {'input_key': 'question', 'output_key': 'answer'}}],
            'initial_keys': ['question'], 'final_keys': ['question', 'answer'],
            'resources': resources, 'servings': resources}


class ServingTests(unittest.TestCase):
    def test_endpoint_conventions(self):
        for base, expected in [
            ('https://example.com', 'https://example.com/v1/chat/completions'),
            ('https://example.com/v1/', 'https://example.com/v1/chat/completions'),
            ('http://example.com/proxy/v1', 'http://example.com/proxy/v1/chat/completions'),
            ('https://example.com/v1/chat/completions', 'https://example.com/v1/chat/completions'),
            ('https://example.com/custom/chat?version=1', 'https://example.com/custom/chat?version=1'),
        ]:
            self.assertEqual(normalize_chat_url(base), expected)

    def test_standalone_pipeline_serving_and_failed_responses(self):
        requests = []
        reply = {'status': 200, 'body': {'choices': [{'message': {'content': '[{"group":0}]'}}]}}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(reply['status'])
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(reply['body']).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        spec = serving_spec(server.server_port)
        source = render_dataflow_pipeline(spec)
        # The endpoint convention is applied when the source is written, so the
        # reviewed pipeline shows the exact URL DataFlow will call.
        self.assertIn(f'api_url="http://127.0.0.1:{server.server_port}/v1/chat/completions"', source)
        namespace = {'__name__': 'generated_test'}
        exec(compile(source, '<pipeline>', 'exec'), namespace)
        with patch.dict('os.environ', {'DF_PIPELINE_TEST_KEY': 'test-key'}):
            pipeline = namespace['Reasoning_APIPipeline']()
        serving = pipeline.test
        self.addCleanup(serving.cleanup)
        self.assertEqual(serving.generate_from_input(['classify']), ['[{"group":0}]'])
        self.assertEqual(requests[0][0], '/v1/chat/completions')
        self.assertEqual(requests[0][1]['model'], 'mock')
        # The runner, not the generated pipeline, turns silent API failures
        # into an actionable error.
        guard_servings(pipeline)
        for status, body in [(404, {'error': {'message': 'Invalid URL'}}),
                             (200, {'choices': [{'message': {'content': ''}}]})]:
            reply.update(status=status, body=body)
            with self.subTest(status=status):
                with self.assertRaisesRegex(RuntimeError, 'LLM serving test: request failed or returned empty content'):
                    serving.generate_from_input(['classify'])

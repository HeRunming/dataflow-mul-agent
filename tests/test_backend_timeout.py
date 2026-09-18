"""Timeout diagnostics must retain evidence without leaking credentials."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from dataflow_agents.backend import CodexBackend


class BackendTimeoutTests(unittest.TestCase):
    def test_timeout_records_redacted_diagnostics_and_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Mock(pid=12345, returncode=-9)
            event = json.dumps({'type': 'error', 'message': 'Service temporarily unavailable sk-private-test'})
            process.communicate.side_effect = [subprocess.TimeoutExpired('codex', 240), (event, 'stderr sk-private-test')]
            with patch.dict('os.environ', {'DF_CODEX_API_KEY': 'sk-private-test'}), patch('dataflow_agents.backend.subprocess.Popen', return_value=process), patch('dataflow_agents.backend.os.killpg') as kill:
                with self.assertRaisesRegex(RuntimeError, '240s; last transport error: Service temporarily unavailable') as error:
                    CodexBackend().ask('operator_specialist', {}, schema={}, directory=directory)
            kill.assert_called_once()
            self.assertNotIn('sk-private-test', str(error.exception))
            root = Path(directory)
            for name in ('codex-events.jsonl', 'stderr.log', 'transport.json'):
                self.assertTrue((root/name).exists())
                self.assertNotIn('sk-private-test', (root/name).read_text())
            transport = json.loads((root/'transport.json').read_text())
            self.assertTrue(transport['timed_out'])
            self.assertEqual(transport['timeout_seconds'], 240)
            self.assertEqual(transport['exit_code'], -9)

    def test_timeout_with_no_output_and_child_already_exited(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Mock(pid=12345, returncode=0)
            process.communicate.side_effect = [subprocess.TimeoutExpired('codex', 5), ('', '')]
            with patch.dict('os.environ', {'DF_CODEX_API_KEY': 'test-credential'}), patch('dataflow_agents.backend.subprocess.Popen', return_value=process), patch('dataflow_agents.backend.os.killpg', side_effect=ProcessLookupError):
                with self.assertRaisesRegex(RuntimeError, 'Codex role timed out after 5s'):
                    CodexBackend(timeout_seconds=5).ask('planner', {}, schema={}, directory=directory)
            self.assertTrue(json.loads((Path(directory)/'transport.json').read_text())['timed_out'])

"""Presentation exports preserve provenance without copying credentials."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dataflow_agents.team import TeamStore

spec = importlib.util.spec_from_file_location('evidence_export', Path(__file__).parents[1] / 'scripts/export_demo_evidence.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EvidenceExportTests(unittest.TestCase):
    def test_redacted_file_hash_is_separate_from_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.json'
            source.write_text(json.dumps({'message': 'Bearer arbitrary-credential sk-test-secret ghp_testtoken',
                                          'api_key': 'other-secret'}))
            dest = root / 'export'
            exporter = module.Export(dest)
            exporter.copy(source, 'log.json')
            exported = (dest / 'log.json').read_text()
            for secret in ('arbitrary-credential', 'sk-test-secret', 'ghp_testtoken', 'other-secret'):
                self.assertNotIn(secret, exported)
            json.loads(exported)
            record = exporter.manifest[0]
            self.assertEqual(record['source_sha256'], module.sha(source.read_bytes()))
            self.assertEqual(record['export_sha256'], module.sha(exported.encode()))
            self.assertNotEqual(record['source_sha256'], record['export_sha256'])

    def test_exact_environment_secret_redaction(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'DF_CODEX_API_KEY': 'not-a-standard-prefix-credential'}):
            exporter = module.Export(Path(directory))
            self.assertEqual(exporter.clean('key: not-a-standard-prefix-credential'), 'key: [REDACTED]')

    def test_sqlite_events_preserve_sequence_and_distinguish_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TeamStore(root)
            store.event('skill.invoked', 'planner', skill='pipeline-planning')
            store.event('tool.called', 'operator_specialist', tool='operator_registry.lookup')
            before = (root / 'team.sqlite').read_bytes()
            events = module.events(root)
            self.assertEqual([item['seq'] for item in events], [1, 2])
            self.assertEqual(events[0]['detail']['skill'], 'pipeline-planning')
            self.assertEqual(sum(item['event'] == 'skill.invoked' for item in events), 1)
            self.assertEqual(before, (root / 'team.sqlite').read_bytes())

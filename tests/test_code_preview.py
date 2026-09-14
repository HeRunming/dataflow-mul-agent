"""Source previews must reflect executable artifacts, not earlier agent proposals."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from dataflow_agents.orchestrator import Orchestrator, load_config
from dataflow_agents.team import write_json
from dataflow_agents.web import create_app
from test_orchestrator import CustomBackend


class CodePreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = load_config(backend="offline", runs_root=self.tmp.name, auto_execute=False)
        self.app = create_app(self.cfg)
        self.client = self.enterContext(TestClient(self.app))
        self.root = Path(self.tmp.name) / "run-preview"

    def preview(self):
        return self.client.get('/api/v1/runs/run-preview/pipeline-code')

    def test_complete_registered_sources_follow_final_spec(self):
        Orchestrator(config=self.cfg).run("清洗空格并去重", run_dir=self.root)
        # An earlier binding may have been repaired. It must not drive review.
        write_json(self.root / "bindings.json", [{"operator": "obsolete"}])
        import json
        spec = json.loads((self.root / "pipeline-spec.json").read_text())
        spec["steps"].append(dict(spec["steps"][0], step_id="step-repeated"))
        write_json(self.root / "pipeline-spec.json", spec)
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], (self.root / "pipeline.py").read_text())
        self.assertEqual(len(payload["operators"]), 3)
        self.assertEqual(len({op["id"] for op in payload["operators"]}), 3)
        for step, op in zip(spec["steps"], payload["operators"]):
            raw = (Path(self.cfg["dataflow_root"]) / step["source_file"]).read_bytes()
            self.assertEqual(op["code"], raw.decode())
            self.assertEqual(op["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertFalse(op["changed"])
            self.assertIsNone(op["error"])
        self.assertFalse((self.root / "candidate.jsonl").exists())

    def test_custom_source_uses_current_file_and_reports_missing(self):
        Orchestrator(config=self.cfg, backend=CustomBackend()).run("Count A", run_dir=self.root)
        source = self.root / "custom/CountUpperA.py"
        # Review must show local edits, not stale proposal.source embedded in spec.
        source.write_text(source.read_text() + '\n# revised implementation\n' + '# long source\n' * 4000)
        op = self.preview().json()["operators"][0]
        self.assertEqual(op["code"], source.read_text())
        self.assertEqual(op["kind"], "custom")
        self.assertTrue(op["changed"])
        source.unlink()
        payload = self.preview().json()
        self.assertTrue(payload["code"])
        self.assertEqual(payload["operators"][0]["code"], "")
        self.assertIn("missing", payload["operators"][0]["error"])

    def test_source_cannot_escape_via_path_or_symlink(self):
        self.root.mkdir()
        (self.root / "pipeline.py").write_text("# pipeline\n")
        (self.root / "custom").mkdir()
        outside = Path(self.tmp.name) / "private.py"
        outside.write_text("private content")
        (self.root / "custom/link.py").symlink_to(outside)
        for filename in ("../private.py", str(outside), "custom/link.py"):
            with self.subTest(filename=filename):
                write_json(self.root / "pipeline-spec.json", {"steps": [{
                    "step_id": "step-1", "operator": "Custom", "proposal": {"source": "stale"},
                    "source_file": filename}]})
                response = self.preview()
                self.assertEqual(response.status_code, 200)
                op = response.json()["operators"][0]
                self.assertEqual(op["code"], "")
                self.assertIn("outside", op["error"])

    def test_unfinished_and_unknown_run(self):
        self.assertEqual(self.preview().status_code, 404)
        self.root.mkdir()
        self.assertEqual(self.preview().status_code, 404)

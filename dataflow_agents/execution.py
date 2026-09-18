"""Artifact-bound approval and bounded execution of real DataFlow code."""
from __future__ import annotations
import getpass
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from .codegen import RUNNER_FILENAME, render_pipeline_runner
from .team import digest, write_json


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ensure_runner(root):
    """Keep the workbench runner next to the pipeline it executes.

    The runner is workbench code rather than generated content, so a run
    created before it existed is refreshed instead of rejected.
    """
    path = Path(root) / RUNNER_FILENAME
    source = render_pipeline_runner()
    if not path.exists() or path.read_text(encoding="utf-8") != source:
        path.write_text(source, encoding="utf-8")
    return path


def manifest(root):
    root = Path(root)
    paths = [root / name for name in ("pipeline.py", "pipeline-spec.json", "input.jsonl")]
    paths += sorted((root / "custom").glob("*.py"))
    return {str(p.relative_to(root)): file_hash(p) for p in paths}


def request_approval(root, reasons):
    item = {"action":"execute_reviewed_pipeline", "artifact_digest":digest(manifest(root)),
            "files":manifest(root), "reasons":reasons, "created":time.time(), "status":"pending"}
    write_json(Path(root) / "approval-request.json", item)
    return item


def approve(root, ttl_seconds=3600):
    root = Path(root)
    request = json.loads((root / "approval-request.json").read_text())
    current = digest(manifest(root))
    if request["artifact_digest"] != current:
        raise ValueError("Artifacts changed since approval request; regenerate the request")
    grant = {"artifact_digest":current, "actor":getpass.getuser(), "issued":time.time(),
             "expires":time.time()+min(max(ttl_seconds, 1), 86400), "action":request["action"]}
    write_json(root / "approval.json", grant)
    return grant


def approval_valid(root):
    path = Path(root) / "approval.json"
    if not path.exists():
        return False
    grant = json.loads(path.read_text())
    return (grant.get("action") == "execute_reviewed_pipeline" and grant.get("expires", 0) > time.time()
            and grant.get("artifact_digest") == digest(manifest(root)))


def check_sources(spec, dataflow_root):
    for step in spec["steps"]:
        if not step["proposal"]:
            source = (Path(dataflow_root) / step["source_file"]).resolve()
            if not source.is_relative_to(Path(dataflow_root).resolve()):
                raise ValueError("Source escaped DataFlow repository")
            if file_hash(source) != step["source_sha256"]:
                raise ValueError(f"Upstream operator changed: {step['operator']}")


def execute(root, config, *, user_requested=False):
    root = Path(root).resolve()
    spec = json.loads((root / "pipeline-spec.json").read_text())
    check_sources(spec, config["dataflow_root"])
    ensure_runner(root)
    missing_resources = []
    resource_secrets = config.get("resource_secrets", {})
    for name, resource in spec.get("resources", {}).items():
        args = resource.get("args", {})
        key = args.get("key_name_of_api_key", "")
        if args.get("api_url", "").startswith("https://configure-resource.example.invalid") or not (os.getenv(key) or resource_secrets.get(name)):
            missing_resources.append(name)
    if missing_resources:
        return {"status":"resource_required", "compile":False, "executed":False, "resources":missing_resources,
                "error":"Register the referenced API resource and configure its backend environment variable"}
    reasons = [s["operator"] for s in spec["steps"] if s["risk"] != "low"]
    reasons += ["external resource: " + name for name in spec.get("resources", {})]
    if reasons and not user_requested and not approval_valid(root):
        request_approval(root, reasons)
        return {"status":"approval_required", "compile":False, "executed":False, "operators":reasons}
    # The local executor is a subprocess with a deadline, not an OS security sandbox.
    # A direct execution request is confirmation for this invocation only.
    env = {k:v for k,v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT"}}
    env.update(PYTHONPATH=config["dataflow_root"], PYTHONDONTWRITEBYTECODE="1", DF_LOGGING_LEVEL="ERROR")
    for name, resource in spec.get("resources", {}).items():
        key = resource["args"]["key_name_of_api_key"]
        secret = os.getenv(key) or resource_secrets.get(name)
        if not key.startswith("DF_PIPELINE_") or not secret:
            raise ValueError("Missing dedicated pipeline credential: " + key)
        env[key] = secret
    command = [config.get("python_bin", sys.executable), str(root / RUNNER_FILENAME),
               "--input", str(root / "input.jsonl"), "--cache", str(root / "cache"),
               "--output", str(root / "candidate.jsonl"), "--report", str(root / "runtime-report.json"),
               "--spec", str(root / "pipeline-spec.json"), "--pipeline", str(root / "pipeline.py"), "--execute"]
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=config.get("runtime_timeout_seconds", 900))
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        timeout_seconds = int(config.get("runtime_timeout_seconds", 900))
        report = {"status":"failed", "compile":False, "executed":False,
                  "error":f"Runtime deadline exceeded after {timeout_seconds}s",
                  "error_code":"RUNTIME_TIMEOUT", "timeout_seconds":timeout_seconds}
        write_json(root / "runtime-report.json", report)
    else:
        path = root / "runtime-report.json"
        report = json.loads(path.read_text()) if path.exists() else {"status":"failed", "error":stderr[-2000:], "compile":False, "executed":False}
        if process.returncode != 0:
            report["status"] = "failed"
    (root / "runtime.stdout.log").write_text(stdout, encoding="utf-8")
    (root / "runtime.stderr.log").write_text(stderr, encoding="utf-8")
    report.update(duration_ms=round((time.monotonic()-started)*1000), exit_code=process.returncode,
                  isolation="local subprocess", authorization="user_requested" if user_requested else "execution_policy")
    write_json(root / "runtime-report.json", report)
    return report


def promote(run_root, deployment_root):
    run_root, deployment_root = Path(run_root).resolve(), Path(deployment_root).resolve()
    status = json.loads((run_root / "status.json").read_text())
    if status["state"] != "VERIFIED":
        raise ValueError("Only VERIFIED output can be promoted")
    integrity = json.loads((run_root / "integrity.json").read_text())
    for name, expected in integrity.items():
        if file_hash(run_root / name) != expected:
            raise ValueError("Verified output was modified")
    deployment_root.mkdir(parents=True, exist_ok=True)
    path = deployment_root / "active.json"
    prior = json.loads(path.read_text()) if path.exists() else None
    active = {"run":str(run_root), "output_sha256":file_hash(run_root / "output.jsonl"), "previous":prior}
    write_json(path, active)
    return active


def rollback(deployment_root):
    path = Path(deployment_root) / "active.json"
    active = json.loads(path.read_text())
    previous = active.get("previous")
    if not previous:
        raise ValueError("No previous promoted version to restore")
    if file_hash(Path(previous["run"]) / "output.jsonl") != previous["output_sha256"]:
        raise ValueError("Previous output failed integrity check")
    write_json(path, previous)
    return previous

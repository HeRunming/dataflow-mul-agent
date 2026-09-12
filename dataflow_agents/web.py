"""HTTP API and small web host for the multi-Codex DataFlow workflow.

The API is deliberately a thin adapter around :class:`Orchestrator`: all
planning, operator binding, compilation, execution and verification remain in
the existing durable run workflow.
"""
from __future__ import annotations

import asyncio
import ast
import fcntl
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .catalog import discover_operator_catalog, load_catalog, search_catalog
from .compiler import render_dataflow_pipeline
from .execution import approve, execute
from .identities import IDENTITIES
from .orchestrator import Orchestrator, load_config
from .team import TeamStore, write_json

try:  # Keep importing the core package possible without the optional web deps.
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
except ImportError as exc:  # pragma: no cover - exercised only in minimal installs
    raise RuntimeError("Install the web extras with: pip install -e '.[web]'") from exc


TERMINAL_STATES = {"READY", "VERIFIED", "EXECUTED", "REFUSED", "BLOCKED", "APPROVAL_REQUIRED", "RESOURCE_REQUIRED"}
ARTIFACTS = {
    "request.json", "status.json", "plan.json", "bindings.json", "pipeline-spec.json",
    "static-validation.json", "runtime-report.json", "verification.json", "metrics.json",
    "traces.json", "result.json", "jobs.json", "catalog.json", "retrieval.json",
    "approval-request.json", "approval.json", "integrity.json", "input.jsonl", "output.jsonl",
    "pipeline.py",
}


def _dataset_registry_path() -> Path:
    return Path(__file__).parents[1] / "config" / "datasets.json"


def _read_datasets() -> dict[str, Any]:
    return _read_json(_dataset_registry_path(), {}) or {}


def _write_secret_registry(path: Path, secrets: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(secrets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _model_endpoint(url: str) -> list[str]:
    url = url.rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[:-len("/chat/completions")]
    if url.endswith("/models") or url.endswith("/model"):
        return [url]
    return [url + "/models", url + "/model"]


def _fetch_models(url: str, api_key: str) -> list[dict[str, Any]]:
    last_error = None
    for endpoint in _model_endpoint(url):
        request = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
        if api_key:
            request.add_header("Authorization", "Bearer " + api_key)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            items = payload.get("data", payload.get("models", payload)) if isinstance(payload, dict) else payload
            if isinstance(items, dict):
                items = items.get("data", [])
            result = []
            for item in items or []:
                if isinstance(item, str):
                    result.append({"id": item})
                elif isinstance(item, dict) and (item.get("id") or item.get("name")):
                    result.append({"id": item.get("id") or item.get("name"),
                                   "owned_by": item.get("owned_by"), "created": item.get("created")})
            return sorted(result, key=lambda item: item["id"])
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
    raise ValueError(f"Model discovery failed: {last_error}")


def _stage_snapshots(root: Path, limit: int = 30) -> list[dict[str, Any]]:
    """Read DataFlow's materialized per-call JSONL outputs for UI review."""
    cache = root / "cache"
    spec = _read_json(root / "pipeline-spec.json", {}) or {}
    call_names = []
    for step in spec.get("steps", []):
        for target, source in (step.get("prepare_fields") or {}).items():
            call_names.append(f"Copy {source} -> {target}")
        call_names.append(step.get("operator", ""))
    files = sorted(cache.glob("pipeline_step*.jsonl"), key=lambda path: int(re.search(r"step(\d+)", path.name).group(1))) if cache.is_dir() else []
    result = []
    for position, path in enumerate(files):
        match = re.search(r"step(\d+)", path.name)
        if not match:
            continue
        rows = []
        try:
            for line in path.open(encoding="utf-8"):
                if len(rows) >= limit:
                    break
                if line.strip():
                    rows.append(json.loads(line))
        except (OSError, ValueError):
            continue
        result.append({"stage_id": f"stage-{int(match.group(1)):02d}", "index": position,
                       "name": call_names[position] if position < len(call_names) else path.stem,
                       "operator": call_names[position] if position < len(call_names) else None,
                       "rows": rows, "row_count": sum(1 for line in path.open(encoding="utf-8") if line.strip()),
                       "fields": list(rows[0]) if rows and isinstance(rows[0], dict) else [],
                       "source": path.name})
    if not result:
        candidate = root / "candidate.jsonl"
        if candidate.exists():
            rows = []
            for line in candidate.open(encoding="utf-8"):
                if len(rows) >= limit:
                    break
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
            result.append({"stage_id": "final", "index": 0, "name": "Final output", "operator": None,
                           "rows": rows, "row_count": sum(1 for line in candidate.open(encoding="utf-8") if line.strip()),
                           "fields": list(rows[0]) if rows and isinstance(rows[0], dict) else [], "source": candidate.name})
    return result


def _safe_run(config: dict[str, Any], run_id: str) -> Path:
    root = (Path(config["runs_root"]).resolve() / run_id).resolve()
    if root.parent != Path(config["runs_root"]).resolve() or not root.name.startswith("run-"):
        raise HTTPException(status_code=400, detail="Invalid run id")
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Run not found")
    return root


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _events(root: Path) -> list[dict[str, Any]]:
    # SQLite is the live join authority; events.jsonl is the durable export.
    db = root / "team.sqlite"
    if db.exists():
        with TeamStore(root).connect() as conn:
            rows = conn.execute("SELECT seq,timestamp,kind,role,job,detail FROM events ORDER BY seq").fetchall()
        return [{"seq": seq, "timestamp": timestamp, "event": kind, "agent": role,
                 "job": job, "trace_id": root.name, **json.loads(detail)}
                for seq, timestamp, kind, role, job, detail in rows]
    path = root / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def _run_summary(root: Path) -> dict[str, Any]:
    request = _read_json(root / "request.json", {}) or {}
    status = _read_json(root / "status.json", {"state": "QUEUED"}) or {"state": "QUEUED"}
    result = _read_json(root / "result.json", {}) or {}
    spec = _read_json(root / "pipeline-spec.json", {}) or {}
    events = _events(root)
    latest = events[-1] if events else {}
    return {"run_id": root.name, "state": status.get("state", "QUEUED"),
            "backend": status.get("backend") or result.get("backend") or "unknown",
            "updated": status.get("updated"), "request": request.get("request", ""),
            "input_keys": request.get("input_keys", []), "operators": [s.get("operator") for s in spec.get("steps", [])],
            "result": result, "summary": status.get("summary") or result.get("summary"),
            "reason": status.get("reason") or result.get("reason") or status.get("error") or result.get("error"),
            "latest_event": latest, "approval_required": status.get("state") == "APPROVAL_REQUIRED"}


def _agent_outputs(root: Path) -> list[dict[str, Any]]:
    outputs = []
    paths = sorted((root / "agents").glob("*/*"), key=lambda p: p.stat().st_mtime)
    for directory in paths:
        if not directory.is_dir():
            continue
        path = directory / "last-message.json"
        event_path = directory / "codex-events.jsonl"
        try:
            text = path.read_text(encoding="utf-8")[-3000:] if path.exists() else (
                event_path.read_text(encoding="utf-8")[-3000:] if event_path.exists() else "")
            if text:
                outputs.append({"agent": directory.parent.name, "attempt": directory.name, "text": text})
        except OSError:
            continue
    return outputs


def create_app(config: dict[str, Any] | None = None) -> FastAPI:
    cfg = config or load_config()
    cfg = dict(cfg, auto_execute=False)
    resource_path = Path(__file__).parents[1] / "config" / "resources.json"
    secret_path = Path(__file__).parents[1] / "config" / "resource-secrets.json"
    cfg.setdefault("resource_secrets", {})
    if secret_path.exists() and not cfg["resource_secrets"]:
        cfg["resource_secrets"].update(_read_json(secret_path, {}) or {})
    runs_root = Path(cfg["runs_root"]).resolve()
    runs_root.mkdir(parents=True, exist_ok=True)
    workers = max(1, int(cfg.get("api_workers", 4)))
    executor = __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(max_workers=workers)
    app = FastAPI(title="DataFlow Multi-Codex", version="1.0.0")
    app.state.config = cfg
    app.state.executor = executor
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def launch(run_dir: Path, request_text: str, input_keys: list[str] | None, input_file: Path,
               allow_custom: bool, source: dict[str, Any], constraints: dict[str, Any]) -> None:
        def work() -> None:
            try:
                Orchestrator(config=cfg).run(request_text, input_keys=input_keys, input_file=input_file,
                                              run_dir=run_dir, allow_custom=allow_custom,
                                              source=source, constraints=constraints)
            except Exception as exc:  # Orchestrator persists BLOCKED state itself.
                write_json(run_dir / "result.json", {"state": "BLOCKED", "run_dir": str(run_dir), "error": str(exc)})
        executor.submit(work)

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "backend": cfg.get("backend", "codex"), "dataflow_root": cfg["dataflow_root"]}

    @app.get("/api/v1/resources")
    @app.get("/api/v1/servings")
    def resources() -> dict[str, Any]:
        return {"resources": [{"name": name, "type": value.get("type"),
                                "api_url": value.get("args", {}).get("api_url"),
                                "model_name": value.get("args", {}).get("model_name"),
                                "key_name_of_api_key": value.get("args", {}).get("key_name_of_api_key"),
                                "configured": bool(os.getenv(value.get("args", {}).get("key_name_of_api_key", "")) or cfg.get("resource_secrets", {}).get(name))}
                              for name, value in cfg.get("resources", {}).items()]}

    @app.get("/api/v1/resources/classes")
    @app.get("/api/v1/servings/classes")
    def serving_classes() -> dict[str, Any]:
        """Expose constructor metadata in the same shape needed by a serving form."""
        dataflow_root = str(Path(cfg["dataflow_root"]).resolve())
        source_path = Path(dataflow_root) / "dataflow/serving/api_llm_serving_request.py"
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            raise HTTPException(status_code=500, detail=f"Cannot inspect serving class: {exc}") from exc
        init = next((node for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef) and node.name == "__init__"), None)
        params = []
        if init is None:
            raise HTTPException(status_code=500, detail="APILLMServing_request.__init__ not found")
        arguments = list(init.args.posonlyargs) + list(init.args.args) + list(init.args.kwonlyargs)
        defaults = [None] * (len(arguments) - len(init.args.defaults)) + list(init.args.defaults)
        for parameter, default_node in zip(arguments, defaults):
            if parameter.arg == "self":
                continue
            try:
                default = ast.literal_eval(default_node) if default_node is not None else None
            except (ValueError, TypeError):
                default = ast.unparse(default_node) if default_node is not None else None
            params.append({"name": parameter.arg, "required": default_node is None,
                           "default_value": default,
                           "type": ast.unparse(parameter.annotation) if parameter.annotation else "Any"})
        return {"classes": [{"cls_name": "APILLMServing_request", "params": params}]}

    @app.post("/api/v1/resources")
    @app.post("/api/v1/servings")
    def register_resource(payload: dict[str, Any]):
        name = str(payload.get("name", "")).strip()
        api_url = str(payload.get("api_url", "")).strip()
        model_name = str(payload.get("model_name", "")).strip()
        key_name = str(payload.get("key_name_of_api_key", "")).strip()
        if not name or not name.replace("_", "a").isalnum() or not api_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="name must be alphanumeric and api_url must use HTTP or HTTPS")
        api_key = str(payload.get("api_key", "")).strip()
        if api_key and not key_name:
            key_name = "DF_PIPELINE_" + re.sub(r"[^A-Z0-9]", "_", name.upper()) + "_API_KEY"
        if not key_name.startswith("DF_PIPELINE_"):
            raise HTTPException(status_code=422, detail="key_name_of_api_key must start with DF_PIPELINE_")
        cfg.setdefault("resources", {})[name] = {"type": "api_llm", "args": {
            "api_url": api_url, "key_name_of_api_key": key_name, "model_name": model_name,
            "temperature": float(payload.get("temperature", 0.2)), "max_workers": int(payload.get("max_workers", 2)),
            "max_retries": int(payload.get("max_retries", 2)), "connect_timeout": 10, "read_timeout": 120}}
        if api_key:
            cfg.setdefault("resource_secrets", {})[name] = api_key
        elif name in cfg.get("resource_secrets", {}):
            cfg["resource_secrets"][name] = cfg["resource_secrets"][name]
        write_json(resource_path, cfg["resources"])
        if cfg.get("resource_secrets"):
            _write_secret_registry(secret_path, cfg["resource_secrets"])
        return {"name": name, "resource": cfg["resources"][name],
                "configured": bool(os.getenv(key_name) or cfg.get("resource_secrets", {}).get(name))}

    @app.delete("/api/v1/resources/{name}")
    @app.delete("/api/v1/servings/{name}")
    def delete_resource(name: str):
        if name not in cfg.get("resources", {}):
            raise HTTPException(status_code=404, detail="Resource not found")
        del cfg["resources"][name]
        cfg.get("resource_secrets", {}).pop(name, None)
        write_json(resource_path, cfg["resources"])
        if cfg.get("resource_secrets"):
            _write_secret_registry(secret_path, cfg["resource_secrets"])
        elif secret_path.exists():
            secret_path.unlink()
        return {"deleted": name}

    @app.post("/api/v1/servings/models")
    @app.post("/api/v1/resources/models")
    @app.post("/api/v1/models")
    def discover_models(payload: dict[str, Any]):
        resource_name = str(payload.get("resource_name", "")).strip()
        configured = cfg.get("resources", {}).get(resource_name, {}) if resource_name else {}
        args = configured.get("args", {})
        api_url = str(payload.get("api_url", "")).strip() or args.get("api_url", "")
        api_key = str(payload.get("api_key", "")).strip() or os.getenv(args.get("key_name_of_api_key", ""), "") or cfg.get("resource_secrets", {}).get(resource_name, "")
        if not api_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="api_url must use HTTP or HTTPS")
        try:
            return {"models": _fetch_models(api_url, api_key)}
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/v1/servings/models")
    @app.get("/api/v1/resources/models")
    @app.get("/api/v1/models")
    def discover_models_get(api_url: str = "", resource_name: str = ""):
        return discover_models({"api_url": api_url, "resource_name": resource_name})

    @app.get("/api/v1/datasets")
    def list_datasets() -> dict[str, Any]:
        registry = _read_datasets()
        return {"datasets": list(registry.values())}

    @app.post("/api/v1/datasets")
    def register_dataset(payload: dict[str, Any]):
        name = str(payload.get("name", "")).strip()
        rows = payload.get("rows")
        if not name or not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise HTTPException(status_code=422, detail="name and a non-empty rows array of objects are required")
        if len(rows) > cfg.get("max_input_rows", 10000):
            raise HTTPException(status_code=422, detail="dataset exceeds the configured row limit")
        dataset_id = "ds-" + uuid.uuid4().hex[:12]
        dataset_dir = Path(__file__).parents[1] / "config" / "datasets"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        path = dataset_dir / f"{dataset_id}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        os.chmod(path, 0o600)
        registry = _read_datasets()
        item = {"id": dataset_id, "name": name, "path": str(path), "rows": len(rows),
                "input_keys": list(rows[0]), "sample": rows[:5]}
        registry[dataset_id] = item
        write_json(_dataset_registry_path(), registry)
        return item

    @app.get("/api/v1/datasets/{dataset_id}/preview")
    def dataset_preview(dataset_id: str, limit: int = 5):
        item = _read_datasets().get(dataset_id)
        if not item:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return {"dataset": item, "rows": item.get("sample", [])[:max(1, min(limit, 100))]}

    @app.delete("/api/v1/datasets/{dataset_id}")
    def delete_dataset(dataset_id: str):
        registry = _read_datasets()
        item = registry.pop(dataset_id, None)
        if not item:
            raise HTTPException(status_code=404, detail="Dataset not found")
        path = Path(item.get("path", ""))
        if path.is_file() and path.parent == (Path(__file__).parents[1] / "config" / "datasets"):
            path.unlink()
        write_json(_dataset_registry_path(), registry)
        return {"deleted": dataset_id}

    @app.get("/api/v1/agents")
    def agents() -> list[dict[str, Any]]:
        return [asdict(identity) for identity in IDENTITIES]

    @app.get("/api/v1/runs")
    def list_runs() -> list[dict[str, Any]]:
        roots = sorted((p for p in runs_root.glob("run-*") if p.is_dir()),
                       key=lambda p: (_read_json(p / "status.json", {}).get("updated", p.stat().st_mtime), p.name), reverse=True)
        return [_run_summary(root) for root in roots[:100]]

    @app.delete("/api/v1/runs/{run_id}")
    def delete_run(run_id: str):
        root = _safe_run(cfg, run_id)
        state = (_read_json(root / "status.json", {}) or {}).get("state")
        if state not in TERMINAL_STATES and (root / ".leader.lock").exists():
            raise HTTPException(status_code=409, detail="Running runs cannot be deleted")
        shutil.rmtree(root)
        input_path = runs_root / ".api-inputs" / f"{run_id}.jsonl"
        if input_path.exists():
            input_path.unlink()
        return {"deleted": run_id}

    @app.post("/api/v1/runs")
    async def create_run(payload: dict[str, Any]) -> JSONResponse:
        request_text = str(payload.get("request", "")).strip()
        if not request_text:
            raise HTTPException(status_code=422, detail="request is required")
        dataset_id = str(payload.get("dataset_id", "")).strip()
        dataset_item = _read_datasets().get(dataset_id) if dataset_id else None
        if dataset_id and not dataset_item:
            raise HTTPException(status_code=404, detail="Dataset not found")
        rows = None if dataset_item else payload.get("input_rows")
        input_keys = payload.get("input_keys")
        if dataset_item:
            rows = [json.loads(line) for line in Path(dataset_item["path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
            input_keys = input_keys or dataset_item.get("input_keys") or list(rows[0])
        if rows is not None:
            if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
                raise HTTPException(status_code=422, detail="input_rows must be a non-empty list of objects")
            if input_keys is None:
                input_keys = list(rows[0])
            if any(set(input_keys) - set(row) for row in rows):
                raise HTTPException(status_code=422, detail="input rows do not contain input_keys")
        else:
            default = Path(__file__).parents[1] / "examples/input.jsonl"
            rows = [json.loads(line) for line in default.read_text().splitlines() if line.strip()]
            input_keys = input_keys or list(rows[0])
        run_id = "run-" + uuid.uuid4().hex[:12]
        input_dir = runs_root / ".api-inputs"
        input_dir.mkdir(mode=0o700, exist_ok=True)
        input_file = input_dir / f"{run_id}.jsonl"
        input_file.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        run_dir = runs_root / run_id
        run_dir.mkdir(mode=0o700, exist_ok=False)
        launch(run_dir, request_text, input_keys, input_file, bool(payload.get("allow_custom", True)),
               payload.get("source") or {"type": "web"}, payload.get("constraints") or {})
        return JSONResponse({"run_id": run_id, "state": "QUEUED", "request": request_text}, status_code=202)

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        root = _safe_run(cfg, run_id)
        summary = _run_summary(root)
        summary["status"] = _read_json(root / "status.json", {})
        summary["metrics"] = _read_json(root / "metrics.json", {})
        summary["verification"] = _read_json(root / "verification.json")
        summary["pipeline"] = _read_json(root / "pipeline-spec.json")
        return summary

    @app.get("/api/v1/runs/{run_id}/events")
    def get_events(run_id: str, after: int = 0) -> list[dict[str, Any]]:
        return [event for event in _events(_safe_run(cfg, run_id)) if event.get("seq", 0) > after]

    @app.get("/api/v1/runs/{run_id}/stages")
    def stages(run_id: str, limit: int = 30):
        return {"stages": _stage_snapshots(_safe_run(cfg, run_id), max(1, min(limit, 100)))}

    @app.get("/api/v1/runs/{run_id}/agent-outputs")
    def agent_outputs(run_id: str) -> list[dict[str, Any]]:
        return _agent_outputs(_safe_run(cfg, run_id))

    @app.get("/api/v1/runs/{run_id}/stream")
    async def stream_events(run_id: str, after: int = 0) -> StreamingResponse:
        root = _safe_run(cfg, run_id)
        async def generator():
            cursor = after
            idle = 0
            while idle < 300:
                events = [event for event in _events(root) if event.get("seq", 0) > cursor]
                for event in events:
                    cursor = max(cursor, event.get("seq", cursor))
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                state = (_read_json(root / "status.json", {}) or {}).get("state")
                if state in TERMINAL_STATES and not events:
                    yield f"event: done\ndata: {json.dumps({'state': state})}\n\n"
                    return
                idle += 1
                await asyncio.sleep(0.5)
        return StreamingResponse(generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/v1/runs/{run_id}/artifact/{name}")
    def artifact(run_id: str, name: str):
        root = _safe_run(cfg, run_id)
        if name not in ARTIFACTS or Path(name).name != name:
            raise HTTPException(status_code=400, detail="Artifact is not available")
        path = root / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="Artifact not found")
        if path.suffix == ".jsonl" or path.suffix == ".py":
            return FileResponse(path)
        return JSONResponse(_read_json(path, {}))

    @app.post("/api/v1/runs/{run_id}/approve")
    def approve_run(run_id: str, payload: dict[str, Any] | None = None):
        root = _safe_run(cfg, run_id)
        try:
            grant = approve(root, int((payload or {}).get("ttl_seconds", 3600)))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        TeamStore(root).event("approval.granted", "human", **grant)
        return {"run_id": run_id, "approval": grant}

    @app.post("/api/v1/runs/{run_id}/resume")
    def resume_run(run_id: str):
        root = _safe_run(cfg, run_id)
        state = (_read_json(root / "status.json", {}) or {}).get("state")
        if state not in {"APPROVAL_REQUIRED", "RESOURCE_REQUIRED"}:
            raise HTTPException(status_code=409, detail=f"Run is {state or 'unknown'}, not awaiting approval or resource configuration")
        executor.submit(lambda: Orchestrator(config=cfg).resume(root))
        return {"run_id": run_id, "state": "RESUMING"}

    @app.post("/api/v1/runs/{run_id}/execute")
    @app.post("/api/v1/runs/{run_id}/run")
    def execute_pipeline(run_id: str):
        root = _safe_run(cfg, run_id)
        if not (root / "pipeline-spec.json").exists() or not (root / "pipeline.py").exists():
            raise HTTPException(status_code=409, detail="This run has no generated pipeline yet")
        state = (_read_json(root / "status.json", {}) or {}).get("state")
        if state not in TERMINAL_STATES:
            raise HTTPException(status_code=409, detail="This run is already busy")
        lock = (root / ".leader.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise HTTPException(status_code=409, detail="This run is already busy")
        def work() -> None:
            store = TeamStore(root)
            try:
                # Bind the latest serving configuration without re-planning.
                spec = _read_json(root / "pipeline-spec.json")
                for name in spec.get("resources", {}):
                    if name in cfg.get("resources", {}):
                        spec["resources"][name] = cfg["resources"][name]
                spec["servings"] = dict(spec.get("resources", {}))
                write_json(root / "pipeline-spec.json", spec)
                (root / "pipeline.py").write_text(render_dataflow_pipeline(spec), encoding="utf-8")
                store.event("execution.requested", "human", confirmation="Run pipeline")
                runtime = execute(root, cfg, user_requested=True)
                write_json(root / "runtime-report.json", runtime)
                if runtime["status"] == "resource_required":
                    store.checkpoint("RESOURCE_REQUIRED", resources=runtime["resources"], summary="等待注册或配置 DataFlow API resource")
                elif runtime["status"] == "approval_required":
                    store.checkpoint("APPROVAL_REQUIRED", reasons=runtime["operators"])
                elif runtime["status"] == "passed":
                    candidate = root / "candidate.jsonl"
                    if candidate.exists():
                        shutil.copyfile(candidate, root / "output.jsonl")
                    store.checkpoint("EXECUTED", summary=f"Pipeline 执行完成，输出 {runtime.get('rows', 0)} 行")
                    write_json(root / "result.json", {"state": "EXECUTED", "run_dir": str(root), "rows": runtime.get("rows", 0)})
                else:
                    store.checkpoint("BLOCKED", error=runtime.get("error", "Pipeline execution failed"))
            except Exception as exc:
                store.checkpoint("BLOCKED", error=str(exc)[-2400:])
            finally:
                try:
                    store.export()
                finally:
                    lock.close()
        try:
            TeamStore(root).checkpoint("RUNNING", summary="正在执行已生成的 DataFlow pipeline")
            executor.submit(work)
        except Exception as exc:
            lock.close()
            TeamStore(root).checkpoint("BLOCKED", error=str(exc)[-2400:])
            raise HTTPException(status_code=503, detail="Could not start pipeline execution") from exc
        return {"run_id": run_id, "state": "RUNNING"}

    @app.get("/api/v1/operators")
    def operators(query: str = "", limit: int = 50):
        try:
            catalog = load_catalog()
            if not catalog:
                catalog = discover_operator_catalog(cfg["dataflow_root"])
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        matches = search_catalog(query, catalog, limit=max(1, min(limit, 200))) if query else catalog[:max(1, min(limit, 200))]
        return {"count": len(matches), "operators": matches}

    frontend = Path(__file__).parents[1] / "frontend" / "dist"
    if frontend.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            candidate = frontend / path
            return FileResponse(candidate if candidate.is_file() else frontend / "index.html")

    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("dataflow_agents.web:app", host=os.getenv("DF_WEB_HOST", "127.0.0.1"),
                port=int(os.getenv("DF_WEB_PORT", "8000")), reload=False)

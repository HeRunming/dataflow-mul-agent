"""End-to-end durable Codex workflow: plan, parallel bind, integrate, execute, verify."""
from __future__ import annotations
import fcntl
import json
import os
import shutil
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .serving import normalize_chat_url
from .backend import build_backend
from .catalog import discover_operator_catalog, catalog_version, search_catalog, with_sources
from .compiler import compile_spec, ordered_steps
from .codegen import write_pipeline_sources
from .contracts import SCHEMAS, PROMPTS
from .execution import execute, file_hash, manifest
from .memory import ExperienceStore
from .team import CodexTeamRuntime, TeamStore, digest, write_json

ROOT = Path(__file__).parents[1]


def _fallback_binding(step, catalog):
    """Source-grounded fallback for common reasoning pipeline steps.

    This keeps serving selection separate from operator selection: the binding
    contains only a stable ``$resource`` reference, never an API client.
    """
    text = (step.get("query", "") + " " + step.get("objective", "")).lower()
    names = [o["name"] for o in catalog]
    def exists(name): return name if name in names else ""
    if any(x in text for x in ("synth", "generate two", "question generation")):
        name = exists("ReasoningQuestionGenerator")
        if name:
            return {"step_id": step["step_id"], "operator": name,
                    "init_args": {"llm_serving": {"$resource": "llm_default"}, "num_prompts": 2},
                    "run_args": {"input_key": step["input_keys"][0], "output_synth_or_input_flag": "Synth_or_Input"},
                    "prepare_fields": {}, "rationale": "Fallback source-grounded binding; serving is deferred.", "proposal": None}
    if any(x in text for x in ("reasoning trajectory", "generate reasoning", "generated_cot")):
        name = exists("ReasoningAnswerGenerator")
        if name:
            return {"step_id": step["step_id"], "operator": name,
                    "init_args": {"llm_serving": {"$resource": "llm_default"}},
                    "run_args": {"input_key": step["input_keys"][0], "output_key": "generated_cot"},
                    "prepare_fields": {}, "rationale": "Fallback source-grounded binding; serving is deferred.", "proposal": None}
    if any(x in text for x in ("valid", "correct", "solvab", "reasonable")):
        name = exists("ReasoningQuestionFilter")
        if name:
            return {"step_id": step["step_id"], "operator": name,
                    "init_args": {"llm_serving": {"$resource": "llm_default"}},
                    "run_args": {"input_key": step["input_keys"][0]}, "prepare_fields": {},
                    "rationale": "Fallback source-grounded binding; serving is deferred.", "proposal": None}
    if "ngram" in text or "near-duplicate" in text:
        name = exists("ReasoningAnswerNgramFilter")
        if name:
            return {"step_id": step["step_id"], "operator": name,
                    "init_args": {}, "run_args": {"input_question_key": "question", "input_answer_key": "generated_cot"},
                    "prepare_fields": {}, "rationale": "Fallback source-grounded binding.", "proposal": None}
    return None


def _catalog_covers_plan(plan, catalog):
    """Detect planner false negatives when every semantic step is catalog-backed."""
    steps = plan.get("steps") or []
    if not steps:
        return False
    for step in steps:
        query = f"{step.get('query', '')} {step.get('objective', '')}"
        if not search_catalog(query, catalog, limit=1):
            return False
    return True

def load_config(path=None, **overrides):
    path = Path(path or ROOT / "config/runtime.json")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    resource_path = path.parent / "resources.json"
    if resource_path.exists():
        cfg.setdefault("resources", {}).update(json.loads(resource_path.read_text(encoding="utf-8")))
    secret_path = path.parent / "resource-secrets.json"
    if secret_path.exists():
        cfg["resource_secrets"] = json.loads(secret_path.read_text(encoding="utf-8"))
    cfg.update({k:v for k,v in overrides.items() if v is not None})
    cfg["dataflow_root"] = str(Path(os.getenv("DATAFLOW_ROOT", cfg.get("dataflow_root", "../DataFlow"))).expanduser())
    if not Path(cfg["dataflow_root"]).is_absolute():
        cfg["dataflow_root"] = str((ROOT / cfg["dataflow_root"]).resolve())
    cfg.setdefault("python_bin", sys.executable)
    cfg.setdefault("runs_root", str(ROOT / "runs"))
    cfg.setdefault("resources", {})
    cfg.setdefault("resource_secrets", {})
    for resource in cfg["resources"].values():
        args = resource.get("args", {})
        allowed = {"api_url", "key_name_of_api_key", "model_name", "temperature", "max_workers", "max_tokens", "max_retries", "connect_timeout", "read_timeout"}
        if resource.get("type") != "api_llm" or set(args) - allowed:
            raise ValueError("Resources support api_llm with allowlisted constructor arguments only")
        if not args.get("key_name_of_api_key", "").startswith("DF_PIPELINE_"):
            raise ValueError("Pipeline credentials must use a dedicated DF_PIPELINE_* environment variable")
        if not args.get("api_url", "").startswith(("http://", "https://")):
            raise ValueError("API resources require an explicit HTTP or HTTPS URL")
        args["api_url"] = normalize_chat_url(args["api_url"])
        if int(args.get("max_workers", 1)) < 1 or int(args.get("max_tokens", 1)) < 1:
            raise ValueError("API resource max_workers and max_tokens must be positive")
    cfg["backend"] = os.getenv("CODEX_BACKEND", cfg.get("backend", "codex"))
    return cfg

class Orchestrator:
    def __init__(self, config_path=None, config=None, backend=None, **kwargs):
        self.config = config or load_config(config_path)
        self.backend = backend or build_backend(self.config)
        self.memory = ExperienceStore(Path(self.config["runs_root"]) / "experience.jsonl")

    def run(self, request, input_keys=None, *, input_file=None, run_dir=None, allow_custom=True, source=None, constraints=None):
        if not request.strip():
            raise ValueError("Request must not be empty")
        root = Path(run_dir or Path(self.config["runs_root"]) / ("run-" + uuid.uuid4().hex[:12])).resolve()
        if root.exists() and (root / "request.json").exists():
            raise ValueError("Run already exists; use resume")
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        input_file = Path(input_file or ROOT / "examples/input.jsonl").resolve()
        if input_file.stat().st_size > self.config.get("max_input_bytes", 2_000_000):
            raise ValueError("Input exceeds the configured sample execution budget")
        rows = [json.loads(line) for line in input_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not rows or not all(isinstance(r, dict) for r in rows):
            raise ValueError("Input must be nonempty JSONL objects")
        if len(rows) > self.config.get("max_input_rows", 10000):
            raise ValueError("Input exceeds configured row budget")
        keys = input_keys or list(rows[0])
        if any(set(keys) - set(row) for row in rows):
            raise ValueError("Input rows do not have the declared columns")
        shutil.copyfile(input_file, root / "input.jsonl")
        write_json(root / "request.json", {"request":request, "input_keys":keys, "allow_custom":allow_custom,
                   "source":source or {"type":"cli"}, "constraints":constraints or {}, "sample":rows[:6],
                   "input_rows":len(rows), "input_sha256":file_hash(root / "input.jsonl")})
        return self.resume(root)

    def resume(self, run_dir):
        root = Path(run_dir).resolve()
        with (root / ".leader.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another leader is running this team") from exc
            store = TeamStore(root)
            team = CodexTeamRuntime(store, self.backend, self.config.get("agent_attempts", 2))
            try:
                result = self._workflow(root, store, team)
            except Exception as exc:
                store.checkpoint("BLOCKED", error=str(exc)[-2400:])
                self.memory.add({"kind":"failure", "run":str(root), "request":json.loads((root / "request.json").read_text()).get("request"),
                                 "error":str(exc)[-1600:], "backend":self.config["backend"]})
                result = {"state":"BLOCKED", "run_dir":str(root), "error":str(exc)[-2400:]}
            finally:
                store.export()
            write_json(root / "result.json", result)
            return result

    def _workflow(self, root, store, team):
        request = json.loads((root / "request.json").read_text())
        if file_hash(root / "input.jsonl") != request["input_sha256"]:
            raise ValueError("Input snapshot changed; start a new run")
        catalog = discover_operator_catalog(self.config["dataflow_root"])
        version = catalog_version(catalog)
        write_json(root / "catalog.json", {"catalog_version":version, "operators":catalog})
        provenance = {"catalog_version":version, "backend":self.config["backend"],
                      "role_contracts":digest({"schemas":SCHEMAS, "prompts":PROMPTS}),
                      "model":os.getenv("CODEX_MODEL", self.config.get("model")),
                      "skills":{p.parent.name:file_hash(p) for p in (ROOT / ".agents/skills").glob("*/SKILL.md")}}
        shared = {"request":request["request"], "input_keys":request["input_keys"], "constraints":request["constraints"],
                  "sample":request["sample"], "provenance":provenance, "allow_custom":request["allow_custom"], "resources":self.config.get("resources", {})}
        store.checkpoint("PLANNING", backend=self.config["backend"], catalog_version=version,
                         summary="Planner 正在分析请求、输入字段和 DataFlow 算子目录")
        experience_path = root / "retrieval.json"
        if not experience_path.exists():
            write_json(experience_path, self.memory.search(request["request"], 3))
        experience = json.loads(experience_path.read_text())
        store.event("skill.invoked", "planner", skill="pipeline-planning")
        store.event("rag.retrieve", "planner", results=len(experience), source="experience.jsonl")
        overview = [{k:v for k,v in op.items() if k in {"name","category","input_parameters","output_parameters"}}
                    | {"description":op["description"][:350]} for op in catalog]
        plan = team.ask("planner", "planner", dict(shared, catalog=overview, experience=experience), SCHEMAS["planner"])
        # Resource registration is an execution concern. Some model responses
        # still mark a fully decomposed plan unsupported solely because the
        # referenced LLM resource is absent; retain that plan and defer the
        # block to the resource-aware executor.
        reason_text = str(plan.get("reason", "")).lower()
        resource_only = any(term in reason_text for term in ("resource", "llm", "model", "api"))
        if not plan.get("supported") and plan.get("steps") and (resource_only or _catalog_covers_plan(plan, catalog)):
            plan["supported"] = True
            suffix = (" Resource registration will be checked before execution."
                      if resource_only else
                      " The catalog covers the semantic steps; field-level validation will be repaired by specialists and the compiler.")
            plan["reason"] = plan.get("reason", "") + suffix
        write_json(root / "plan.json", plan)
        if not plan["supported"]:
            store.checkpoint("REFUSED", reason=plan["reason"], summary="Planner 判断当前请求无法安全映射到可验证的 DataFlow pipeline")
            return {"state":"REFUSED", "reason":plan["reason"], "run_dir":str(root)}
        steps = ordered_steps(plan)
        store.checkpoint("BINDING", count=len(steps), summary=f"正在为 {len(steps)} 个步骤并行检索和绑定算子")

        def bind(step):
            # Keep enough source-grounded candidates for domain generators;
            # the old top-8 cutoff could hide ReasoningQuestionGenerator behind
            # unrelated evaluators and trigger unnecessary custom code.
            matches = search_catalog(step["query"] + " " + step["objective"], catalog, limit=24)
            store.event("tool.called", "operator_specialist", step["step_id"],
                        tool="operator_registry.lookup", matches=[m["name"] for m in matches], catalog_version=version)
            store.event("skill.invoked", "operator_specialist", step["step_id"], skill="operator-discovery")
            specialist_prompt = dict(shared, step=step, candidates=with_sources(matches, self.config["dataflow_root"]))
            last = None
            for repair in range(self.config.get("specialist_repairs", 2) + 1):
                if repair:
                    specialist_prompt["repair_feedback"] = last
                binding = team.ask("operator_specialist", "specialist-" + step["step_id"] + f"-{repair}", specialist_prompt, SCHEMAS["operator_specialist"])
                if binding.get("operator") or binding.get("proposal"):
                    return binding
                last = "Empty binding is invalid. Choose an existing catalog operator using a $resource placeholder when needed, or provide a complete registered OperatorABC proposal."
            fallback = _fallback_binding(step, catalog)
            if fallback:
                store.event("workflow.repair", "leader", job=step["step_id"], reason="deterministic source-grounded fallback")
                return fallback
            raise ValueError(f"Specialist could not bind step {step['step_id']}: {last}")
        with ThreadPoolExecutor(max_workers=min(self.config.get("max_parallel_agents", 3), len(steps))) as pool:
            bindings = list(pool.map(bind, steps))
        for binding in bindings:
            if binding["proposal"] and not request["allow_custom"]:
                raise ValueError("Custom operator proposed but custom generation is disabled")
        write_json(root / "bindings.json", bindings)
        store.checkpoint("INTEGRATING", summary="Integrator 正在对齐字段、参数和步骤依赖")
        selected_names = {b["operator"] for b in bindings}
        contracts = [o for o in catalog if o["name"] in selected_names]
        feedback = []
        for repair in range(self.config.get("max_repairs", 1) + 1):
            store.event("skill.invoked", "pipeline_integrator", skill="schema-alignment", repair=repair)
            integrated = team.ask("pipeline_integrator", f"integrator-{repair}",
                                 dict(shared, plan=plan, bindings=bindings, contracts=contracts, validation_feedback=feedback),
                                 SCHEMAS["pipeline_integrator"])
            # Do not allow an integrator to replace a known catalog operator
            # with ad-hoc code for a standard reasoning transformation.
            for index, step in enumerate(plan["steps"]):
                candidate = _fallback_binding(step, catalog)
                current = integrated["bindings"][index] if index < len(integrated["bindings"]) else None
                if candidate and current and current.get("proposal") and candidate["operator"] in {"ReasoningQuestionGenerator", "ReasoningAnswerGenerator", "ReasoningQuestionFilter", "ReasoningAnswerNgramFilter"}:
                    candidate["rationale"] = "Replaced invalid custom proposal with the registered DataFlow operator."
                    integrated["bindings"][index] = candidate
            try:
                spec, errors = compile_spec(plan, integrated, catalog, request["input_keys"], self.config.get("resources"))
            except Exception as exc:
                spec, errors = None, [str(exc)[-2400:]]
                store.event("workflow.validation_failed", "pipeline_integrator", repair=repair, error=errors[0])
            write_json(root / "static-validation.json", {"passed":not errors, "errors":errors, "repair":repair})
            if errors:
                feedback = errors
                if repair < self.config.get("max_repairs", 1):
                    continue
                raise ValueError("; ".join(errors))
            write_json(root / "pipeline-spec.json", spec)
            write_pipeline_sources(root, spec, request["request"])
            for b in spec["steps"]:
                if b["proposal"]:
                    path = root / b["source_file"]
                    store.event("skill.invoked", "operator_specialist", b["step_id"], skill="operator-scaffolding", source_hash=file_hash(path))
            if not self.config.get("auto_execute", False):
                store.checkpoint("READY", backend=self.config["backend"],
                                 summary="Pipeline 已生成并通过静态检查，尚未执行")
                return {"state": "READY", "backend": self.config["backend"], "run_dir": str(root),
                        "pipeline": str(root / "pipeline.py"), "executed": False,
                        "operators": [b["operator"] for b in spec["steps"]]}
            store.checkpoint("VALIDATING", summary="正在编译并执行 DataFlow pipeline，等待 Verifier 检查证据")
            store.event("tool.called", "verifier", tool="pipeline.compile_and_run")
            runtime = execute(root, self.config)
            write_json(root / "runtime-report.json", runtime)
            if runtime["status"] == "resource_required":
                store.checkpoint("RESOURCE_REQUIRED", resources=runtime["resources"], summary="等待注册或配置 DataFlow API resource")
                reason = runtime.get("error", "请先注册并配置 pipeline 所引用的 API resource")
                return {"state":"RESOURCE_REQUIRED", "backend":self.config["backend"], "run_dir":str(root),
                        "resources":runtime["resources"], "reason":reason}
            if runtime["status"] == "approval_required":
                store.checkpoint("APPROVAL_REQUIRED", reasons=runtime["operators"])
                return {"state":"APPROVAL_REQUIRED", "run_dir":str(root), "operators":runtime["operators"],
                        "approval_request":str(root / "approval-request.json")}
            candidate = root / "candidate.jsonl"
            output = [json.loads(line) for line in candidate.read_text().splitlines() if line.strip()] if candidate.exists() and runtime["status"] == "passed" else []
            store.event("skill.invoked", "verifier", skill="verification-evidence")
            verdict = team.ask("verifier", f"verifier-{repair}",
                              dict(shared, plan=plan, pipeline=spec, runtime=runtime, output=output[:20],
                                   output_rows=len(output)), SCHEMAS["verifier"])
            write_json(root / "verification.json", verdict)
            if runtime["status"] == "passed" and verdict["verdict"] == "pass":
                shutil.copyfile(candidate, root / "output.jsonl")
                hashes = manifest(root)
                hashes["output.jsonl"] = file_hash(root / "output.jsonl")
                hashes["verification.json"] = file_hash(root / "verification.json")
                hashes["runtime-report.json"] = file_hash(root / "runtime-report.json")
                write_json(root / "integrity.json", hashes)
                store.checkpoint("VERIFIED", backend=self.config["backend"], output_rows=len(output), summary=f"验证通过，输出 {len(output)} 行结果")
                self.memory.add({"kind":"verified_pipeline", "request":request["request"], "catalog_version":version,
                                 "operators":[b["operator"] for b in spec["steps"]], "run":str(root),
                                 "evidence_digest":digest(hashes), "backend":self.config["backend"]})
                return {"state":"VERIFIED", "backend":self.config["backend"], "run_dir":str(root), "output":str(root / "output.jsonl"),
                        "pipeline":str(root / "pipeline.py"), "rows":len(output), "operators":[b["operator"] for b in spec["steps"]]}
            feedback = [runtime.get("error", ""), verdict["reason"], *verdict["issues"]]
            bindings = integrated["bindings"]
            if repair < self.config.get("max_repairs", 1):
                store.event("workflow.repair", "leader", issues=feedback)
        raise ValueError("Verification failed: " + "; ".join(feedback))

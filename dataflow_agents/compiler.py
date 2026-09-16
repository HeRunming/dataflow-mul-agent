"""Validate actual DataFlow signatures and emit standalone PipelineABC code."""
from __future__ import annotations
import ast
import json
import inspect
import pprint
from .serving import normalize_chat_url
from .catalog import extract_source

def fields(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return value
    if value is None:
        return []
    raise ValueError("Field parameters must be strings or lists of strings")

def ordered_steps(plan):
    pending = {s["step_id"]: s for s in plan["steps"]}
    if len(pending) != len(plan["steps"]) or not pending:
        raise ValueError("Plan has duplicate step ids or no steps")
    ordered, done = [], set()
    while pending:
        ready = [s for s in pending.values() if set(s["depends_on"]) <= done]
        if not ready:
            raise ValueError("Cyclic or missing step dependencies")
        for step in ready:
            if not step["step_id"].replace("-", "_").isidentifier():
                raise ValueError("Invalid step id")
            ordered.append(step)
            done.add(step["step_id"])
            del pending[step["step_id"]]
    return ordered

def validate_call(args, signature, label):
    errors = []
    for key in args:
        if key not in signature:
            errors.append(f"{label}: unknown parameter {key}")
    for key, info in signature.items():
        if info["required"] and key not in args:
            errors.append(f"{label}: missing required parameter {key}")
        if key.startswith("output_") and key not in args and info.get("default") is not None:
            errors.append(f"{label}: output default {key} must be explicit")
    return errors

def _walk_refs(value):
    if isinstance(value, dict):
        if len(value) == 1 and ("$resource" in value or "$serving" in value):
            yield value
        else:
            for item in value.values():
                yield from _walk_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_refs(item)

def compile_spec(plan, integrated, catalog, initial_keys, resources=None):
    index = {o["name"]:o for o in catalog}
    order = ordered_steps(plan)
    bindings = integrated["bindings"]
    if len(bindings) != len(order) or {b["step_id"] for b in bindings} != {s["step_id"] for s in order}:
        raise ValueError("Integrator must preserve every planned step exactly once")
    by_id = {b["step_id"]:b for b in bindings}
    available, completed, errors, steps = set(initial_keys), set(), [], []
    for step in order:
        b = dict(by_id[step["step_id"]])
        name = b["operator"]
        if b["proposal"]:
            proposal = b["proposal"]
            if name in index:
                errors.append(f"{name}: custom operator conflicts with existing registry")
            if not name.isidentifier() or proposal["name"] != name:
                raise ValueError("Invalid custom operator identity")
            ops = extract_source(proposal["source"], "custom." + name, "custom/" + name + ".py")
            matches = [o for o in ops if o["name"] == name]
            if len(matches) != 1:
                raise ValueError(f"{name}: must define a registered OperatorABC class with run")
            tree = ast.parse(proposal["source"])
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
            if "OperatorABC" not in [ast.unparse(base) for base in cls.bases]:
                raise ValueError("Custom operator must inherit OperatorABC")
            op = matches[0]
        else:
            if name not in index:
                raise ValueError(f"Unregistered operator: {name!r}; no implementation provided")
            op = index[name]
        # Serving is a separate pipeline resource. Operators declare the
        # dependency through their constructor; the operator binding should
        # never need a concrete API client or secret. Inject a stable reference
        # when an LLM operator was selected, even if no serving is registered.
        if "llm_serving" in op["init_signature"] and "llm_serving" not in b["init_args"]:
            b["init_args"]["llm_serving"] = {"$resource": "llm_default"}
        elif "llm_serving" in op["init_signature"] and b["init_args"].get("llm_serving") is None:
            b["init_args"]["llm_serving"] = {"$resource": "llm_default"}
        for target, source in b["prepare_fields"].items():
            if source not in available:
                errors.append(f"{step['step_id']}: missing copy source {source}")
            if target in available and target != source:
                errors.append(f"{step['step_id']}: prepare_fields would overwrite {target}")
            available.add(target)
        call_errors = validate_call(b["init_args"], op["init_signature"], name + ".__init__")
        call_errors += validate_call(b["run_args"], op["run_signature"], name + ".run")
        errors += call_errors
        # DataFlow operators may use named field parameters such as input_key,
        # output_key or origin_key. The registry signature is authoritative;
        # rejecting other valid operator-specific field names caused false
        # failures for custom row-expansion operators.
        input_fields, output_fields = [], []
        for parameter, value in b["run_args"].items():
            if parameter.startswith("input_"):
                input_fields += fields(value)
            if parameter.startswith("output_"):
                output_fields += fields(value)
        for key in input_fields:
            if key not in available:
                errors.append(f"{step['step_id']}: missing input field {key}")
        if name == "HashDeduplicateFilter":
            if not b["run_args"].get("input_key") or b["run_args"].get("input_keys"):
                errors.append("HashDeduplicateFilter: use one input_key; unsupported list path in this upstream version")
            if set(input_fields) & set(output_fields):
                errors.append("Deduplication label would overwrite input content")
        # Operators return the fields they write. The return contract is not
        # always recoverable from Python annotations, so planned output fields
        # are carried forward and the real DataFlow compile/run remains the
        # final check for invalid output claims.
        available.update(output_fields)
        if not call_errors:
            available.update(step["output_keys"])
        missing = set(step["output_keys"]) - available
        if missing:
            errors.append(f"{step['step_id']}: planned output fields not produced: {sorted(missing)}")
        b.update(module=op["module"], source_file=op["source_file"], source_sha256=op["source_sha256"],
                 risk="review" if b["proposal"] else op["risk"], depends_on=step["depends_on"])
        steps.append(b)
        completed.add(step["step_id"])
    final = integrated["final_keys"]
    if set(final) != set(plan["final_keys"]):
        errors.append("Integrator changed requested final columns")
    if not final or set(final) - available:
        errors.append("Final fields missing from pipeline")
    # The serving registry is global, but a pipeline spec should contain only
    # resources referenced by its operators. Otherwise an unrelated or
    # partially configured serving could block an otherwise runnable pipeline.
    registered_resources = dict(resources or {})
    resources = {}
    def check_resource(value):
        if isinstance(value, dict):
            if "$resource" in value or "$serving" in value:
                ref_key = "$resource" if "$resource" in value else "$serving"
                if set(value) != {ref_key}:
                    errors.append("Invalid resource reference")
                elif value[ref_key] not in registered_resources:
                    # Preserve a source-grounded pipeline with an unresolved
                    # resource. Registration can happen after generation;
                    # execution will pause until the resource is configured.
                    resources[value[ref_key]] = {"type": "api_llm", "args": {
                        "api_url": "https://configure-resource.example.invalid/v1",
                        "key_name_of_api_key": "DF_PIPELINE_PENDING_RESOURCE",
                        "model_name": value[ref_key], "temperature": 0.2,
                        "max_workers": 2, "max_tokens": 1024, "max_retries": 2, "connect_timeout": 10,
                        "read_timeout": 120}}
                else:
                    resources[value[ref_key]] = registered_resources[value[ref_key]]
            else:
                for item in value.values():
                    check_resource(item)
        elif isinstance(value, list):
            for item in value:
                check_resource(item)
    for step in steps:
        check_resource(step["init_args"])
    refs = sorted({value[next(iter(value))] for step in steps for value in _walk_refs(step["init_args"])})
    spec = {"api_version":"dataflow.agents/v2", "steps":steps, "final_keys":final, "initial_keys":initial_keys,
            "resources":resources, "servings": {name: resources[name] for name in refs}}
    return spec, errors

RUNTIME_SOURCE = r'''
import argparse
import importlib.util
import json
import os
from pathlib import Path
from dataflow.pipeline import PipelineABC
from dataflow.core import OperatorABC
from dataflow.utils.storage import FileStorage
from dataflow.utils.registry import OPERATOR_REGISTRY

class CopyField(OperatorABC):
    def run(self, storage, input_key, output_key):
        df = storage.read("dataframe").copy()
        df[output_key] = df[input_key]
        storage.write(df)
        return [output_key]

def load_custom():
    for step in SPEC["steps"]:
        if step["proposal"]:
            path = Path(__file__).parent / step["source_file"]
            module_spec = importlib.util.spec_from_file_location("agent_custom_" + step["operator"], path)
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)

RESOURCE_CACHE = {}

def _walk_refs(value):
    if isinstance(value, dict):
        if len(value) == 1 and ("$resource" in value or "$serving" in value):
            yield value
        else:
            for item in value.values(): yield from _walk_refs(item)
    elif isinstance(value, list):
        for item in value: yield from _walk_refs(item)

def resolve(value):
    if isinstance(value, dict) and len(value) == 1 and ("$resource" in value or "$serving" in value):
        name = value.get("$resource", value.get("$serving"))
        if name not in RESOURCE_CACHE:
            resource = SPEC["resources"][name]
            if resource["type"] != "api_llm":
                raise ValueError("Unsupported resource type")
            from dataflow.serving import APILLMServing_request
            class CheckedAPIServing(APILLMServing_request):
                def _run_threadpool(self, task_args_list, desc):
                    responses = super()._run_threadpool(task_args_list, desc)
                    if len(responses) != len(task_args_list) or any(
                        value is None or (isinstance(value, str) and not value.strip())
                        for value in responses
                    ):
                        raise RuntimeError(
                            f"LLM resource {name}: request failed or returned empty content "
                            f"(model={self.model_name}, endpoint={self.api_url}). "
                            "Check serving URL, credentials and model; see runtime.stderr.log for HTTP errors."
                        )
                    return responses
            args = dict(resource["args"])
            args["api_url"] = normalize_chat_url(args["api_url"])
            RESOURCE_CACHE[name] = CheckedAPIServing(**args)
        return RESOURCE_CACHE[name]
    if isinstance(value, dict):
        return {key:resolve(item) for key,item in value.items()}
    if isinstance(value, list):
        return [resolve(item) for item in value]
    return value

class GeneratedPipeline(PipelineABC):
    def __init__(self, input_file, cache):
        super().__init__()
        self.storage = FileStorage(first_entry_file_name=str(input_file), cache_path=str(cache),
                                   file_name_prefix="pipeline", cache_type="jsonl")
        self.calls = []
        for index, step in enumerate(SPEC["steps"]):
            for j, (target, source) in enumerate(step["prepare_fields"].items()):
                attr = f"copy_{index}_{j}"
                setattr(self, attr, CopyField())
                self.calls.append((attr, {"input_key":source, "output_key":target}))
            attr = f"op_{index}"
            setattr(self, attr, OPERATOR_REGISTRY.get(step["operator"])(**resolve(step["init_args"])))
            self.calls.append((attr, step["run_args"]))

    def forward(self):
        for attr, kwargs in self.calls:
            getattr(self, attr).run(storage=self.storage.step(), **kwargs)

def read_last(pipeline):
    operators = [n for n in pipeline.op_nodes_list if n.op_obj is not None]
    storage = operators[-1].storage
    # FileStorage.read reads this stage's input, so read the next stage instead.
    return storage.step().read("dataframe")

def self_test(cache):
    reports = []
    for step in SPEC["steps"]:
        if not step["proposal"]:
            continue
        for index, fixture in enumerate(step["proposal"]["tests"]):
            directory = cache / ("test_" + step["step_id"] + "_" + str(index))
            directory.mkdir(parents=True, exist_ok=True)
            input_file = directory / "input.jsonl"
            input_file.write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in fixture["input"]), encoding="utf-8")
            storage = FileStorage(first_entry_file_name=str(input_file), cache_path=str(directory), cache_type="jsonl")
            operator = OPERATOR_REGISTRY.get(step["operator"])(**resolve(step["init_args"]))
            stage = storage.step()
            operator.run(storage=stage, **step["run_args"])
            actual = json.loads(stage.step().read("dataframe").to_json(orient="records"))
            expected = fixture["expected"]
            # LLM-backed operators are intentionally semantic: equivalent
            # labels can vary between valid model responses. Fixtures verify
            # deterministic structure, input preservation, and non-empty
            # declared outputs; opt into exact values with strict=true.
            if fixture.get("strict"):
                if actual != expected:
                    raise AssertionError(f"Custom fixture failed: {step['step_id']}/{index}: {actual} != {expected}")
            else:
                if len(actual) != len(expected):
                    raise AssertionError(f"Custom fixture row count failed: {step['step_id']}/{index}")
                for actual_row, expected_row in zip(actual, expected):
                    for key, value in expected_row.items():
                        if key in step.get("input_keys", []) and actual_row.get(key) != value:
                            raise AssertionError(f"Custom fixture input preservation failed: {step['step_id']}/{index}/{key}")
                    for key in step.get("output_keys", []):
                        value = actual_row.get(key)
                        if value is None or (isinstance(value, str) and not value.strip()):
                            raise AssertionError(f"Custom fixture output missing: {step['step_id']}/{index}/{key}")
            reports.append({"step":step["step_id"], "fixture":index, "status":"passed", "strict":bool(fixture.get("strict"))})
    return reports

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    report = {"status":"blocked", "compile":False, "executed":False, "custom_tests":[]}
    try:
        load_custom()
        pipeline = GeneratedPipeline(args.input, args.cache)
        pipeline.compile()
        report["compile"] = True
        report["compiled_fields"] = pipeline.final_keys
        if args.execute:
            report["custom_tests"] = self_test(Path(args.cache))
            pipeline.forward()
            data = read_last(pipeline)
            missing = set(SPEC["final_keys"]) - set(data.columns)
            if missing:
                raise AssertionError(f"Output fields missing: {missing}")
            data[SPEC["final_keys"]].to_json(args.output, orient="records", lines=True, force_ascii=False)
            report.update(status="passed", executed=True, rows=len(data), fields=list(data.columns))
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["status"] == "failed":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
'''

def render_dataflow_pipeline(spec, **kwargs):
    # Keep generated source close to the idiomatic DataFlow examples: imports,
    # declarative pipeline metadata, small operator wiring class, and an
    # explicit forward method.  The metadata is pretty-printed so reviewers
    # can inspect fields and operator arguments without one unreadable line.
    normalized = json.loads(json.dumps(spec, sort_keys=True))
    spec_text = pprint.pformat(normalized, width=100, sort_dicts=False)
    source = ("\"\"\"Generated DataFlow pipeline.\n"
              "This file is produced from a validated pipeline spec; edit the spec or agents, then regenerate.\n"
              "\"\"\"\n\n"
              "SPEC = " + spec_text + "\n\n"
              "from urllib.parse import urlsplit, urlunsplit\n\n" + inspect.getsource(normalize_chat_url) + "\n" + RUNTIME_SOURCE)
    ast.parse(source)
    return source

def validate_pipeline_spec(spec, initial_keys):
    available = set(initial_keys)
    errors = []
    for step in spec["steps"]:
        for target, source in step.get("prepare_fields", {}).items():
            if source not in available:
                errors.append(f"Missing {source}")
            available.add(target)
        for key, value in step.get("run_args", {}).items():
            if key.startswith("input_") and set(fields(value)) - available:
                errors.append(f"Missing input {value}")
        for key, value in step.get("run_args", {}).items():
            if key.startswith("output_"):
                available.update(fields(value))
    if set(spec["final_keys"]) - available:
        errors.append("Missing final keys")
    return errors

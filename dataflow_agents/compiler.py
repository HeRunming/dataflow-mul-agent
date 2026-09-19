"""Validate a plan against actual DataFlow signatures and freeze it as a spec."""
from __future__ import annotations
import ast
import copy
from .prompt_templates import OPERATOR_PROMPTS, normalize_prompt
from .catalog import extract_source
from .codegen import render_dataflow_pipeline, render_pipeline_runner  # re-exported

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


def normalize_operator_defaults(spec):
    """Normalize registered reasoning prompt defaults/references without mutation."""
    normalized = copy.deepcopy(spec)
    for step in normalized.get("steps", []):
        operator = step.get("operator")
        if operator in OPERATOR_PROMPTS and not step.get("proposal"):
            args = step.setdefault("init_args", {})
            args["prompt_template"] = normalize_prompt(operator, args.get("prompt_template"))
    return normalized

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
        b.update(module=op["module"], import_path=op.get("import_path") or op["module"],
                 source_file=op["source_file"], source_sha256=op["source_sha256"],
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
    return normalize_operator_defaults(spec), errors

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

"""Read DataFlow source contracts without importing heavy optional dependencies."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import warnings
from pathlib import Path
from .team import digest, write_json

SAFE_CPU = {"RemoveExtraSpacesRefiner", "LowercaseRefiner", "TextNormalizationRefiner",
            "HashDeduplicateFilter", "HtmlEntityRefiner"}

def signature(fn):
    if fn is None:
        return {}
    args = fn.args.posonlyargs + fn.args.args
    defaults = [None] * (len(args) - len(fn.args.defaults)) + list(fn.args.defaults)
    pairs = list(zip(args, defaults)) + list(zip(fn.args.kwonlyargs, fn.args.kw_defaults))
    result = {}
    for arg, default in pairs:
        if arg.arg in {"self", "cls", "storage"}:
            continue
        entry = {"required": default is None, "annotation": ast.unparse(arg.annotation) if arg.annotation else ""}
        if default is not None:
            try:
                entry["default"] = ast.literal_eval(default)
            except (ValueError, TypeError):
                entry["expression"] = ast.unparse(default)
        result[arg.arg] = entry
    return result

def extract_source(source, module, source_file):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    found = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any("OPERATOR_REGISTRY.register" in ast.unparse(d) for d in node.decorator_list):
            continue
        methods = {m.name: m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if "run" not in methods:
            continue
        desc = methods.get("get_desc")
        strings = [n.value for n in ast.walk(desc) if isinstance(n, ast.Constant) and isinstance(n.value, str)] if desc else []
        description = " ".join(strings) or ast.get_docstring(node) or node.name
        run = signature(methods["run"])
        found.append({"name": node.name, "module": module, "source_file": source_file,
                      "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                      "description": description[:6000], "category": ".".join(module.split(".")[2:-1]),
                      "init_signature": signature(methods.get("__init__")), "run_signature": run,
                      "input_parameters": [k for k in run if k.startswith("input_")],
                      "output_parameters": [k for k in run if k.startswith("output_")],
                      "risk": "low" if node.name in SAFE_CPU else "review",
                      "registered": True})
    return found

def discover_operator_catalog(dataflow_root):
    root = Path(dataflow_root).resolve()
    if not (root / "dataflow/operators").is_dir():
        raise ValueError(f"DataFlow repository not found: {root}")
    result = []
    for path in sorted((root / "dataflow/operators").rglob("*.py")):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(root)
        result.extend(extract_source(path.read_text(encoding="utf-8"), ".".join(rel.with_suffix("").parts), rel.as_posix()))
    return result

def catalog_version(catalog):
    return "cat-" + digest(catalog)[:16]

def save_catalog(catalog, path):
    payload = {"catalog_version": catalog_version(catalog), "operators": catalog}
    write_json(path, payload)
    return payload

def load_catalog(path=None):
    path = Path(path or Path(__file__).parents[1] / "catalog/operators.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data["operators"]

def search_catalog(query, catalog, limit=8):
    aliases = {"清洗": "spaces clean refine", "去重": "hash deduplicate", "规范化": "normalization",
               "空格": "spaces", "小写": "lowercase", "语言": "language", "脱敏": "pii anonymize",
               "日期": "date normalization", "问答": "question answer qa", "分块": "chunk"}
    expanded = query
    for word, terms in aliases.items():
        if word in query:
            expanded += " " + terms
    terms = set(re.findall(r"[a-zA-Z0-9]+|[\\u4e00-\\u9fff]", expanded.lower()))
    ranked = []
    for op in catalog:
        name = op["name"].lower()
        doc = (op["description"] + " " + op["category"]).lower()
        score = sum(5 * (t in name) + (t in doc) for t in terms if len(t) > 1 or ord(t[0]) > 127)
        if score:
            ranked.append((score, op))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
    return [op for _, op in ranked[:limit]]

def with_sources(matches, dataflow_root):
    result = []
    for op in matches:
        source = (Path(dataflow_root) / op["source_file"]).read_text(encoding="utf-8")
        if hashlib.sha256(source.encode()).hexdigest() != op["source_sha256"]:
            raise ValueError(f"Stale catalog: {op['name']}")
        result.append(dict(op, source=source[:32000]))
    return result

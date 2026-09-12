import argparse
import json
import sys
from pathlib import Path
from .catalog import discover_operator_catalog, save_catalog
from .execution import approve, promote, rollback
from .orchestrator import Orchestrator, load_config, ROOT
from .team import TeamStore

def main():
    parser = argparse.ArgumentParser(description="Multiple Codex agents for executable DataFlow pipelines")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("request")
    run.add_argument("--input", default=str(ROOT / "examples/input.jsonl"))
    run.add_argument("--input-key", action="append", dest="input_keys")
    run.add_argument("--run-dir")
    run.add_argument("--no-custom", action="store_true")
    resume = sub.add_parser("resume")
    resume.add_argument("run_dir")
    for command in (run, resume):
        command.add_argument("--config")
        command.add_argument("--backend", choices=("codex","offline"))
        command.add_argument("--dataflow-root")
        command.add_argument("--runs-root")
        command.add_argument("--python-bin")
    catalog = sub.add_parser("catalog")
    catalog.add_argument("--dataflow-root")
    catalog.add_argument("--output", default=str(ROOT / "catalog/operators.json"))
    approval = sub.add_parser("approve")
    approval.add_argument("run_dir")
    approval.add_argument("--ttl", type=int, default=3600)
    status = sub.add_parser("status")
    status.add_argument("run_dir")
    promotion = sub.add_parser("promote")
    promotion.add_argument("run_dir")
    promotion.add_argument("--deployment", required=True)
    undo = sub.add_parser("rollback")
    undo.add_argument("--deployment", required=True)
    sub.add_parser("mcp")
    args = parser.parse_args()
    try:
        if args.command == "run" or args.command == "resume":
            cfg = load_config(args.config, backend=args.backend, dataflow_root=args.dataflow_root,
                              runs_root=args.runs_root, python_bin=args.python_bin)
            engine = Orchestrator(config=cfg)
            result = engine.run(args.request, args.input_keys, input_file=args.input,
                                run_dir=args.run_dir, allow_custom=not args.no_custom) if args.command == "run" else engine.resume(args.run_dir)
        elif args.command == "catalog":
            cfg = load_config(dataflow_root=args.dataflow_root)
            catalog = discover_operator_catalog(cfg["dataflow_root"])
            data = save_catalog(catalog, args.output)
            result = {"operators":len(catalog), "catalog_version":data["catalog_version"], "path":args.output}
        elif args.command == "approve":
            result = approve(args.run_dir, args.ttl)
            TeamStore(args.run_dir).event("approval.granted", "human", **result)
        elif args.command == "status":
            root = Path(args.run_dir)
            result = json.loads((root / "status.json").read_text())
            result["run_dir"] = str(root.resolve())
        elif args.command == "promote":
            result = promote(args.run_dir, args.deployment)
            TeamStore(args.run_dir).event("deployment.promoted", "human", deployment=args.deployment)
        elif args.command == "rollback":
            result = rollback(args.deployment)
            TeamStore(result["run"]).event("deployment.rollback", "human", deployment=args.deployment)
        else:
            from .mcp_contract import serve
            serve()
            return
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("state") in {"BLOCKED","REFUSED"}:
            raise SystemExit(2)
    except (ValueError, OSError) as exc:
        print(json.dumps({"error":str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)

if __name__ == "__main__":
    main()

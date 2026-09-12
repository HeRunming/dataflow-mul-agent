"""Read-only MCP stdio connector over the same source and evidence APIs."""
from __future__ import annotations
import json
import re
import sys
import jsonschema
from pathlib import Path
from .catalog import discover_operator_catalog, search_catalog, with_sources
from .contracts import obj, STRING, OBJECT, STRINGS, PLANNER, INTEGRATOR
from .compiler import compile_spec
from .memory import ExperienceStore
from .orchestrator import load_config
from .team import TeamStore

TOOLS = [
    {"name":"operator_registry.lookup", "description":"Search DataFlow operator source contracts",
     "inputSchema":obj({"query":STRING, "limit":{"type":"integer","minimum":1,"maximum":20}}, ["query"])},
    {"name":"pipeline.validate", "description":"Validate actual operator signatures and field dependencies without executing",
     "inputSchema":obj({"plan":PLANNER,"integration":INTEGRATOR,"input_keys":STRINGS})},
    {"name":"memory.search", "description":"Retrieve verified prior pipeline evidence",
     "inputSchema":obj({"query":STRING})},
    {"name":"evidence.get", "description":"Read a run status, verification and metrics",
     "inputSchema":obj({"run_id":STRING})},
]

def tool_contract(name):
    return next(t for t in TOOLS if t["name"] == name)

def call_tool(name, arguments, config):
    tool = tool_contract(name)
    jsonschema.validate(arguments, tool["inputSchema"])
    if name == "operator_registry.lookup":
        catalog = discover_operator_catalog(config["dataflow_root"])
        result = with_sources(search_catalog(arguments["query"], catalog, arguments.get("limit",8)), config["dataflow_root"])
    elif name == "pipeline.validate":
        catalog = discover_operator_catalog(config["dataflow_root"])
        spec, errors = compile_spec(arguments["plan"], arguments["integration"], catalog, arguments["input_keys"])
        result = {"valid":not errors, "errors":errors, "spec":spec}
    elif name == "memory.search":
        result = ExperienceStore(Path(config["runs_root"]) / "experience.jsonl").search(arguments["query"],3)
    else:
        run_id = arguments["run_id"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", run_id):
            raise ValueError("run_id must be a directory name")
        root = (Path(config["runs_root"]) / run_id).resolve()
        if not root.is_relative_to(Path(config["runs_root"]).resolve()):
            raise ValueError("Evidence path escaped runs root")
        result = {name:json.loads((root / name).read_text()) for name in
                  ("status.json","verification.json","metrics.json") if (root / name).exists()}
    audit = TeamStore(Path(config["runs_root"]) / "_tools")
    audit.event("mcp.tool", "mcp_client", tool=name, permission="local_read")
    return result

def dispatch(message, config):
    method, identifier = message.get("method"), message.get("id")
    if identifier is None:
        return None
    try:
        if method == "initialize":
            result = {"protocolVersion":"2024-11-05", "capabilities":{"tools":{"listChanged":False}},
                      "serverInfo":{"name":"dataflow-agent-tools","version":"1.0.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools":TOOLS}
        elif method == "tools/call":
            params = message.get("params", {})
            try:
                value = call_tool(params["name"], params.get("arguments",{}), config)
                result = {"content":[{"type":"text","text":json.dumps(value,ensure_ascii=False)}], "isError":False}
            except Exception as exc:
                result = {"content":[{"type":"text","text":str(exc)}], "isError":True}
        else:
            return {"jsonrpc":"2.0","id":identifier,"error":{"code":-32601,"message":"Method not found"}}
        return {"jsonrpc":"2.0","id":identifier,"result":result}
    except Exception as exc:
        return {"jsonrpc":"2.0","id":identifier,"error":{"code":-32602,"message":str(exc)}}

def serve():
    config = load_config()
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = dispatch(message,config)
        except ValueError:
            response = {"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"Parse error"}}
        if response is not None:
            print(json.dumps(response,ensure_ascii=False),flush=True)

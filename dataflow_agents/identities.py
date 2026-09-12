from dataclasses import dataclass


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    name: str
    purpose: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    tools: tuple[str, ...]
    forbidden: tuple[str, ...]


IDENTITIES = (
    AgentIdentity(
        "planner", "Pipeline Planner", "把用户目标拆成可验证的 DataFlow 步骤并判断可行性",
        ("user_request", "operator_catalog", "task_history"), ("plan", "refusal_reason"),
        ("operator_catalog.search", "rag.retrieve_runbooks"), ("execute_pipeline", "write_code"),
    ),
    AgentIdentity(
        "operator_specialist", "Operator Specialist", "为单个步骤选择最合适的已注册算子，必要时生成新算子草案",
        ("plan_step", "operator_catalog"), ("operator_binding", "operator_proposal"),
        ("operator_registry.lookup", "operator_scaffold.generate"), ("external_write", "unreviewed_execution"),
    ),
    AgentIdentity(
        "pipeline_integrator", "Pipeline Integrator", "对齐字段、拼装 Pipeline 并执行静态契约检查",
        ("plan", "operator_bindings"), ("pipeline_spec", "validation_report"),
        ("pipeline.compile", "schema_validator"), ("register_operator", "skip_validation"),
    ),
    AgentIdentity(
        "verifier", "Evidence Verifier", "验证编译/执行结果，形成可审计证据并触发回滚或人工审批",
        ("pipeline_spec", "run_result", "trace"), ("verdict", "evidence_bundle"),
        ("sandbox.run", "metrics.collect", "approval.request"), ("approve_high_risk", "delete_artifact"),
    ),
)

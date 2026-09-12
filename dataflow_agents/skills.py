from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Skill:
    name: str
    purpose: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    call_condition: str
    dependencies: tuple[str, ...]
    failure_handling: str
    security_boundary: str
    reusable_value: str
    reference: str = ""
    audit_events: tuple[str, ...] = ()


SKILLS = (
    Skill("operator_discovery", "检索并解释 DataFlow 算子", {"query": "string", "candidates": "array"}, {"binding": "object", "proposal": "object|null"}, "每个规划步骤", ("operator_registry.lookup",), "空结果先比较候选，仍无匹配才 proposal，不自动写入", "只读 DataFlow 源码和索引", "所有领域任务复用", ".agents/skills/operator-discovery/SKILL.md", ("tool.called", "agent.completed")),
    Skill("pipeline_planning", "将请求拆成 DAG 步骤", {"request": "string", "catalog": "array", "input_keys": "array"}, {"plan": "object"}, "收到新任务", ("operator_registry.lookup", "memory.search"), "明确返回 unsupported 和原因", "禁止执行和外部写入", "将自然语言变为稳定中间表示", ".agents/skills/pipeline-planning/SKILL.md", ("skill.invoked", "agent.completed")),
    Skill("operator_scaffolding", "按 DataFlow 规范生成新算子草案和 fixtures", {"step": "object", "allow_custom": "boolean"}, {"proposal": "object"}, "无可复用算子且用户允许扩展", ("operator_scaffold.generate", "pipeline.validate"), "schema、AST、fixture 和人工批准全部通过才执行", "隔离工作区，禁止生产导入", "积累领域算子资产", ".agents/skills/operator-scaffolding/SKILL.md", ("approval.requested", "tool.called")),
    Skill("schema_alignment", "对齐 input/output 字段并生成 Pipeline spec", {"plan": "object", "bindings": "array"}, {"pipeline": "object", "errors": "array"}, "所有步骤绑定后", ("pipeline.validate",), "阻断 compile 并返回冲突字段，限制 repair 次数", "只产生声明式 spec", "避免字段错接和不可复现流程", ".agents/skills/schema-alignment/SKILL.md", ("skill.invoked", "pipeline.validation")),
    Skill("verification_and_evidence", "编译/有界子进程执行验证并沉淀证据", {"pipeline": "object", "runtime": "object"}, {"verdict": "object", "evidence": "array"}, "生成 spec 后", ("pipeline.compile_and_run", "evidence.get", "metrics.collect"), "机器失败或语义不符则 blocked/fail；有限 repair 后停止", "环境变量过滤、超时、审批和 hash 完整性", "支持审计、回归和经验沉淀", ".agents/skills/verification-evidence/SKILL.md", ("pipeline.verification", "evidence.written")),
)


class SkillRegistry:
    def __init__(self, skills=SKILLS):
        self._skills = {skill.name: skill for skill in skills}

    def get(self, name: str) -> Skill:
        return self._skills[name]

    def names(self) -> list[str]:
        return sorted(self._skills)

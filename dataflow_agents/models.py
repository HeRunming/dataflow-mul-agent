from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import hashlib
import json
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EvidenceEvent:
    event_id: str
    event: str
    agent: str
    status: str
    detail: dict[str, Any] = field(default_factory=dict)
    parent_event_id: str | None = None
    timestamp: str = field(default_factory=utc_now)


@dataclass
class TaskContext:
    user_request: str
    input_keys: list[str] = field(default_factory=lambda: ["raw_content"])
    task_id: str = field(default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}")
    trace_id: str = field(default_factory=lambda: f"trace-{uuid.uuid4().hex[:12]}")
    source: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    state: str = "RECEIVED"
    events: list[EvidenceEvent] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def emit(self, event: str, agent: str, status: str, **detail: Any) -> None:
        parent = self.events[-1].event_id if self.events else None
        self.events.append(EvidenceEvent(f"evt-{uuid.uuid4().hex[:12]}", event, agent, status, detail, parent))

    def transition(self, state: str) -> None:
        allowed = {
            "RECEIVED": {"PLANNED", "REFUSED", "BLOCKED"},
            "PLANNED": {"BINDING", "REFUSED", "BLOCKED"},
            "BINDING": {"ALIGNED", "BLOCKED"},
            "ALIGNED": {"VALIDATING", "BLOCKED"},
            "VALIDATING": {"VERIFIED", "BLOCKED", "ROLLBACK_PENDING"},
            "VERIFIED": set(),
            "REFUSED": set(),
            "BLOCKED": {"ROLLBACK_PENDING"},
            "ROLLBACK_PENDING": {"BLOCKED", "VERIFIED"},
        }
        if state != self.state and state not in allowed.get(self.state, set()):
            raise ValueError(f"invalid task state transition {self.state} -> {state}")
        self.state = state

    def add_artifact(self, name: str, value: Any, *, media_type: str = "application/json") -> str:
        """Store an immutable, content-addressed artifact manifest."""
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        self.artifacts[name] = {"sha256": digest, "media_type": media_type, "value": value}
        return digest

    def request_approval(self, action: str, reason: str, *, risk: str = "high") -> dict[str, Any]:
        item = {
            "approval_id": f"approval-{uuid.uuid4().hex[:12]}",
            "action": action,
            "reason": reason,
            "risk": risk,
            "status": "pending",
            "created_at": utc_now(),
        }
        self.approvals.append(item)
        return item

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanStep:
    step_id: str
    objective: str
    input_keys: list[str]
    output_keys: list[str]
    selected_operator: str | None = None
    operator_args: dict[str, Any] = field(default_factory=dict)
    runtime_input_parameters: list[str] = field(default_factory=list)
    runtime_output_parameters: list[str] = field(default_factory=list)
    status: str = "planned"
    rationale: str = ""


@dataclass
class PipelinePlan:
    task_id: str
    supported: bool
    steps: list[PlanStep] = field(default_factory=list)
    final_keys: list[str] = field(default_factory=list)
    risk: str = "low"
    approval_required: bool = False
    refusal_reason: str | None = None
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

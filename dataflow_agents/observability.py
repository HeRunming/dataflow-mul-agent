"""OpenTelemetry-compatible trace/metric shape without a hard dependency."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Span:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    start: float = field(default_factory=time.monotonic)
    end: float | None = None
    status: str = "UNSET"

    def finish(self, status: str = "OK", **attributes: Any) -> dict[str, Any]:
        self.end = time.monotonic()
        self.status = status
        self.attributes.update(attributes)
        return {
            "name": self.name,
            "status": self.status,
            "duration_ms": round((self.end - self.start) * 1000, 2),
            "attributes": self.attributes,
        }


class Metrics:
    def __init__(self):
        self.counters: dict[str, int] = {}
        self.values: dict[str, list[float]] = {}

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def observe(self, name: str, value: float) -> None:
        self.values.setdefault(name, []).append(value)

    def snapshot(self) -> dict[str, Any]:
        return {
            "counters": dict(self.counters),
            "values": {key: {"count": len(values), "last": values[-1], "avg": sum(values) / len(values)} for key, values in self.values.items() if values},
        }

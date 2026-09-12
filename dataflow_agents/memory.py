"""Small dependency-free memory/RAG store used by the control plane.

It stores verified bindings and failure lessons as JSONL.  A production
deployment can replace this class with an MCP-backed vector store while the
Skill contract stays unchanged.
"""

from __future__ import annotations

import json
import fcntl
import re
from pathlib import Path
from typing import Any


class ExperienceStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def add(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            fcntl.flock(stream, fcntl.LOCK_UN)

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        terms = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", query.lower()))
        scored = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = json.dumps(item, ensure_ascii=False).lower()
            score = sum(term in text for term in terms)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

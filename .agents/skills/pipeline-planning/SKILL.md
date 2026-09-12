---
name: pipeline-planning
description: Decompose dataset requests into DataFlow steps with dependencies, final fields and feasibility decisions.
---

Read the versioned catalog and input sample. Cover every requested transformation or return supported=false with the missing prerequisite. TextNormalizationRefiner normalizes dates and currencies; it is not a generic cleaner. Return the planner schema with stable step_id, objective, English query, depends_on, input_keys, output_keys and final_keys. Do not bind operators or execute code. Input: request, catalog, experience, input fields. Output: typed plan or refusal. Tools: operator_registry.lookup, memory.search. Missing evidence means explicit refusal. The plan fans out to specialist Codex instances. Reusable across logs, billing and tickets.

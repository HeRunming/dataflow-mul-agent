---
name: schema-alignment
description: Join specialist bindings, align DataFlow columns and repair compile or runtime failures while preserving all steps.
---

Preserve each planned step_id exactly once and respect depends_on. Check actual signatures, inherited columns, copies and explicit output defaults. A filter preserves columns while reducing rows. An in-place refiner does not create a new named column without a preceding copy. Inputs: plan, bindings, selected contracts, validation_feedback. Output: bindings, final_keys, explanation. Tool: pipeline.validate; controller then compiles and executes real PipelineABC. Repair bindings without weakening validation or dropping requested transformations. Keep custom fixtures consistent with field changes. Execution and approvals are outside this role. Provides a reusable field-contract integration gate.

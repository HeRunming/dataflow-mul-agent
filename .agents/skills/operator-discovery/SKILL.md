---
name: operator-discovery
description: Select reusable DataFlow operators from source-grounded constructor and run contracts.
---

Compare source semantics, required dependencies, output fields and cost. Return step_id, operator, init_args, run_args, prepare_fields, rationale and proposal. Include every output default explicitly. Many refiners update input_key in place and reject output_key. prepare_fields maps a NEW column to an existing column before the operator. HashDeduplicateFilter writes numeric labels and filters rows: never overwrite content with its label. Its one-element input_keys path is broken in this checkout; use input_key. If an existing operator requires an LLM/API/database and resources is empty, bind it with a stable {"$resource":"llm_default"} placeholder; do not return an empty binding and do not generate custom code merely because the resource is not registered. For question synthesis use ReasoningQuestionGenerator with num_prompts=2, input_key and output_synth_or_input_flag; for reasoning use ReasoningAnswerGenerator with input_key/output_key. Inputs: step and candidates with source. Output: binding or proposal when allowed. Tool: operator_registry.lookup. Empty search requires comparing alternatives before new code. No execution or upstream edits. Independent bindings are joined by the integrator; source-based matching is reusable for every DataFlow domain.

For the registered ReasoningQuestionFilter, ReasoningQuestionGenerator and ReasoningAnswerGenerator,
use `prompt_template: null` for the default math template. To select a source-declared alternative,
use `{"$prompt":"GeneralQuestionFilterPrompt","args":{}}` with the corresponding allowed class.
Diy references require `args.prompt_template` containing the intended text. Do not pass Python
class names or constructor expressions as raw string parameters; unknown or incompatible references
are compile errors, not grounds to silently discard the requested prompt.

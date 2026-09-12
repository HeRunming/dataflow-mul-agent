---
name: verification-evidence
description: Independently compare DataFlow code and observed output with the full request and produce a strict verdict.
---

Read runtime report compile, executed, status, rows and errors. Static validation alone is not execution evidence. Require machine success before pass, then compare semantics and output fields. Numeric labels are not cleaned text. A sample validates only that sample. Inputs: request, plan, bindings, runtime report, output rows. Output: verdict pass/fail/blocked, reason, issues. Tools: pipeline.compile_and_run and evidence.get, invoked by the controller before review. Unexecuted means blocked; wrong semantics means fail. Do not approve code or overrule machine failures. Leader limits repair attempts and only publishes verified output. Reports and hashes provide reusable regression evidence.

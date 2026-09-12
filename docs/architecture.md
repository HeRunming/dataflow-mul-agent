# DataFlow 多 Codex Agent 架构

## 目标边界

控制平面把告警、工单、日志、账单、安全事件或自然语言数据处理请求转成可执行的 DataFlow `PipelineABC`。`/Users/blackbox/DataFlow` 是唯一运行时事实来源；本项目用 AST 同步 `@OPERATOR_REGISTRY.register()`、构造函数和 `run(storage,input_*,output_*)` 签名，不导入所有可选模型依赖。每次 run 固定 catalog/source hash，防止上游变更后继续执行旧计划。

## 多 Codex Agent 编排

系统包含四个独立 Codex 身份：`Pipeline Planner` 输出带 `step_id/depends_on/input_keys/output_keys/final_keys` 的计划或结构化拒绝；`Operator Specialist` 按 step 检索源码契约，返回已有 binding 或完整新算子 proposal；`Pipeline Integrator` 在 specialist fan-out 完成后保留每个 step，修复字段、参数和依赖；`Evidence Verifier` 独立比较静态校验、真实 compile/execute、结果行和原始请求。

每个 Agent 调用都是独立的 `codex exec --json --ephemeral` 子进程。`dataflow_agents/orchestrator.py` 是主控：Planner 完成后用线程池并行启动 Specialist，所有结果 join 后再调用 Integrator。默认在静态检查后结束生成；仅显式启用 `auto_execute` 的非 Web 流程继续执行并调用 Verifier。`dataflow_agents/team.py` 的 `CodexTeamRuntime` 只负责持久化 job、输入输出、重试、缓存和审计，不依赖外部多智能体 SDK。若未来需要替换 transport，只需替换 `AgentBackend`，角色、JSON Schema、状态和 evidence 契约保持不变。

SQLite 是 join 和恢复的状态权威，`jobs.json/events.jsonl` 保存 role、attempt、cache hit、错误和父子事件。相同 `job + prompt hash` 恢复时复用已完成答案，修改输入或源码则失效。默认状态为 `PLANNING → BINDING → INTEGRATING → READY`；Web 手动运行进入 `RUNNING → EXECUTED`，拒绝为 `REFUSED`，失败为 `BLOCKED`，执行缺资源为 `RESOURCE_REQUIRED`。旧 `auto_execute` 模式保留 `VALIDATING → VERIFIED` 和 `APPROVAL_REQUIRED`。

## 端到端闭环

请求快照和 JSONL 样例 → Planner → 并行 Specialist → Integrator → AST/signature/field validation → READY。生成或保存资源不会执行 pipeline；用户点击 `Run pipeline` 后才进行真实 `PipelineABC.compile()` 和有超时限制的子进程执行，保存各阶段输出。显式 `auto_execute` 模式可继续 Verifier → artifact hash/integrity → promote。

## Skill、MCP、RAG 与可观测

五个 Skill 在 `.agents/skills/` 中，每个定义输入、输出、调用条件、工具、失败处理、边界和复用价值。Skill 是能力层；MCP 是连接层。`mcp_contract.py` 提供 stdio JSON-RPC `initialize`、`tools/list`、`tools/call`，工具包括 `operator_registry.lookup`、`pipeline.validate`、`memory.search`、`evidence.get`；参数由 JSON Schema 校验，工具调用写入 audit event，证据路径限制在 runs root。

共享 `request.json`、`plan.json`、`bindings.json`、`pipeline-spec.json`、`runtime-report.json` 和 SQLite 是短期记忆；`ExperienceStore` 以加锁 JSONL 保存 verified pipeline/failure，Planner 检索相关历史。满足共享状态 + 经验 RAG 两类上下文能力。`traces.json` 使用 `gen_ai.agent.id`、`gen_ai.operation.name`、`dataflow.job.id` 等 OpenTelemetry GenAI 风格属性；Metrics 包含 agent calls、失败、cache hit、token usage、operator reuse ratio、执行耗时和输出行数。

## DataFlow 运行契约

编译器将声明式 spec 渲染为 `PipelineABC`：每个 operator 挂到 `self.op_*`，`forward()` 调用 `storage.step()`，然后由 DataFlow `compile()` 构建 key graph。原地 refiner 由 compiler 的 `prepare_fields` 复制列后再运行；过滤算子保留列但减少行；输出列由 `final_keys` 投影。构造参数、运行参数、输出默认值和资源引用都做 allowlist 校验。

## 新算子、资源和安全

只有目录无匹配且 `allow_custom=true` 时 Specialist 才能提案。Integrator 检查继承 `OperatorABC`、注册装饰器、run field 参数、fixture；生成不需要审批，Web 点击运行即确认该次执行，不二次审批。旧 `auto_execute` 模式的批准与源码 hash 绑定。API/LLM serving 资源通过 `$resource` 引用，允许延迟注册，URL 支持 HTTP 和 HTTPS，凭据只在执行子进程环境中的 `DF_PIPELINE_*` 注入。执行器过滤环境变量、设置 timeout、保存 stdout/stderr；这是本地子进程边界，不冒充容器级沙箱。

证据包含 input/source/spec/pipeline/output/verification 的 SHA-256、role prompt/result、catalog version、事件链、metrics、trace、审批人和 TTL。只有 `VERIFIED` run 可 promote；deployment 保存 previous active manifest，rollback 先重新校验旧 output hash。

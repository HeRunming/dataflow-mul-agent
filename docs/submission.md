# 参赛提交说明

## 方案摘要

本系统将告警、工单、日志、账单、安全事件或自然语言数据处理请求统一为结构化任务，由四个独立 Codex Agent 协同生成 DataFlow Pipeline：Planner 拆解任务，Operator Specialist 并行绑定算子，Pipeline Integrator 做字段图对齐和 Pipeline spec，Evidence Verifier 做编译/有界执行验证、审批和证据沉淀。系统输出既可审查的声明式 spec，也可生成真实 `PipelineABC` 源码。

## Agent Identity

详见 [agent-identities.md](agent-identities.md)。四个 Identity 都有固定输入、输出、工具白名单和禁止动作；Agent 之间只通过带 `task_id`、`trace_id`、`step_id`、`catalog_version` 的结构化 handoff 传递上下文。

## 多 Codex Agent 编排

| 编排能力 | 本实现 |
|---|---|
| role routing | `identities.py` 的 Identity 清单与 `Orchestrator` 的角色路由 |
| task decomposition | Planner 输出 typed `PlanStep` |
| fan-out / join | `ThreadPoolExecutor` 并行派发每个 step，所有 binding 完成后进入 Integrator 屏障 |
| context passing | `TaskContext`、artifact manifest、事件父子链 |
| state tracking | `RECEIVED → PLANNED → BINDING → ALIGNED → VALIDATING → VERIFIED`，异常态为 `REFUSED/BLOCKED/ROLLBACK_PENDING` |
| retry / escalation | MCP contract 定义错误码和重试边界；高风险动作创建 approval item |

## Skill 清单

| Skill | 输入 / 输出 | 调用条件 | 失败处理和安全边界 | 复用价值 |
|---|---|---|---|---|
| `pipeline_planning` | request + catalog → typed plan/refusal | 收到任务 | 缺字段或越界则结构化拒绝；只读 | 所有企业任务入口 |
| `operator_discovery` | query → ranked bindings | 每个 step | 空结果转 proposal；只读 registry | 算子复用和能力发现 |
| `operator_scaffolding` | step → proposal/diff/tests | 无匹配且明确允许扩展 | 只能写隔离 workspace，注册前审批 | 新算子资产积累 |
| `schema_alignment` | plan + bindings → Pipeline spec | 所有 step 绑定后 | 字段图冲突阻断 compile | 防止错接字段 |
| `verification_and_evidence` | spec → verdict/evidence | spec 生成后 | compile/smoke 失败进入 blocked；高风险需审批 | 审计、回归、评估 |

Skill 的机器可读定义在 `dataflow_agents/skills.py`，工具连接契约在 `dataflow_agents/mcp_contract.py`。

## MCP 与等价迁移契约

`operator_registry.lookup`、`operator_scaffold.generate`、`pipeline.compile`、`sandbox.run` 均声明 entrypoint、参数/返回 Schema、权限、幂等性、错误码和审计字段。当前默认是本地适配器；迁移到 MCP Server 只需替换 entrypoint adapter，不改 Agent、Skill 或 handoff schema。鉴权由工具侧注入，控制平面不持久化 token。

## RAG、记忆和可观测

系统实现共享状态和经验 RAG 两类上下文能力：`TaskContext` 保存当前任务的结构化状态、artifact hash、审批和事件；`ExperienceStore` 以 JSONL 保存已验证算子绑定、失败原因和 catalog 版本，Planner 在下一次任务中检索前三条经验。`Span` 使用 `gen_ai.*` 与 `dataflow.*` 属性，`Metrics` 记录任务数、步骤数、规划延迟、算子命中、验证通过率和审批耗时；生产部署可将同一结构发送到 OTLP Collector、Tempo/ClickHouse/Prometheus。

## 安全、审批和回滚

默认只生成并静态检查 pipeline，结束于 `READY`，不执行算子、不请求审批。Web 用户点击 `Run pipeline` 即确认该次执行，后端记录 `execution.requested` 事件，无二次审批；保存 API 配置不会触发执行。执行失败进入 `BLOCKED`，缺资源进入 `RESOURCE_REQUIRED`。只有显式启用非 Web `auto_execute` 流程才保留旧审批和 Verifier 机制。只有 `VERIFIED` 结果允许晋级，回滚恢复上一版本并检查输出哈希；本地子进程有超时与环境过滤，不提供容器级隔离。

## 可运行验证

```bash
cd /Users/blackbox/dataflow-mul-agents
python3 -m unittest discover -s tests -v
python3 -m dataflow_agents.cli "把 raw_content 清洗、去重，并输出 cleaned_content"
python3 -m dataflow_agents.cli --sync-catalog --dataflow-root /Users/blackbox/DataFlow
python3 -m dataflow_agents.cli "清洗 raw_content" --render-source /tmp/generated_pipeline.py
```

真实 Codex backend 通过 `CODEX_BACKEND=codex` 开启，模型、工作目录和超时由 `config/runtime.json` 控制。API key 只从运行环境读取，不写入仓库或证据文件。

## 当前可审查产物

一次成功的 run 目录包含 `request.json`、`catalog.json`、`retrieval.json`、`plan.json`、`bindings.json`、`pipeline-spec.json`、`pipeline.py`、`static-validation.json`、`runtime-report.json`、`verification.json`、`output.jsonl`、`integrity.json`、`events.jsonl`、`jobs.json`、`metrics.json` 和 `traces.json`。`runs/codex-reuse-v2` 是本次真实 Codex 复用测试的证据样例；其 Planner、两个 Specialist、Integrator 和 Verifier 均有独立 Codex JSONL 轨迹，最终状态为 VERIFIED。

当前仓库用独立 `codex exec` 进程、SQLite mailbox、fan-out/join、状态机和审计实现协同；如果未来需要替换模型 transport，只需替换 `AgentBackend`，保留本项目的 Identity、Skill、MCP、schema 和证据契约。

## Web 交互入口

`frontend/` 是面向参赛演示的 Vue 3 + Vite 工作台，布局参考 DataFlow-WebUI 的 pipeline、chat 和执行面板，但后端只接入本项目的 `/api/v1`。用户可以提交自然语言请求和 JSON 样例，查看 Agent 事件时间线、算子 DAG 和生成源码；需要执行时点击 `Run pipeline`，随后查看各阶段数据输出。生成、恢复和资源注册均不自动执行 pipeline。FastAPI 适配层位于 `dataflow_agents/web.py`，SQLite live events 通过 SSE 暴露，完成后的 `events.jsonl`、`metrics.json`、`traces.json` 仍由原有 Orchestrator 生成。生产启动命令和 API 路径见根目录 `README.md`。

# Multi-Turn Agent Workbench 详细实施计划

## 1. 目标

当前系统已经可以把一次自然语言请求编译为 DataFlow Pipeline，并通过 Planner、Operator Specialist、Pipeline Integrator 和 Evidence Verifier 完成生成、编译、执行和验证。

下一阶段把它升级为一个可以持续对话的 Multi-Agent Workbench：用户面对一个最上层的 Codex Conversation Controller，Controller 负责理解用户意图、创建或修改任务、调度底层多智能体、解释实时进度，并在任务结束后继续接受反馈。

目标交互如下：

```text
用户：帮我清洗并去重 raw_content
  ↓
Conversation Controller
  ↓
Durable Run / Orchestrator
  ├─ Planner
  ├─ Operator Specialists
  ├─ Pipeline Integrator
  └─ Evidence Verifier
  ↓
Pipeline、运行结果和证据
  ↓
Conversation Controller 用自然语言解释结果

用户：把去重规则改成按 normalized_content
  ↓
Conversation Controller 判断这是对已有任务的修改
  ↓
创建新的 revision，复用可复用上下文，重新调度必要 Agent
```

## 2. 设计判断

这个顶层 Codex 适合做“会话控制器”和“用户代理”，不应该替代现有四个执行型 Agent。

现有 Agent 的职责边界已经比较清楚：

- Planner 负责任务拆解。
- Operator Specialist 负责算子发现和绑定。
- Pipeline Integrator 负责字段、参数和 Pipeline spec 对齐。
- Evidence Verifier 负责真实编译、执行和证据判断。

Conversation Controller 增加以下能力：

- 维护会话上下文和用户目标。
- 判断用户是在提出新任务、补充信息、修改需求、询问进度，还是要求查看证据。
- 将自然语言反馈转换为结构化 change request。
- 选择新建 Run、恢复 Run、创建 revision，或仅查询已有证据。
- 订阅底层 Run 事件，并把 Agent/Skill 状态翻译成用户可理解的进度。
- 在任务完成后总结 Pipeline、算子、输出和验证结果。
- 在失败时解释原因，并给出下一步可操作建议。

Controller 不应直接修改 Pipeline 文件、绕过 schema 校验、替代 Verifier 判定，也不应把用户的普通反馈直接拼接成 Agent prompt 而不经过结构化解析。

## 3. 核心对象和状态模型

### 3.1 Conversation

建议新增会话对象：

```json
{
  "conversation_id": "conv-...",
  "title": "Customer intent normalization",
  "created_at": "...",
  "updated_at": "...",
  "active_run_id": "run-...",
  "active_revision": 2,
  "status": "awaiting_user",
  "messages": [],
  "artifacts": [],
  "preferences": {}
}
```

会话和 Run 分开：一个 Conversation 可以包含多个 Run revision；一个 Run 仍然保持当前已有的不可变输入快照、Pipeline、事件和证据。

### 3.2 Message

每条消息应该记录：

```json
{
  "message_id": "msg-...",
  "conversation_id": "conv-...",
  "role": "user|controller|system",
  "content": "...",
  "created_at": "...",
  "intent": "new_task|clarification|revision|status_query|artifact_query|approval",
  "run_id": "run-...",
  "revision": 2
}
```

### 3.3 Change Request

用户反馈不直接覆盖旧 Run，而是先转换为结构化变更：

```json
{
  "kind": "change_request",
  "base_run_id": "run-...",
  "base_revision": 1,
  "change_type": "operator|field|input|resource|execution|output|general",
  "request": "按 normalized_content 去重",
  "affected_steps": ["step-2"],
  "reuse_policy": "reuse_unaffected",
  "requires_replan": false,
  "requires_rebind": true,
  "requires_reexecute": true
}
```

### 3.4 Conversation 状态

```text
NEW
  ↓
UNDERSTANDING
  ↓
DISPATCHED
  ↓
RUNNING
  ├─ NEEDS_INPUT
  ├─ RESOURCE_REQUIRED
  ├─ BLOCKED
  └─ COMPLETED
       ↓
AWAITING_FEEDBACK
       ↓
REVISION_REQUESTED
       ↓
DISPATCHED
```

Conversation 状态只描述用户会话；底层 Run 继续使用现有状态机。两者不要混成一套状态，避免用户等待状态和 Pipeline 执行状态互相污染。

## 4. 顶层 Controller 的工作流

### 4.1 新任务

1. 接收用户消息。
2. 读取当前 Conversation 上下文、最近消息和已有 Run 摘要。
3. 判断是否需要补充信息。
4. 如果信息足够，生成结构化 dispatch intent。
5. 创建新的 Run 或调用现有 Orchestrator。
6. 返回确认消息和可观察的进度卡片。
7. 订阅 Run SSE 事件。
8. 任务完成后读取 Pipeline、runtime report、verification 和输出摘要。
9. 生成面向用户的完成总结。

### 4.2 进度查询

用户问“现在到哪一步了”时，Controller 不重新调用 Planner，而是：

1. 读取 Conversation 的 active run。
2. 读取当前 status、events、jobs、metrics。
3. 将最近事件压缩成阶段摘要。
4. 返回当前 Agent、Skill、步骤、耗时和阻塞原因。

### 4.3 需求修改

用户说“把去重字段改成 normalized_content”时：

1. Controller 判断这是 revision，而不是新任务。
2. 固定 base run 和 revision。
3. 生成 Change Request。
4. 判断可以复用哪些结果：catalog、未受影响的 operator binding、输入快照和历史证据。
5. 新建 revision 目录，不修改旧 Run。
6. 只重新调用受影响的 Agent；如果字段图或目标发生变化，再调用 Integrator。
7. 重新执行受影响 Pipeline，并由 Verifier 生成新证据。
8. 将新 revision 设为 active，旧 revision 保留可比较和回滚。

### 4.4 不满意反馈

用户说“结果不符合预期”时，Controller 应要求或提取具体反馈：

- 哪些行不符合预期。
- 期望的字段或标签是什么。
- 是输入、算子、字段映射还是模型输出问题。
- 是否允许生成新算子。

反馈应转为可审计的 revision request，并显示“本次修改将重跑哪些步骤”。

## 5. 后端实施计划

### Phase 1：会话持久化和 Controller 基础

新增模块建议：

```text
dataflow_agents/conversation.py
dataflow_agents/controller.py
dataflow_agents/conversation_store.py
```

实现：

- Conversation、Message、Change Request 数据结构。
- SQLite conversation store，复用现有 TeamStore 的持久化思路。
- 创建会话、读取会话、追加消息、切换 active run。
- Controller 的最小规则路由：new task、status query、revision request、artifact query。
- 暂不让 Controller 自己执行任意 shell 或修改文件。

建议 API：

```text
POST /api/v1/conversations
GET  /api/v1/conversations
GET  /api/v1/conversations/{conversation_id}
POST /api/v1/conversations/{conversation_id}/messages
GET  /api/v1/conversations/{conversation_id}/stream
POST /api/v1/conversations/{conversation_id}/revisions
```

### Phase 2：Conversation Controller 与 Orchestrator 对接

实现结构化 dispatch：

```json
{
  "intent": "new_task|status_query|revision|artifact_query|clarification",
  "run_id": "...",
  "request": "...",
  "change_request": null,
  "missing_information": [],
  "response": "..."
}
```

初版可以使用确定性规则和已有状态信息，之后再接入一个专用 Codex Controller。这样不会因为 Controller 模型输出异常而破坏底层编排。

Controller 调用 Codex 时需要单独的 schema，禁止返回任意代码，只允许返回上述结构化 intent。

### Phase 3：Revision 和增量重跑

新增：

- `revision.json`
- `parent_run_id`
- `revision_number`
- `change-request.json`
- `reuse-report.json`

复用策略：

- catalog 未变化时复用 catalog。
- 输入未变化时复用 input snapshot。
- 未受影响的 operator binding 可以复用，但必须重新做 hash 和字段校验。
- Pipeline spec 发生变化时必须重新 compile。
- 运行输出和 verification 不跨 revision 直接继承。

### Phase 4：更丰富的可观测 API

补充以下聚合接口，避免前端自行拼接大量原始文件：

```text
GET /api/v1/runs/{run_id}/collaboration
GET /api/v1/runs/{run_id}/skills
GET /api/v1/runs/{run_id}/evidence
GET /api/v1/runs/{run_id}/revisions
```

`collaboration` 应返回：

- Agent 列表和状态
- 当前 job
- attempt
- duration
- cache hit
- 关联 Skill
- 关联 step
- 输入输出 artifact

`skills` 应返回：

- Skill 名称
- 描述
- 输入输出 schema
- 依赖工具
- SKILL.md hash
- 最近调用记录

## 6. 前端实施计划

### 6.1 页面信息架构

建议从当前单体 `App.vue` 逐步拆分为：

```text
frontend/src/
  api/
    client.js
    types.js
  composables/
    useConversation.js
    useRunStream.js
    useCollaboration.js
  components/
    conversation/
      ConversationPanel.vue
      MessageList.vue
      Composer.vue
    collaboration/
      AgentCollaboration.vue
      SkillTimeline.vue
      EventInspector.vue
    pipeline/
      PipelineGraph.vue
      PipelineInspector.vue
    evidence/
      EvidencePanel.vue
      RevisionList.vue
    code/
      CodeViewer.vue
```

第一阶段不必引入 Pinia；使用 composable 管理会话和 SSE，等跨页面状态确实复杂后再升级。

### 6.2 顶部对话区

增加一个持续对话面板：

- 用户消息
- Controller 回复
- 当前 Run / revision 标签
- 正在执行的 Agent 和 Skill
- 输入框支持“继续修改需求”
- 任务完成后显示“基于此结果继续修改”

Controller 回复应明确区分：

- 已理解的需求
- 当前正在执行什么
- 需要用户补充什么
- 已生成什么
- 哪些步骤失败
- 下一步建议

### 6.3 Agent Collaboration 面板

展示真实协作链路：

```text
Planner
  ├─ Specialist: step-1
  ├─ Specialist: step-2
  └─ Specialist: step-3
        ↓ join
Pipeline Integrator
        ↓
Evidence Verifier
```

每个节点显示：

- 状态
- Agent 名称
- Skill 名称
- step id
- attempt
- duration
- cache hit
- 输出摘要

### 6.4 Skill Timeline

将 `skill.invoked` 从普通事件中提升为一等 UI 元素，展示：

- Skill 名称
- 调用 Agent
- 调用时间
- 输入摘要
- 输出摘要
- 依赖工具
- Skill 版本/hash

### 6.5 Pipeline Graph

保留当前 Pipeline 结构，但升级节点信息：

- input fields
- output fields
- objective
- operator
- binding agent
- skill
- risk
- source hash
- 当前状态

第一版继续使用 Vue 原生 DOM/CSS；第二版再评估 `@vue-flow/core`，用于缩放、拖拽、边和 minimap。

### 6.6 Inspector

右侧 Inspector 分成四个 tab：

1. Agent
2. Skill
3. Contract
4. Evidence

这样用户可以从“某个结果”追溯到“哪个 Agent、使用哪个 Skill、基于哪个 Operator 契约、经过什么运行证据”。

### 6.7 实时事件

将当前 `setInterval(tick, 1800)` 改为 EventSource：

```js
const stream = new EventSource(`/api/v1/runs/${runId}/stream`)
stream.onmessage = event => applyRunEvent(JSON.parse(event.data))
stream.addEventListener('done', closeStream)
```

保留低频 polling 作为断线恢复和初始状态同步机制。

## 7. README 和 GitHub 项目主页

README 的第一屏应该让访客在 30 秒内理解：这是什么、为什么不是普通 Agent Demo、如何运行和如何查看真实证据。

建议结构：

```text
项目标题和一句话定位
Badges
截图 / GIF
核心能力
端到端协作流程图
快速启动
WebUI 说明
Agent 和 Skill 清单
真实运行产物
架构和安全边界
测试结果
目录结构
Roadmap
License
```

需要重点突出：

- 四个独立 Codex Agent。
- 五个 source-grounded Skills。
- DataFlow compile/execute 真实运行。
- SQLite mailbox、retry、cache、resume。
- pipeline、runtime、verification 和 hash evidence。
- 多轮 Conversation 和 revision 机制。

建议增加一张 Mermaid 流程图和一张 WebUI 截图；后续可以增加一个简短 GIF 展示 Agent 节点依次点亮。

## 8. 设计参考和借鉴原则

### LangSmith

借鉴 Run / Trace / Tool call / Agent timeline 的信息组织方式，重点是可追溯性，不直接复制界面。

### Temporal Web

借鉴 Workflow 状态、事件时间线、失败恢复和历史事件查看。

### Linear

借鉴 command bar、紧凑状态标签、详情 Inspector 和低噪声布局。

### Ray Dashboard

借鉴任务、资源、执行状态和性能指标的展示方式。

### Vue Flow

作为 Pipeline Graph 第二阶段候选，用于 zoom/pan、自定义节点、边和 minimap。

### Shiki

作为只读 Pipeline / Operator 源码高亮候选。暂不为了展示功能引入 Monaco。

### Vue / Web UI skills

可以参考并在后续项目本地化这些实践：

- Vue best practices：拆分大组件、将副作用移入 composable。
- Frontend design：建立明确视觉层级，避免把页面做成普通后台模板。
- Web design guidelines：focus、loading、错误、无障碍和键盘交互。
- Vue testing best practices：Vitest + Playwright 覆盖实时状态、异步 modal、会话和节点选择。

## 9. 测试计划

### 后端

- Conversation CRUD。
- Controller 对 new task、status query、revision、clarification 的路由。
- Message 顺序和幂等性。
- Run 与 Conversation 关联。
- Revision 不修改父 Run。
- 复用报告和 hash 校验。
- SSE 事件顺序、断线恢复和 done 事件。

### 前端

- 消息发送和 loading。
- SSE 事件驱动 Agent 状态。
- Planner fan-out / Integrator join 展示。
- Skill timeline 筛选。
- Pipeline 节点和 Inspector 联动。
- Revision 创建和切换。
- 断线、错误和空状态。

### 端到端

1. 提交一个新任务。
2. 看到 Controller 确认并派发。
3. 看到四类 Agent 和 Skill 事件。
4. 查看 Pipeline 和 evidence。
5. 发送修改需求。
6. 看到新 revision 创建。
7. 确认旧 revision 仍可查看和对比。

## 10. 分阶段交付顺序

### Milestone 1：可观测单次 Run

- SSE 前端接入。
- Agent collaboration panel。
- Skill timeline。
- Event Inspector。
- Pipeline 节点状态。

### Milestone 2：Conversation MVP

- Conversation store。
- Conversation API。
- 顶部对话面板。
- Controller 规则路由。
- 新任务、状态查询和基础澄清。

### Milestone 3：Revision MVP

- Change Request。
- revision 目录和 parent run。
- 复用未受影响的 catalog/bindings。
- 重新编译、执行和验证。
- Revision 列表和结果对比。

### Milestone 4：展示和开源完善

- README 重写。
- Mermaid 架构图。
- WebUI 截图/GIF。
- Demo 数据和固定演示脚本。
- GitHub 项目主页整理。

### Milestone 5：体验增强

- Pipeline Graph 引入 Vue Flow。
- 源码高亮引入 Shiki。
- 更细粒度的 metrics 和 trace 查询。
- Playwright 端到端测试。

## 11. 完成标准

### 多轮交互

- 用户可以在一个 Conversation 中连续发送消息。
- Controller 能区分新任务、进度查询、澄清和修改。
- 修改会创建新 revision，不覆盖旧 Run。
- 用户可以查看和比较不同 revision。

### 可观测协作

- 前端显示真实 Agent 状态和 fan-out/join。
- 前端显示真实 Skill 调用。
- 前端显示 catalog、Skill 和 source version/hash。
- 前端显示 compile、runtime、verification evidence。
- SSE 断线后可以恢复当前状态。

### 可落地性

- 所有 Controller 输出经过 schema 校验。
- 底层 Orchestrator 的安全边界保持不变。
- 不允许通过对话绕过审批、验证或 hash 检查。
- 旧 Run 可恢复、可审计、可回滚。
- README 提供从安装到查看证据的完整路径。

## 12. 推荐的第一轮开发范围

建议第一轮只实现以下内容：

1. SSE 实时事件前端接入。
2. Agent Collaboration 面板。
3. Skill Timeline 和 Skill Inspector。
4. 顶部 Conversation MVP，只支持新任务、进度查询和简单澄清。
5. Conversation store 和基础 API。
6. README 第一版重写。

先不要在第一轮实现：

- 任意代码编辑器。
- 自动修改旧 Pipeline 文件。
- 无确认的生产执行。
- 复杂的跨 Run 自动复用。
- 直接引入大型状态管理框架。

## 13. 实现状态核对（2026-09-18）

本文件第 1–12 节是设计计划，不代表所有功能已完成。当前可运行实现与限制以
[README](../README.md#当前范围与限制) 和 [配置/API 参考](workbench-reference.md) 为准。

已提供规则式 Conversation Controller、本地 JSON 消息存储、Run SSE、基础 revision 新建、
Agent/Skill/Evidence 聚合接口、代码预览、阶段输出、新对话按钮和 Runs 独立滚动。
阶段播报只在前端对话框中显示，不写入持久化会话。

尚未完成独立顶层 Codex 对话代理、语义阶段摘要、复杂需求合并、跨 revision 增量复用、
完整 revision 对比、历史会话导航、Conversation SSE 游标恢复和前端自动化测试套件。
Vue Flow / Shiki 仍为候选，当前 Pipeline 源码仍采用通用运行器结构。

验证命令：

```bash
.venv/bin/python -m unittest discover -s tests -v
npm run build --prefix frontend
git diff --check
```

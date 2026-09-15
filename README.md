# DataFlow 多 Codex Agent Pipeline 编排器

将自然语言企业任务编译为可验证、可回滚的 DataFlow Pipeline。项目运行目录为 `/Users/blackbox/dataflow-mul-agents`，事实来源为 `/Users/blackbox/DataFlow`。

## 快速运行

```bash
cd /Users/blackbox/dataflow-mul-agents
python3.12 -m venv .venv  # DataFlow requires Python >=3.10
uv pip install --python .venv/bin/python -r requirements-local.txt
.venv/bin/python -m pip check

# 离线生成示例，不调用模型，不执行 pipeline
.venv/bin/python -m dataflow_agents.cli run "清洗 raw_content 的多余空格，按清洗内容精确去重，输出 cleaned_content" --backend offline

# 同步 DataFlow 的真实注册算子和源码签名
.venv/bin/python -m dataflow_agents.cli catalog
```

每次生成创建 `runs/run-*` 目录，保存请求快照、catalog 版本、planner/specialist/integrator 的输入输出、SQLite 状态、JSONL 事件、Trace、Metrics、静态校验和 Pipeline 源码。默认结束于 `READY`（已生成，未执行）；运行报告和输出数据仅在实际执行后产生。

## 依赖与本地环境

使用 Python 3.12 和 `uv`（`python3.12 -m pip install uv`）。默认 `requirements-local.txt` 在同一次解析中安装本项目的 Web/test extras 和 `../DataFlow` 的 editable 包，包含 DataFlow 的完整基础 requirements；不要使用 `--no-deps` 跳过依赖。`pyproject.toml` 明确依赖 `open-dataflow==1.0.10`（导入名为 `dataflow`），其依赖包含 rapidfuzz、NumPy、PyArrow、模型与云服务库。仅设置 `PYTHONPATH` 不算安装 DataFlow。

上游 DataFlow 的基础 requirements 本身包含完整运行依赖；`uv` 会在一次解析中解决版本组合并避免 pip 的长时间回溯。生产环境应在目标平台重新解析并保存自己的 lock/constraints，而不要把某台机器的包快照当作通用锁文件。DataFlow 源码目录由 `config/runtime.json` 的 `dataflow_root` 或 `DATAFLOW_ROOT` 指定；换目录时，同时修改 `requirements-local.txt` 的 editable 路径，让索引、预览和执行使用同一个 checkout。执行器默认复用启动后端的 Python 解释器，所以请通过 `.venv/bin/dataflow-agents-web` 启动。

验证安装：

```bash
.venv/bin/python -m pip check
.venv/bin/python -c "import dataflow, rapidfuzz; from dataflow.pipeline import PipelineABC; from dataflow.serving import APILLMServing_request; print(dataflow.__version__, dataflow.__file__)"
.venv/bin/python -m unittest discover -s tests -v
```

DataFlow 的 CUDA/vLLM、MinerU 等具名 extras、模型权重、外部 API 密钥仍按选用的算子配置；本地安装覆盖上游完整基础依赖，不会自动下载模型或调用外部服务。

## 真实 Codex

API key 只通过进程环境传递，不写入配置或证据。使用自定义 provider 时，Codex 官方配置支持用 `model_providers.<id>.env_key` 指定 bearer token 环境变量；本实现将它限制为 `DF_CODEX_API_KEY`，并将每个 Agent 放在独立的 ephemeral Codex 进程中。

```bash
export DF_CODEX_API_KEY='...'
export DF_CODEX_BASE_URL='https://api.zcloudapi.com/v1'
.venv/bin/python -m dataflow_agents.cli run "清洗 raw_content，按清洗结果去重并输出 cleaned_content" --backend codex
```

Codex 输出必须是符合 `dataflow_agents/contracts.py` JSON Schema 的最终消息；JSONL 事件会保存为证据，模型没有合法最终 JSON、超时或 schema 校验失败会重试一次，随后 `BLOCKED`。

## 生成、执行和回滚

默认只生成并静态检查 pipeline，不运行算子、不调用 pipeline 的模型服务，也不请求人工审批。Codex Agent 仍会调用模型来完成规划和代码生成。WebUI 中点击 `Run pipeline` 才执行已生成的 pipeline，这次点击就是执行确认，不再二次审批。保存 API resource 不会自动运行或恢复任务。`READY` 不表示经过运行验证，手动执行成功显示 `EXECUTED`。

非 Web 调用方可显式设置 `auto_execute: true` 启用旧的执行和 Verifier 流程，该模式仍对新算子和外部资源保留审批检查。历史审批、晋级和回滚命令仍可使用；只有通过 Verifier 的 `VERIFIED` 结果可以晋级：

```bash
.venv/bin/python -m dataflow_agents.cli approve runs/run-...
.venv/bin/python -m dataflow_agents.cli resume runs/run-...
.venv/bin/python -m dataflow_agents.cli promote runs/run-... --deployment deployments/production
.venv/bin/python -m dataflow_agents.cli rollback --deployment deployments/production
```

批准与 `pipeline.py`、spec、custom source、输入快照的 SHA-256 manifest 绑定，任何修改都会使批准失效。执行器是带资源继承过滤、超时和独立进程的本地控制平面，不等同于容器或内核沙箱；生产部署应把它放进容器、Job 或受控 runner。

## 设计和提交材料

- [docs/architecture.md](docs/architecture.md)：端到端 Codex workflow、状态机、RAG、MCP、观测和安全边界
- [docs/agent-identities.md](docs/agent-identities.md)：四个 Agent Identity 清单
- [docs/submission.md](docs/submission.md)：可直接提交的赛题逐项说明
- `.agents/skills/`：`pipeline-planning`、`operator-discovery`、`operator-scaffolding`、`schema-alignment`、`verification-evidence`
- `dataflow_agents/mcp_contract.py`：stdio MCP 协议实现和工具契约

## Web 工作台

WebUI 使用本项目的多 Codex Orchestrator 作为唯一 pipeline 生成后端，提供运行列表、实时 Agent 事件、pipeline DAG、手动执行和各阶段输出预览，不调用参考仓库中的旧单 Agent/MCP workflow。

```bash
uv pip install --python .venv/bin/python -r requirements-local.txt
cd frontend && npm install && npm run build && cd ..
DF_CODEX_API_KEY=... CODEX_BACKEND=codex .venv/bin/dataflow-agents-web
```

打开 <http://127.0.0.1:8000/>。开发模式可在 `frontend/` 运行 `npm run dev`，Vite 会将 `/api` 转发到 `127.0.0.1:8000`。`DF_CODEX_API_KEY` 只在后端进程环境中读取，不会进入前端或 run artifact。主要接口包括 `POST /api/v1/runs`、`GET /api/v1/runs/{run_id}`、`GET /api/v1/runs/{run_id}/stream`、`POST /api/v1/runs/{run_id}/execute`，以及真实 DataFlow 算子目录查询接口。

运行阶段默认超时为 900 秒（15 分钟），适合较复杂或较大数据量的 pipeline；可在 `config/runtime.json` 的 `runtime_timeout_seconds` 中调整。该限制只作用于用户手动启动的 DataFlow 执行子进程。

LLM/API 算子不要求在生成 pipeline 之前完成 resource 注册。Planner 和 compiler 会保留 `$resource` 占位符，先生成并校验 pipeline spec；手动执行时若 resource 尚未注册或密钥缺失，run 进入 `RESOURCE_REQUIRED`。可在 WebUI 的 API resources 面板注册后点击 `Run pipeline`，执行时会绑定最新配置，无需重新规划。resource 定义持久化在 `config/resources.json`，密钥从服务端环境或 secret registry 读取。

Serving 与算子绑定保持独立：pipeline 只保存稳定的 `$resource` 引用，`/api/v1/servings`（兼容别名 `/api/v1/resources`）负责维护 API 配置，`/api/v1/servings/classes` 返回 `APILLMServing_request` 的表单元数据。未被当前 pipeline 引用的 serving 不会阻塞本次执行。

WebUI 还提供 `/api/v1/models`（以及 serving/resources 别名）用于向 API 的 `/models` 或 `/model` 端点发现模型；API key 可在 serving 表单中填写，后端保存到被 `.gitignore` 排除且权限为 0600 的 secret registry，执行时才注入子进程环境。数据集可通过 `/api/v1/datasets` 注册、预览、切换和删除；`POST /api/v1/runs` 支持 `dataset_id`，会使用数据集样本和完整数据集生成输入快照。已有 pipeline 可通过 `POST /api/v1/runs/{run_id}/execute` 直接执行，`GET /api/v1/runs/{run_id}/stages` 展示每个 DataFlow cache 阶段的真实输出。

### Multi-Turn Conversation Workbench

工作台顶部的 **Conversation Controller** 支持在同一会话中连续提出新任务、查询进度、查看证据和修改需求。Controller 只负责结构化路由，实际规划、算子绑定、Pipeline 拼装和验证仍由四个执行 Agent 完成；修改需求会创建新的 revision/Run，旧 Run 保留用于审计和对比。右侧 Agent activity 通过 Run SSE 实时更新，展示 Agent、Skill、工具调用和验证事件。

会话 API：

```text
POST /api/v1/conversations
GET  /api/v1/conversations/{conversation_id}
POST /api/v1/conversations/{conversation_id}/messages
GET  /api/v1/conversations/{conversation_id}/stream
```

详细设计和后续增量复用路线见 [docs/multi_turn_agent_workbench_plan.md](docs/multi_turn_agent_workbench_plan.md)。

代码预览入口为 **View Pipeline / Operator code**。Pipeline 标签显示当前 run 的完整 `pipeline.py`；Operator 标签按最终 `pipeline-spec.json` 的步骤选择算子，已有算子读取 DataFlow 源文件，自定义算子读取 run 下的 `custom/*.py`。预览不会执行源码，文件缺失或与生成时哈希不一致会明确提示。接口为 `GET /api/v1/runs/{run_id}/pipeline-code`，保留原有 `code` 字段，并返回含 step id、文件名、来源、完整代码和状态的 `operators` 数组。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
```

测试覆盖真实 DataFlow compile/execute、完整字段契约、DAG 循环、Codex JSONL 解析、MCP schema、经验记忆、失败恢复、自定义 operator fixture、审批哈希、发布和回滚。

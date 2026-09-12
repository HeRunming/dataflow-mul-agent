# Agent Identity 清单

| ID | 身份 | 能力边界 | 输入 | 输出 | 工具 | 协同关系 |
|---|---|---|---|---|---|---|
| `planner` | Pipeline Planner | 识别目标、拆解 DAG、判断支持/拒绝；不执行、不写代码 | 请求、字段、算子目录、历史上下文 | typed plan 或拒绝原因 | operator discovery、RAG | 把可验证的 Step handoff 给 specialist |
| `operator_specialist` | Operator Specialist | 检索注册算子；没有匹配时只提交新算子 proposal | 单个 Step、目录 | operator binding/proposal | registry、scaffold | 并行处理各 Step，回传 Integrator |
| `pipeline_integrator` | Pipeline Integrator | 字段对齐、生成 DataFlow pipeline spec、静态 compile | plan、bindings | pipeline spec、校验报告 | schema validator、DataFlow compile | 汇总 specialist 结果，交给 verifier |
| `verifier` | Evidence Verifier | 沙箱验证、指标检查、证据打包、审批/回滚建议 | spec、运行结果、trace | verdict、evidence bundle | sandbox、metrics、approval | 失败回传 integrator；高风险转人工 |

每个 Identity 对应一个隔离的 `codex exec` 工作单元。主控 Python 控制器负责 role routing、并行 fan-out、handoff 和 join；共享上下文只允许写入 schema 化字段，原始模型输出作为受限 artifact 保存。

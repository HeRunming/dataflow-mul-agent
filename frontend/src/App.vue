<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

const runs = ref([]), selectedId = ref(''), selected = ref(null), events = ref([]), agentOutputs = ref([])
const datasets = ref([]), selectedDatasetId = ref(''), datasetPreview = ref([])
const request = ref('清理文本中的多余空格并去重')
const inputText = ref('[{"raw_content":"  Hello   world  "},{"raw_content":"Hello world"}]')
const allowCustom = ref(true), busy = ref(false), error = ref(''), backendMode = ref('unknown')
const resources = ref([]), showResource = ref(false), models = ref([]), modelBusy = ref(false)
const resource = ref({ name: 'llm_default', api_url: 'https://api.zcloudapi.com/v1', model_name: 'gpt-5.5', api_key: '', key_name_of_api_key: '', max_workers: 2, max_tokens: 1024, temperature: 0.2 })
const showDataset = ref(false), dataset = ref({ name: '', rows: '[{"raw_content":"example"}]' })
const stages = ref([]), selectedStage = ref(null)
const showCode = ref(false), pipelineCode = ref(''), operatorCodes = ref([]), activeCodeTab = ref('pipeline'), selectedOperatorId = ref(''), codeRunId = ref(''), codeBusy = ref(false)
let timer
const stateOrder = ['PLANNING', 'BINDING', 'INTEGRATING', 'READY']
const agents = [['planner', 'Planner', '拆解需求'], ['operator_specialist', 'Specialists', '选择算子'], ['pipeline_integrator', 'Integrator', '字段对齐'], ['verifier', 'Verifier', '验证证据']]
const currentState = computed(() => selected.value?.state || 'IDLE')
const steps = computed(() => selected.value?.pipeline?.steps || [])
const stateIndex = computed(() => stateOrder.indexOf(currentState.value))
const selectedRows = computed(() => selectedStage.value?.rows || [])
const selectedFields = computed(() => selectedStage.value?.fields || (selectedRows.value[0] ? Object.keys(selectedRows.value[0]) : []))
async function api(path, options) { const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options }); if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || response.statusText); return response.json() }
async function loadResources() { resources.value = (await api('/api/v1/resources')).resources }
async function loadDatasets() { datasets.value = (await api('/api/v1/datasets')).datasets; if (!selectedDatasetId.value && datasets.value.length) await selectDataset(datasets.value[0].id) }
async function selectDataset(id) { selectedDatasetId.value = id; if (!id) return; const data = await api(`/api/v1/datasets/${id}/preview`); datasetPreview.value = data.rows || []; if (datasetPreview.value.length) inputText.value = JSON.stringify(datasetPreview.value, null, 2) }
async function registerDataset() { try { const rows = JSON.parse(dataset.value.rows); const created = await api('/api/v1/datasets', { method: 'POST', body: JSON.stringify({ name: dataset.value.name, rows }) }); await loadDatasets(); await selectDataset(created.id); showDataset.value = false } catch (err) { error.value = err.message } }
async function deleteDataset(id) { try { await api(`/api/v1/datasets/${id}`, { method: 'DELETE' }); if (selectedDatasetId.value === id) selectedDatasetId.value = ''; await loadDatasets() } catch (err) { error.value = err.message } }
async function discoverModels() { modelBusy.value = true; try { models.value = (await api('/api/v1/models', { method: 'POST', body: JSON.stringify({ api_url: resource.value.api_url, api_key: resource.value.api_key }) })).models } catch (err) { error.value = err.message } finally { modelBusy.value = false } }
async function registerResource() { try { await api('/api/v1/resources', { method: 'POST', body: JSON.stringify(resource.value) }); await loadResources(); showResource.value = false; resource.value.api_key = '' } catch (err) { error.value = err.message } }
async function refreshRuns() { runs.value = await api('/api/v1/runs'); if (!selectedId.value && runs.value.length) selectedId.value = runs.value[0].run_id; if (selectedId.value) await selectRun(selectedId.value, false) }
async function selectRun(id, reset = true) {
  const previousStageId = selectedId.value === id ? selectedStage.value?.stage_id : null
  selectedId.value = id
  if (reset) events.value = []
  selected.value = await api(`/api/v1/runs/${id}`)
  events.value = await api(`/api/v1/runs/${id}/events`)
  agentOutputs.value = await api(`/api/v1/runs/${id}/agent-outputs`)
  const data = await api(`/api/v1/runs/${id}/stages`)
  stages.value = data.stages || []
  selectedStage.value = stages.value.find(stage => stage.stage_id === previousStageId) || stages.value.at(-1) || null
}
async function createRun() { busy.value = true; error.value = ''; try { const inputRows = selectedDatasetId.value ? undefined : JSON.parse(inputText.value); const created = await api('/api/v1/runs', { method: 'POST', body: JSON.stringify({ request: request.value, input_rows: inputRows, dataset_id: selectedDatasetId.value || undefined, allow_custom: allowCustom.value }) }); await selectRun(created.run_id); await refreshRuns() } catch (err) { error.value = err.message } finally { busy.value = false } }
async function executePipeline() { busy.value = true; try { await api(`/api/v1/runs/${selectedId.value}/execute`, { method: 'POST' }); await tick() } catch (err) { error.value = err.message } finally { busy.value = false } }
async function viewPipelineCode() {
  const runId = selectedId.value
  codeBusy.value = true
  try {
    const data = await api(`/api/v1/runs/${runId}/pipeline-code`)
    if (runId !== selectedId.value) return
    pipelineCode.value = data.code || ''
    operatorCodes.value = data.operators || []
    selectedOperatorId.value = operatorCodes.value[0]?.id || ''
    codeRunId.value = runId
    activeCodeTab.value = 'pipeline'
    showCode.value = true
  } catch (err) { error.value = err.message } finally { codeBusy.value = false }
}
const activeOperator = computed(() => operatorCodes.value.find(item => item.id === selectedOperatorId.value))
const activeCode = computed(() => activeCodeTab.value === 'pipeline' ? pipelineCode.value : (activeOperator.value?.code || ''))
async function deleteRun(id) { try { await api(`/api/v1/runs/${id}`, { method: 'DELETE' }); if (selectedId.value === id) { selectedId.value = ''; selected.value = null }; await refreshRuns() } catch (err) { error.value = err.message } }
async function tick() { if (!selectedId.value) return; try { await selectRun(selectedId.value, false); await refreshRuns() } catch (err) { error.value = err.message } }
function labelState(state) { return ({ READY: '已生成，未执行', RUNNING: '执行中', PLANNING: '规划中', BINDING: '算子绑定', INTEGRATING: '拼装中', VALIDATING: '验证中', VERIFIED: '已验证', EXECUTED: '已执行', APPROVAL_REQUIRED: '待手动运行', RESOURCE_REQUIRED: '等待 API resource', REFUSED: '已拒绝', BLOCKED: '已阻断', QUEUED: '排队中' })[state] || state }
function eventLabel(event) { return ({ 'workflow.state': '流程状态', 'agent.started': 'Agent 开始', 'agent.completed': 'Agent 完成', 'agent.failed': 'Agent 失败', 'tool.called': '工具调用', 'skill.invoked': 'Skill 调用', 'workflow.repair': '修复尝试', 'approval.granted': '审批通过' })[event] || event }
function operatorName(step) { return step.operator || step.operator_name || '未绑定' }
onMounted(async () => { try { backendMode.value = (await api('/api/v1/health')).backend; await Promise.all([loadResources(), loadDatasets(), refreshRuns()]) } catch (err) { error.value = err.message }; timer = setInterval(tick, 1800) })
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="app-shell"><header class="topbar"><div class="brand"><span class="brand-mark">DF</span><div><strong>DataFlow</strong><small>Multi-Codex Studio</small></div></div><div class="top-actions"><span class="connection"><i></i>{{ backendMode }} backend</span><button class="ghost" @click="showDataset = true">Datasets ({{ datasets.length }})</button><button class="ghost" @click="showResource = true">Serving / API ({{ resources.length }})</button><button class="ghost" @click="refreshRuns">Refresh</button></div></header>
    <main class="workspace"><aside class="sidebar panel"><div class="panel-title"><span>Runs</span><span class="count">{{ runs.length }}</span></div><button v-for="run in runs" :key="run.run_id" class="run-item" :class="{ active: run.run_id === selectedId }" @click="selectRun(run.run_id)"><span class="run-dot" :class="run.state.toLowerCase()"></span><span class="run-copy"><b>{{ run.request || 'Untitled run' }}</b><small>{{ run.run_id }} · {{ labelState(run.state) }}</small></span><span class="run-delete" title="Delete run" @click.stop="deleteRun(run.run_id)">×</span></button><div v-if="!runs.length" class="empty">No runs yet</div></aside>
      <section class="center"><div class="hero panel"><div><span class="eyebrow">MULTI-AGENT PIPELINE BUILDER</span><h1>Turn intent into a verified DataFlow pipeline.</h1><p>Planner, operator specialists, integrator and verifier collaborate through one durable run.</p></div><div class="agent-strip"><span v-for="agent in agents" :key="agent[0]" class="agent-chip"><b>{{ agent[1][0] }}</b>{{ agent[1] }}</span></div></div>
        <button v-if="selected?.pipeline" class="code-launch ghost" :disabled="codeBusy" @click="viewPipelineCode">{{ codeBusy ? 'Loading code...' : 'View Pipeline / Operator code' }}</button><div class="composer panel"><label>Describe your data task</label><textarea v-model="request" rows="2" placeholder="Describe a transformation..."></textarea><div class="dataset-row"><select v-model="selectedDatasetId" @change="selectDataset(selectedDatasetId)"><option value="">Inline JSON input</option><option v-for="item in datasets" :key="item.id" :value="item.id">{{ item.name }} ({{ item.rows }} rows)</option></select><button class="ghost" @click="showDataset = true">Register dataset</button><span v-if="selectedDatasetId" class="dataset-hint">Using first row as schema</span></div><textarea v-if="!selectedDatasetId" class="input-json" v-model="inputText" rows="3" placeholder="Input JSON rows"></textarea><div class="composer-bottom"><label class="toggle"><input type="checkbox" v-model="allowCustom" /> Allow new operators</label><button class="primary" :disabled="busy" @click="createRun">{{ busy ? 'Starting...' : 'Generate pipeline' }} <span>→</span></button></div></div>
        <div class="pipeline panel"><div class="section-head"><div><span class="eyebrow">PIPELINE GRAPH</span><h2>{{ selected?.request || 'Select a run to inspect its pipeline' }}</h2></div><div class="section-actions"><span v-if="selected" class="state-pill" :class="currentState.toLowerCase()">{{ labelState(currentState) }}</span><button v-if="selected?.pipeline && ['READY','VERIFIED','EXECUTED','APPROVAL_REQUIRED','RESOURCE_REQUIRED','BLOCKED'].includes(currentState)" class="ghost" :disabled="busy" @click="executePipeline">Run pipeline</button></div></div><div v-if="selected" class="progress"><span v-for="state in stateOrder" :key="state" :class="{ done: stateIndex >= stateOrder.indexOf(state), current: state === currentState }">{{ labelState(state) }}</span></div><div v-if="selected?.summary || selected?.latest_event" class="summary"><b>当前阶段</b><span>{{ selected.summary || `${eventLabel(selected.latest_event.event)} · ${selected.latest_event.agent}` }}</span></div><div v-if="selected?.reason" class="reason"><b>{{ currentState === 'REFUSED' ? 'Planner 拒绝原因' : currentState === 'RESOURCE_REQUIRED' ? 'API resource 配置' : '运行摘要' }}</b><span>{{ selected.reason }}</span><button v-if="currentState === 'RESOURCE_REQUIRED'" class="primary inline-action" @click="showResource = true">Configure resource</button></div><div v-if="steps.length" class="dag"><template v-for="(step, index) in steps" :key="step.step_id"><div class="dag-node"><span class="node-index">{{ String(index + 1).padStart(2, '0') }}</span><div><b>{{ operatorName(step) }}</b><small>{{ step.objective || step.step_id }}</small></div><span class="node-status">{{ step.risk || 'low' }}</span></div><div v-if="index < steps.length - 1" class="dag-link">↓</div></template></div><div v-else class="graph-empty">The integrator will render operator bindings here.</div></div>
        <div v-if="stages.length" class="panel stage-panel"><div class="panel-title"><span>Stage output review</span><span class="count">{{ stages.length }} stages</span></div><div class="stage-tabs"><button v-for="stage in stages" :key="stage.stage_id" :class="{ active: selectedStage?.stage_id === stage.stage_id }" @click="selectedStage = stage">{{ stage.index + 1 }} · {{ stage.name }} <small>{{ stage.row_count }} rows</small></button></div><div v-if="selectedStage" class="stage-meta"><b>{{ selectedStage.source }}</b><span>{{ selectedStage.fields.join(', ') }}</span></div><div class="table-scroll"><table><thead><tr><th v-for="field in selectedFields" :key="field">{{ field }}</th></tr></thead><tbody><tr v-for="(row, i) in selectedRows" :key="i"><td v-for="field in selectedFields" :key="field">{{ typeof row[field] === 'object' ? JSON.stringify(row[field]) : row[field] }}</td></tr></tbody></table><div v-if="!selectedRows.length" class="empty">No materialized rows yet.</div></div></div></section>
      <aside class="rightbar"><div class="panel side-panel"><div class="panel-title"><span>Agent activity</span><span class="live"><i></i> Live</span></div><div class="timeline"><div v-for="event in events.slice(-14).reverse()" :key="event.seq" class="timeline-item"><span class="timeline-line"></span><span class="timeline-dot"></span><div><b>{{ eventLabel(event.event) }}</b><small>{{ event.agent }}<span v-if="event.job"> · {{ event.job }}</span></small></div></div><div v-if="!events.length" class="empty">Events will appear as agents work.</div></div></div><div v-if="agentOutputs.length" class="panel side-panel"><div class="panel-title">Codex latest output</div><div class="codex-output"><div v-for="output in agentOutputs" :key="output.agent + output.attempt"><b>{{ output.agent }}</b><pre>{{ output.text }}</pre></div></div></div></aside></main>
    <div v-if="showResource" class="modal-backdrop"><div class="resource-modal panel"><div class="panel-title">Register LLM API resource <button class="ghost" @click="showResource = false">Close</button></div><input v-model="resource.name" placeholder="Resource name" /><input v-model="resource.api_url" placeholder="HTTP / HTTPS API endpoint" /><div class="inline-form"><input v-model="resource.api_key" type="password" placeholder="API key (stored backend-side)" /><button class="ghost" :disabled="modelBusy" @click="discoverModels">{{ modelBusy ? 'Loading...' : 'Load /models' }}</button></div><select v-model="resource.model_name"><option v-if="!models.length" value="">Model name</option><option v-for="model in models" :key="model.id" :value="model.id">{{ model.id }}</option></select><div class="serving-options"><label>并发度 <input v-model.number="resource.max_workers" type="number" min="1" max="128" /></label><label>最大输出 tokens <input v-model.number="resource.max_tokens" type="number" min="1" max="32768" /></label><label>Temperature <input v-model.number="resource.temperature" type="number" min="0" max="2" step="0.1" /></label></div><input v-model="resource.key_name_of_api_key" placeholder="Optional DF_PIPELINE_* env var" /><small>并发度控制 DataFlow serving 的请求线程数；最大输出 tokens 会传给兼容 OpenAI 的 API。</small><small>API key is written only to the backend secret registry and never to pipeline artifacts.</small><button class="primary" @click="registerResource">Register resource</button><div v-if="resources.length" class="resource-list"><b>Configured</b><span v-for="item in resources" :key="item.name">{{ item.name }} · {{ item.model_name }} · {{ item.max_workers || '—' }} workers · {{ item.max_tokens || '—' }} tokens · {{ item.configured ? 'ready' : 'credential missing' }}</span></div></div></div>
    <div v-if="showDataset" class="modal-backdrop"><div class="resource-modal panel"><div class="panel-title">Register dataset <button class="ghost" @click="showDataset = false">Close</button></div><input v-model="dataset.name" placeholder="Dataset name" /><textarea v-model="dataset.rows" rows="7" placeholder="JSON array of objects"></textarea><button class="primary" @click="registerDataset">Register dataset</button><div v-if="datasets.length" class="resource-list"><b>Registered datasets</b><span v-for="item in datasets" :key="item.id"><button class="link-button" @click="selectDataset(item.id)">{{ item.name }} · {{ item.rows }} rows</button><button class="delete-button" @click="deleteDataset(item.id)">Delete</button></span></div></div></div><div v-if="error" class="toast">{{ error }} <button @click="error = ''">Dismiss</button></div>
  </div>
<div v-if="showCode" class="modal-backdrop" @click.self="showCode = false" @keydown.esc="showCode = false">
  <div class="code-modal panel" role="dialog" aria-modal="true" aria-labelledby="code-title">
    <div class="panel-title"><span id="code-title">Generated code · {{ codeRunId }}</span><button class="ghost" @click="showCode = false">Close</button></div>
    <div class="code-tabs">
      <button :class="{ active: activeCodeTab === 'pipeline' }" :aria-pressed="activeCodeTab === 'pipeline'" @click="activeCodeTab = 'pipeline'">Pipeline</button>
      <button :class="{ active: activeCodeTab === 'operators' }" :aria-pressed="activeCodeTab === 'operators'" @click="activeCodeTab = 'operators'">Operator ({{ operatorCodes.length }})</button>
    </div>
    <label v-if="activeCodeTab === 'operators' && operatorCodes.length" class="operator-select">Select operator
      <select v-model="selectedOperatorId"><option v-for="item in operatorCodes" :key="item.id" :value="item.id">{{ item.id }} · {{ item.name }} · {{ item.kind }}</option></select>
    </label>
    <div class="code-filename">{{ activeCodeTab === 'pipeline' ? 'pipeline.py' : activeOperator?.filename }}</div>
    <p v-if="activeCodeTab === 'operators' && !operatorCodes.length" class="empty">No operator sources are available for this run.</p>
    <p v-else-if="activeCodeTab === 'operators' && activeOperator?.error" class="code-notice" role="alert">{{ activeOperator.error }}</p>
    <template v-else>
      <p v-if="activeCodeTab === 'operators' && activeOperator?.changed" class="code-notice">Source changed since generation. Showing the current file.</p>
      <pre class="pipeline-code" tabindex="0"><code>{{ activeCode }}</code></pre>
    </template>
  </div>
</div>
</template>

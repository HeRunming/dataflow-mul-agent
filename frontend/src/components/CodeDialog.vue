<script setup>
import { computed, ref, watch } from 'vue'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import BaseDialog from './BaseDialog.vue'
import { api } from '../api'
import { useWorkbench } from '../composables/useWorkbench'

hljs.registerLanguage('python', python)

const props = defineProps({ runId: String })
const emit = defineEmits(['close'])
const { notify } = useWorkbench()

const loading = ref(true)
const payload = ref(null)
const tab = ref('pipeline')
const operatorId = ref('')
const copied = ref(false)

const operators = computed(() => payload.value?.operators || [])
const activeOperator = computed(() => operators.value.find((item) => item.id === operatorId.value))

const current = computed(() => {
  if (tab.value === 'pipeline') return { filename: payload.value?.filename || 'pipeline.py', code: payload.value?.code || '', note: '' }
  if (tab.value === 'runner') {
    return {
      filename: payload.value?.runner?.filename || 'run_pipeline.py',
      code: payload.value?.runner?.code || '',
      note: '工作台执行器：负责 fixtures、运行报告与输出投影，pipeline.py 保持原生写法。',
    }
  }
  const operator = activeOperator.value
  return {
    filename: operator?.filename || '',
    code: operator?.code || '',
    note: operator?.error || (operator?.changed ? '源码在生成之后发生过修改，这里显示当前文件。' : ''),
    tone: operator?.error ? 'danger' : 'warn',
  }
})

const highlighted = computed(() => {
  const code = current.value.code
  if (!code) return ''
  try {
    return hljs.highlight(code, { language: 'python' }).value
  } catch {
    return code.replace(/[&<>]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[char]))
  }
})

const lineCount = computed(() => (current.value.code ? current.value.code.split('\n').length : 0))

async function load() {
  loading.value = true
  try {
    payload.value = await api.pipelineCode(props.runId)
    operatorId.value = payload.value.operators?.[0]?.id || ''
  } catch (error) {
    notify(error.message)
    emit('close')
  } finally {
    loading.value = false
  }
}

async function copy() {
  try {
    await navigator.clipboard.writeText(current.value.code)
    copied.value = true
    setTimeout(() => (copied.value = false), 1600)
  } catch {
    notify('浏览器拒绝了剪贴板访问')
  }
}

function download() {
  const blob = new Blob([current.value.code], { type: 'text/x-python' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = current.value.filename.split('/').pop() || 'pipeline.py'
  link.click()
  URL.revokeObjectURL(url)
}

watch(() => props.runId, load, { immediate: true })
</script>

<template>
  <BaseDialog wide :title="`生成代码 · ${runId}`" @close="emit('close')">
    <template #actions>
      <button class="btn small" :disabled="!current.code" @click="copy">{{ copied ? '已复制' : '复制' }}</button>
      <button class="btn small" :disabled="!current.code" @click="download">下载</button>
    </template>

    <nav class="tabs">
      <button :class="{ active: tab === 'pipeline' }" @click="tab = 'pipeline'">pipeline.py</button>
      <button :class="{ active: tab === 'runner' }" @click="tab = 'runner'">run_pipeline.py</button>
      <button :class="{ active: tab === 'operators' }" @click="tab = 'operators'">
        算子源码 · {{ operators.length }}
      </button>
    </nav>

    <div v-if="tab === 'operators' && operators.length" class="picker">
      <select v-model="operatorId" class="select">
        <option v-for="item in operators" :key="item.id" :value="item.id">
          {{ item.id }} · {{ item.name }} · {{ item.kind === 'custom' ? '本次生成' : 'DataFlow 内置' }}
        </option>
      </select>
    </div>

    <div class="file-bar">
      <span class="mono truncate">{{ current.filename || '—' }}</span>
      <span class="spacer" />
      <span v-if="lineCount" class="tag">{{ lineCount }} 行</span>
    </div>

    <p v-if="current.note" class="note" :class="current.tone || 'info'">{{ current.note }}</p>

    <div class="code scroll" tabindex="0">
      <div v-if="loading" class="skeleton code-skeleton" />
      <div v-else-if="!current.code" class="empty">没有可显示的源码。</div>
      <pre v-else><code class="hljs" v-html="highlighted" /></pre>
    </div>
  </BaseDialog>
</template>

<style scoped>
.tabs { display: flex; gap: 4px; padding: 10px 16px 0; }
.tabs button {
  padding: 6px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
  background: transparent;
  color: var(--text-3);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.tabs button:hover { color: var(--text-2); background: var(--surface-2); }
.tabs button.active { color: var(--brand); background: var(--brand-soft); border-color: color-mix(in srgb, var(--brand) 28%, transparent); }
.picker { padding: 10px 16px 0; }
.file-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 16px 0;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  background: var(--surface-3);
  font-size: 11px;
  color: var(--text-2);
}
.spacer { margin-left: auto; }
.note {
  margin: 8px 16px 0;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  font-size: 11.5px;
  background: var(--warn-soft);
  color: var(--warn);
}
.note.danger { background: var(--danger-soft); color: var(--danger); }
.note.info { background: var(--info-soft); color: var(--info); }
.code { flex: 1; min-height: 0; margin: 10px 16px 16px; border-radius: var(--radius); background: var(--code-bg); border: 1px solid var(--border); }
.code pre { margin: 0; padding: 14px 16px; }
.code code { font-family: var(--font-mono); font-size: 11.5px; line-height: 1.65; white-space: pre; }
.code-skeleton { height: 320px; margin: 14px; }
</style>
